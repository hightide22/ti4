import unittest
import random
from unittest.mock import patch

from board import ROOT, load_board
from action_card_deck import ActionCardDeck
from movement import MovementController, MovementError, Session, Snapshot, capital_ship
from main_menu import MainMenu
from player import create_players
from strategic_action import StrategyController, StrategyResolution
from transactions import TransactionController, TransactionError
from turn_order import TurnOrder
from technology import production_allowed
from units import Region, Unit, UnitLocation, mobile_ship


class SetupTests(unittest.TestCase):
    def test_action_card_deck_contains_base_cards_and_exhausts_cleanly(self):
        deck = ActionCardDeck(random.Random(3))
        available = len(deck.cards)
        drawn = [deck.draw() for _ in range(available)]
        self.assertGreater(available, 0)
        self.assertEqual(len(drawn), len(set(drawn)))
        self.assertTrue(all(drawn))
        self.assertIsNone(deck.draw())

    def test_menu_cycles_factions_without_creating_duplicates(self):
        menu = MainMenu()
        menu.player_count = 4
        menu.select_faction(0, 1)
        self.assertEqual(menu.factions[:4], ['jolnar', 'sol', 'hacan', 'letnev'])
        self.assertEqual(len(set(menu.active_factions)), 4)

    def test_three_player_faction_selection_keeps_fourth_slot_unique(self):
        menu = MainMenu()
        menu.select_faction(2, -1)
        self.assertEqual(menu.active_factions, ('sol', 'jolnar', 'letnev'))
        menu.player_count = 4
        self.assertEqual(len(set(menu.active_factions)), 4)

    def test_four_player_map_has_four_distinct_home_systems_and_37_tiles(self):
        config, board = load_board(ROOT / 'maps/four_player.json')
        players = create_players(board, config)
        self.assertEqual(len(board), 37)
        self.assertEqual([player.faction for player in players], ['sol', 'jolnar', 'hacan', 'letnev'])
        self.assertEqual(len({tile.position for tile in board.values()}), 37)
        self.assertEqual({tile.number for tile in board.home_tiles}, {1, 10, 12, 16})

    def test_menu_faction_choices_are_applied_in_player_slot_order(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('hacan', 'sol', 'letnev', 'jolnar'))
        players = create_players(board, config)
        self.assertEqual([player.faction for player in players], ['hacan', 'sol', 'letnev', 'jolnar'])
        self.assertEqual([player.color_code for player in players], ['gld', 'blu', 'blk', 'ppl'])
        self.assertEqual({tile.number for tile in board.home_tiles}, {1, 10, 12, 16})

    def test_menu_rejects_duplicate_or_wrong_number_of_factions(self):
        map_path = ROOT / 'maps/four_player.json'
        with self.assertRaisesRegex(ValueError, 'different faction'):
            load_board(map_path, ('sol', 'sol', 'hacan', 'letnev'))
        with self.assertRaisesRegex(ValueError, 'exactly 4'):
            load_board(map_path, ('sol', 'jolnar', 'hacan'))

    def test_new_base_factions_setup_with_their_faction_homeworlds(self):
        for faction in ('arborec', 'saar', 'muaat', 'l1z1x', 'ghost'):
            with self.subTest(faction=faction):
                fillers = [alias for alias in ('sol', 'jolnar', 'hacan', 'letnev')]
                factions = [faction, *fillers[:3]]
                config, board = load_board(ROOT / 'maps/four_player.json', factions)
                players = create_players(board, config)
                player = next(player for player in players if player.faction == faction)
                self.assertTrue(any(planet.faction_homeworld == faction for tile in board.values()
                                    for planet in tile.planets))
                self.assertTrue(any(unit.owner == faction for tile in board.values()
                                    for unit in tile.units))
                if faction == 'ghost':
                    self.assertIn(51, {tile.number for tile in board.values()})
                    self.assertIn(17, {tile.number for tile in board.values()})
                if faction == 'saar':
                    dock = next(unit for tile in board.values() for unit in tile.units
                                if unit.owner == faction and unit.kind == 'spacedock')
                    self.assertEqual(dock.location.region, Region.SPACE)
                    self.assertTrue(mobile_ship(dock))
                    self.assertFalse(capital_ship(dock))

    def test_ghost_home_system_routes_to_the_galaxy_through_the_gate(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('ghost', 'sol', 'hacan', 'letnev'))
        players = create_players(board, config)
        ghost = next(player for player in players if player.faction == 'ghost')
        movement = MovementController(board, players)
        home = next(tile for tile in board.values() if tile.number == 51)
        gate = next(tile for tile in board.values() if tile.number == 17)
        empty_system = next(tile for tile in board.values() if tile.number == 48)
        carrier = next(unit for unit in home.units
                       if unit.owner == 'ghost' and unit.kind == 'carrier')

        # The only traversable geometric neighbor of tile 51 is the Creuss
        # Gate; the other touching hex is a supernova. The route to a system
        # beside the Gate must therefore visibly pass through the Gate.
        neighbors = board.neighbors(home.position)
        self.assertIn(gate, neighbors)
        self.assertEqual({tile.number for tile in neighbors if 'supernova' not in tile.anomalies},
                         {17})
        self.assertEqual(movement.route(home, empty_system, carrier, ghost),
                         (home.position, gate.position, empty_system.position))

    def test_arborec_infantry_is_only_producible_from_letani(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('arborec', 'sol', 'jolnar', 'hacan'))
        arborec = next(player for player in create_players(board, config)
                       if player.faction == 'arborec')
        self.assertTrue(production_allowed(arborec, 'infantry'))


class FactionAbilityTests(unittest.TestCase):
    def test_arborec_mitosis_places_infantry_on_a_controlled_planet(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('arborec', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        arborec = next(player for player in players if player.faction == 'arborec')
        movement = MovementController(board, players)
        planet = arborec.planets[0].planet
        before = sum(unit.owner == 'arborec' and unit.kind == 'infantry' and
                     unit.location.planet_id == planet.planet_id
                     for tile in board.values() for unit in tile.units)

        movement.mitosis(arborec, planet.planet_id)

        self.assertEqual(sum(unit.owner == 'arborec' and unit.kind == 'infantry' and
                             unit.location.planet_id == planet.planet_id
                             for tile in board.values() for unit in tile.units), before + 1)

    def test_l1z1x_harrow_bombards_after_each_ground_combat_round(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('l1z1x', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        l1z1x = next(player for player in players if player.faction == 'l1z1x')
        sol = next(player for player in players if player.faction == 'sol')
        target = next(tile for tile in board.values() if tile.planets and not tile.units)
        planet = target.planets[0]
        target.units.extend((
            Unit('harrow-dread', 'dreadnought', 'l1z1x', l1z1x.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('harrow-attacker', 'infantry', 'l1z1x', l1z1x.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
            Unit('harrow-defender-1', 'infantry', 'sol', sol.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
            Unit('harrow-defender-2', 'infantry', 'sol', sol.color_code,
                 UnitLocation(Region.PLANET, planet.planet_id)),
        ))
        movement = MovementController(board, players)
        session = Session(l1z1x, target, {}, Snapshot.capture(board, l1z1x, players),
                          combat_type='ground', combat_planet_id=planet.planet_id,
                          combat_factions=('l1z1x', 'sol'), combat_round=1)
        movement.session = session
        before = sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                     unit.location.planet_id == planet.planet_id for unit in target.units)

        with patch('movement.random.randint', return_value=10):
            movement._finish_combat_round(session)

        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                             unit.location.planet_id == planet.planet_id for unit in target.units), before - 1)

    def test_muaat_star_forge_spends_strategy_token_and_is_reversible(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('muaat', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        muaat = next(player for player in players if player.faction == 'muaat')
        movement = MovementController(board, players)
        home = next(tile for tile in board.values()
                    if any(unit.owner == 'muaat' and unit.kind == 'warsun'
                           and unit.location.region == Region.SPACE for unit in tile.units))
        before_tokens = muaat.command_pools['strategic']
        before_home_fighters = sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                                   for unit in home.units)
        before_fighters = sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                              for tile in board.values() for unit in tile.units)

        movement.star_forge(muaat, home.position, 'fighter')

        self.assertEqual(muaat.command_pools['strategic'], before_tokens - 1)
        self.assertEqual(sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                             for unit in home.units), before_home_fighters + 2)
        self.assertTrue(movement.undo())
        self.assertEqual(muaat.command_pools['strategic'], before_tokens)
        self.assertEqual(sum(unit.owner == 'muaat' and unit.kind == 'fighter'
                             for tile in board.values() for unit in tile.units), before_fighters)

    def test_sol_versatile_grants_one_extra_command_each_round(self):
        config, board = load_board()
        players = create_players(board, config)
        by_faction = {player.faction: player for player in players}
        self.assertEqual(by_faction['sol'].round_command_gain(), 3)
        self.assertEqual(by_faction['hacan'].round_command_gain(), 2)

    def test_sol_orbital_drop_spends_strategy_token_and_can_be_undone(self):
        config, board = load_board()
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        movement = MovementController(board, players)
        home = next(tile for tile in board.values() if tile.planet_owners.get('jord') == 'sol')
        before_units = list(home.units)
        before_pool = sol.command_pools['strategic']

        movement.orbital_drop(sol, 'jord')

        self.assertEqual(sol.command_pools['strategic'], before_pool - 1)
        self.assertEqual(sum(unit.owner == 'sol' and unit.kind == 'infantry' and
                             unit.location.planet_id == 'jord' for unit in home.units), 7)
        self.assertTrue(movement.undo())
        self.assertEqual(home.units, before_units)
        self.assertEqual(sol.command_pools['strategic'], before_pool)

    def test_letnev_armada_adds_two_to_system_fleet_limit(self):
        config, board = load_board(ROOT / 'maps/four_player.json')
        players = create_players(board, config)
        letnev = next(player for player in players if player.faction == 'letnev')
        self.assertEqual(MovementController.fleet_supply(letnev), letnev.command_pools['fleet'] + 2)
        letnev.command_pools['fleet'] = 5
        self.assertEqual(MovementController.fleet_supply(letnev), 7)

    def test_jol_nar_fragile_shifts_its_combat_target_by_one(self):
        self.assertEqual(MovementController.combat_threshold('jolnar', {'combatHitsOn': 5}), 6)
        self.assertEqual(MovementController.combat_threshold('sol', {'combatHitsOn': 5}), 5)

    def test_letnev_can_pay_for_and_reroll_selected_space_combat_dice(self):
        config, board = load_board(ROOT / 'maps/four_player.json')
        players = create_players(board, config)
        letnev = next(player for player in players if player.faction == 'letnev')
        sol = next(player for player in players if player.faction == 'sol')
        letnev.trade_goods = 2
        target = board[(0, 0)]
        target.units.extend((
            Unit('letnev-combat-cruiser', 'cruiser', 'letnev', letnev.color_code,
                 UnitLocation(Region.SPACE)),
            Unit('sol-combat-cruiser', 'cruiser', 'sol', sol.color_code,
                 UnitLocation(Region.SPACE)),
        ))
        movement = MovementController(board, players)
        session = Session(letnev, target, {}, Snapshot.capture(board, letnev, players))
        movement.session = session
        movement.start_combat(session)
        movement.spend_munitions('letnev')

        with patch('movement.random.randint', side_effect=(1, 1)):
            movement.advance_combat()
        movement.toggle_combat_reroll('letnev', 0)
        with patch('movement.random.randint', return_value=10):
            self.assertEqual(movement.reroll_selected_combat_dice('letnev'), 1)

        self.assertEqual(letnev.trade_goods, 0)
        self.assertEqual(session.combat_hits['sol'], 1)
        self.assertNotIn('letnev', session.munitions_available)
        with self.assertRaises(MovementError):
            movement.toggle_combat_reroll('letnev', 0)


class HacanAbilityTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.by_faction = {player.faction: player for player in self.players}
        self.turn = TurnOrder(self.players)
        self.movement = MovementController(self.board, self.players)
        self.controller = TransactionController(self.board, self.players, self.turn, self.movement)

    def test_guild_ships_allows_hacan_transactions_across_the_galaxy(self):
        self.assertFalse(self.controller.are_neighbors('sol', 'jolnar'))
        self.turn.active_index = self.players.index(self.by_faction['hacan'])
        self.controller.open()
        self.assertIn(self.by_faction['sol'], self.controller.eligible_partners(self.by_faction['hacan']))
        self.controller.cancel()

    def test_arbiters_and_trade_goods_are_exchanged_in_one_transaction(self):
        hacan, sol = self.by_faction['hacan'], self.by_faction['sol']
        hacan.trade_goods, sol.trade_goods = 4, 2
        hacan.commodities, sol.commodities = 2, 1
        hacan.action_cards[:] = [f'hacan_card_{index}' for index in range(7)]
        sol.action_cards[:] = [f'sol_card_{index}' for index in range(7)]
        self.turn.active_index = self.players.index(hacan)
        self.controller.open()
        self.controller.choose_partner('sol')
        for _ in range(2):
            self.controller.change('give_trade_goods', 1)
        self.controller.change('take_trade_goods', 1)
        self.controller.change('give_commodities', 1)
        self.controller.toggle_action_card('hacan', 'hacan_card_6')
        self.controller.toggle_action_card('sol', 'sol_card_6')
        self.controller.confirm()

        self.assertEqual((hacan.trade_goods, sol.trade_goods), (3, 3))
        self.assertEqual((hacan.commodities, sol.commodities), (1, 2))
        self.assertEqual(hacan.action_cards[-1], 'sol_card_6')
        self.assertEqual(sol.action_cards[-1], 'hacan_card_6')
        self.assertEqual(len(hacan.action_cards), 7)
        self.assertEqual(len(sol.action_cards), 7)
        self.assertFalse(self.controller.can_transact(hacan, sol))

    def test_hacan_masters_of_trade_resolves_trade_secondary_for_free(self):
        hacan = self.by_faction['hacan']
        resolution = StrategyResolution(5, hacan, hacan, [])
        controller = StrategyController(self.board, self.turn, self.movement)
        controller.session = resolution
        self.assertEqual(controller.secondary_cost(), 0)
        resolution.player = self.by_faction['sol']
        self.assertEqual(controller.secondary_cost(), 1)

    def test_trade_card_gifting_is_limited_to_neighbors_without_hacan(self):
        sol, jolnar = self.by_faction['sol'], self.by_faction['jolnar']
        self.turn.active_index = self.players.index(sol)
        self.controller.open()
        self.assertNotIn(jolnar, self.controller.eligible_partners(sol))
        with self.assertRaises(TransactionError):
            self.controller.choose_partner('jolnar')


if __name__ == '__main__':
    unittest.main()
