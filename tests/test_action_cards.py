import unittest
import random
from unittest.mock import patch

from action_cards import (ACTION_CARD_DEFS, ActionCardController,
                          canonical_action_card)
from action_card_deck import ActionCardDeck
from action_card_panel import CARD_ART, CARD_IMAGES
from board import ROOT, load_board
from movement import MovementController, Session, Snapshot, capital_ship
from player import create_players
from strategic_action import StrategyController
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class ActionCardTests(unittest.TestCase):
    def setUp(self):
        self.config, self.board = load_board()
        self.players = create_players(self.board, self.config)
        self.movement = MovementController(self.board, self.players)
        self.cards = ActionCardController(self.players, self.movement, deck=[])
        self.sol = next(player for player in self.players if player.faction == 'sol')

    def test_draw_pile_contains_all_shortlisted_base_card_types(self):
        deck = ActionCardDeck(rng=random.Random(4))

        self.assertEqual({canonical_action_card(alias) for alias in deck.cards},
                         set(ACTION_CARD_DEFS))
        self.assertIn('f_conscription', deck.cards)

    def test_war_effort_is_an_action_and_places_a_cruiser_in_a_friendly_fleet(self):
        turns = TurnOrder([self.sol], strategy_enabled=False)
        self.cards.turn = turns
        self.sol.action_cards[:] = ['war_effort']
        system = next(tile for tile in self.board.values() if any(
            unit.owner == self.sol.faction and unit.location.region == Region.SPACE
            for unit in tile.units))

        self.assertTrue(self.cards.can_play(self.sol.faction, 'war_effort'))
        self.cards.play(self.sol, 0)
        self.cards.resolve_pending(system.position)

        self.assertTrue(any(unit.owner == self.sol.faction and unit.kind == 'cruiser'
                            for unit in system.units))
        self.assertTrue(turns.action_used)
        self.assertEqual(self.sol.action_cards, [])

    def test_keleres_can_spend_a_trade_good_after_a_component_action_card(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('keleresm', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        keleres = next(player for player in players if player.faction == 'keleresm')
        movement = MovementController(board, players)
        turns = TurnOrder([keleres], strategy_enabled=False)
        cards = ActionCardController(players, movement, deck=[], turn=turns)
        system = next(tile for tile in board.values() if any(
            unit.owner == keleres.faction and unit.location.region == Region.SPACE
            for unit in tile.units))
        keleres.action_cards[:] = ['war_effort']
        keleres.trade_goods = 1

        cards.play(keleres, 0)
        cards.resolve_pending(system.position)

        self.assertEqual('keleres_unlock', cards.pending['effect'])
        self.assertFalse(keleres.leaders['kelerescommander'].unlocked)
        self.assertEqual(1, keleres.trade_goods)
        self.assertEqual([(('spend',), 'Spend 1 trade good · unlock Suffi An'),
                          (('decline',), 'Keep the trade good')], cards.pending_choices())

        cards.resolve_pending(('spend',))

        self.assertTrue(keleres.leaders['kelerescommander'].unlocked)
        self.assertEqual(0, keleres.trade_goods)
        self.assertTrue(turns.action_used)

    def test_keleres_can_decline_unlock_cost_and_keep_trade_good(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('keleresm', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        keleres = next(player for player in players if player.faction == 'keleresm')
        movement = MovementController(board, players)
        turns = TurnOrder([keleres], strategy_enabled=False)
        cards = ActionCardController(players, movement, deck=[], turn=turns)
        system = next(tile for tile in board.values() if any(
            unit.owner == keleres.faction and unit.location.region == Region.SPACE
            for unit in tile.units))
        keleres.action_cards[:] = ['war_effort']
        keleres.trade_goods = 1

        cards.play(keleres, 0)
        cards.resolve_pending(system.position)
        cards.resolve_pending(('decline',))

        self.assertFalse(keleres.leaders['kelerescommander'].unlocked)
        self.assertEqual(1, keleres.trade_goods)

    def test_war_effort_has_no_destination_when_cruiser_supply_is_empty(self):
        for tile in self.board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == self.sol.faction and
                                     unit.kind == 'cruiser')]
        for index in range(8):
            tile = list(self.board.values())[index]
            tile.units.append(Unit(f'war-effort-supply-{index}', 'cruiser',
                                   self.sol.faction, self.sol.color_code,
                                   UnitLocation(Region.SPACE)))
        self.cards.turn = TurnOrder([self.sol], strategy_enabled=False)
        self.sol.action_cards[:] = ['war_effort']

        self.assertTrue(self.cards.can_play(self.sol.faction, 'war_effort'))
        self.cards.play(self.sol, 0)
        self.assertEqual([], self.cards.pending_choices())
        self.cards.cancel_pending()
        self.assertEqual(['war_effort'], self.sol.action_cards)

    def test_war_effort_refreshes_commander_unlocks_after_placing_the_cruiser(self):
        config, board = load_board(ROOT / 'maps/four_player.json',
                                   ('mentak', 'sol', 'jolnar', 'hacan'))
        from player import create_players
        players = create_players(board, config)
        mentak = next(player for player in players if player.faction == 'mentak')
        movement = MovementController(board, players)
        turns = TurnOrder([mentak], strategy_enabled=False)
        cards = ActionCardController(players, movement, deck=[], turn=turns)
        for tile in board.values():
            tile.units[:] = [unit for unit in tile.units
                             if not (unit.owner == mentak.faction and unit.kind == 'cruiser')]
        system = next(tile for tile in board.values() if any(
            unit.owner == mentak.faction and unit.location.region == Region.SPACE
            for unit in tile.units))
        system.units.extend(Unit(f'mentak-cruiser-{index}', 'cruiser', mentak.faction,
                                 mentak.color_code, UnitLocation(Region.SPACE))
                            for index in range(3))
        mentak.action_cards[:] = ['war_effort']

        cards.play(mentak, 0)
        cards.resolve_pending(system.position)

        self.assertEqual(4, sum(unit.owner == mentak.faction and unit.kind == 'cruiser'
                                for tile in board.values() for unit in tile.units))
        self.assertTrue(mentak.leaders['mentakcommander'].unlocked)

    def test_fighter_conscription_checks_the_ten_system_anchor_limit(self):
        for tile in self.board.values():
            tile.units.clear()
        for index, tile in enumerate(list(self.board.values())[:11]):
            tile.units.append(Unit(f'fighter-conscription-anchor-{index}', 'carrier',
                                   self.sol.faction, self.sol.color_code,
                                   UnitLocation(Region.SPACE)))
        self.cards.turn = TurnOrder([self.sol], strategy_enabled=False)
        self.sol.action_cards[:] = ['fighter_conscription']

        self.assertEqual(11, len(self.cards.fighter_conscription_systems(self.sol)))
        self.assertFalse(self.cards.can_resolve_fighter_conscription(self.sol))
        self.assertFalse(self.cards.can_play(self.sol.faction, 'fighter_conscription'))

    def test_upgrade_prompts_for_a_cruiser_and_replaces_it_with_a_dreadnought(self):
        system = next(iter(self.board.values()))
        cruiser = Unit('action-sol-upgrade-cruiser', 'cruiser', self.sol.faction,
                       self.sol.color_code, UnitLocation(Region.SPACE))
        system.units.append(cruiser)
        session = Session(self.sol, system, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='movement')
        self.movement.session = session
        self.sol.action_cards[:] = ['upgrade']

        self.cards.play(self.sol, 0)
        self.cards.resolve_pending(cruiser.unit_id)

        self.assertNotIn(cruiser, system.units)
        self.assertTrue(any(unit.owner == self.sol.faction and unit.kind == 'dreadnought'
                            for unit in system.units))
        self.assertEqual(self.sol.action_cards, [])

    def test_direct_hit_can_destroy_a_ship_damaged_by_space_cannon(self):
        system = next(iter(self.board.values()))
        dreadnought = Unit('action-sol-cannon-dread', 'dreadnought', self.sol.faction,
                           self.sol.color_code, UnitLocation(Region.SPACE))
        system.units.append(dreadnought)
        hacan = next(player for player in self.players if player.faction == 'hacan')
        hacan.action_cards[:] = ['dh1']
        session = Session(self.sol, system, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_cannon_response', space_cannon_next_stage='invasion',
                          cannon_checked=True,
                          space_cannon_events=[{'owner': hacan.faction,
                                                'candidates': [dreadnought.unit_id],
                                                'graviton': False}])
        self.movement.session = session

        self.movement.continue_after_space_cannon(session)

        self.assertEqual(session.stage, 'space_cannon_assign')
        self.movement.assign_space_cannon_hit(dreadnought.unit_id)
        self.assertEqual(session.stage, 'space_cannon_direct_hit')
        self.assertTrue(self.cards.can_play(hacan.faction, 'dh1', session))
        self.cards.play(hacan, 0)
        self.cards.resolve_pending(dreadnought.unit_id)
        self.movement.continue_after_space_cannon(session)

        self.assertNotIn(dreadnought, system.units)
        self.assertEqual(session.stage, 'invasion')

    def test_bunker_reduces_bombardment_against_the_card_owners_planet(self):
        system = next(tile for tile in self.board.values() if tile.planets)
        planet_id = system.planets[0].planet_id
        hacan = next(player for player in self.players if player.faction == 'hacan')
        defender = Unit('action-hacan-bunker-infantry', 'infantry', hacan.faction,
                        hacan.color_code, UnitLocation(Region.PLANET, planet_id=planet_id))
        bomber = Unit('action-sol-bunker-dread', 'dreadnought', self.sol.faction,
                      self.sol.color_code, UnitLocation(Region.SPACE))
        system.units.extend((defender, bomber))
        system.planet_owners[planet_id] = hacan.faction
        session = Session(self.sol, system, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='invasion_start')
        self.movement.session = session
        hacan.action_cards[:] = ['bunker']

        self.cards.play(hacan, 0)
        self.movement.continue_invasion_start(session)
        session.bombard_targets[bomber.unit_id] = planet_id
        with patch('movement.random.randint', return_value=8):
            self.movement.resolve_bombardment()

        self.assertEqual(session.bombard_rolls[0]['value'], 4)
        self.assertIn(defender, system.units)
        self.assertIn('8-4=4 miss', session.bombard_log[-1])

    def test_fire_team_rerolls_selected_ground_dice(self):
        system = next(tile for tile in self.board.values() if tile.planets)
        planet_id = system.planets[0].planet_id
        hacan = next(player for player in self.players if player.faction == 'hacan')
        infantry = Unit('action-sol-fire-team-infantry', 'infantry', self.sol.faction,
                        self.sol.color_code, UnitLocation(Region.PLANET, planet_id=planet_id))
        defender = Unit('action-hacan-fire-team-infantry', 'infantry', hacan.faction,
                        hacan.color_code, UnitLocation(Region.PLANET, planet_id=planet_id))
        system.units.extend((infantry, defender))
        session = Session(self.sol, system, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='ground_combat', combat_type='ground',
                          combat_factions=('sol', 'hacan'), combat_needs_resolution=True,
                          combat_rolls={'sol': [{'unit_id': infantry.unit_id, 'kind': 'infantry',
                                                 'value': 1, 'natural': 1, 'modifier': 0,
                                                 'hit': False, 'rerolled': False}],
                                        'hacan': []},
                          combat_hits={'sol': 0, 'hacan': 0})
        self.movement.session = session
        self.sol.action_cards[:] = ['fire_team']

        self.cards.play(self.sol, 0)
        self.cards.toggle_fire_team_die(self.sol.faction, 0)
        with patch('movement.random.randint', return_value=10):
            self.cards.resolve_fire_team(self.sol.faction)

        self.assertTrue(session.combat_rolls['sol'][0]['hit'])
        self.assertEqual(session.combat_hits['hacan'], 1)
        self.assertIsNone(session.fire_team_pending)

    def test_flank_speed_unlocks_a_route_and_is_spent_on_play(self):
        fleet = [(origin, unit) for origin in self.board.values() for unit in origin.units
                 if unit.owner == self.sol.faction and capital_ship(unit)]
        target = next(tile for tile in self.board.values() if not tile.command_tokens and
                      not any(self.movement.route(origin, tile, unit, self.sol)
                              for origin, unit in fleet) and
                      any(self.movement.route(origin, tile, unit, self.sol, move_bonus=1)
                          for origin, unit in fleet))
        origin, ship = next((origin, unit) for origin, unit in fleet
                            if self.movement.route(origin, target, unit, self.sol, move_bonus=1))
        session = self.movement.activate(self.sol, target.position)
        self.sol.action_cards.append('flank_speed')
        self.assertFalse(session.sources)
        self.assertTrue(self.cards.can_play(self.sol.faction, 'flank_speed', session))

        self.cards.play(self.sol, 0)

        self.assertIn(ship.unit_id, session.sources[origin.position].routes)
        self.assertEqual(session.movement_bonus, 1)
        self.assertEqual(self.sol.action_cards, [])
        self.assertEqual(self.cards.discard, ['flank_speed'])

    def test_undo_activation_restores_played_card_and_discard(self):
        origin = next(tile for tile in self.board.values()
                      if any(unit.owner == self.sol.faction for unit in tile.units))
        target = next(tile for tile in self.movement.neighbors(origin)
                      if not tile.command_tokens and tile.planets)
        self.sol.action_cards.append('flank_speed')
        tactical = self.sol.command_pools['tactical']
        self.movement.activate(self.sol, target.position)
        self.cards.play(self.sol, 0)

        self.assertTrue(self.movement.undo())

        self.assertEqual(self.sol.action_cards, ['flank_speed'])
        self.assertEqual(self.cards.discard, [])
        self.assertEqual(self.sol.command_pools['tactical'], tactical)
        self.assertNotIn(self.sol.faction, target.command_tokens)

    def test_combat_cards_only_highlight_in_their_timing_windows(self):
        target = self.board[next(iter(self.board))]
        sol_ship = Unit('action-sol-dread', 'dreadnought', 'sol', self.sol.color_code,
                        UnitLocation(Region.SPACE))
        fighter = Unit('action-sol-fighter', 'fighter', 'sol', self.sol.color_code,
                       UnitLocation(Region.SPACE))
        hacan = next(player for player in self.players if player.faction == 'hacan')
        enemy_ship = Unit('action-hacan-carrier', 'carrier', 'hacan', hacan.color_code,
                          UnitLocation(Region.SPACE))
        target.units.extend((sol_ship, fighter, enemy_ship))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'))
        self.movement.session = session
        self.sol.action_cards[:] = ['morale_boost', 'fighter_prototype', 'shields_holding']

        self.assertTrue(self.cards.can_play('sol', 'morale_boost', session))
        self.assertTrue(self.cards.can_play('sol', 'fighter_prototype', session))
        self.assertFalse(self.cards.can_play('sol', 'shields_holding', session))
        self.cards.play(self.sol, 0)
        self.cards.play(self.sol, 0)
        self.assertFalse(self.cards.can_play('sol', 'morale_boost', session))
        self.assertFalse(self.cards.can_play('sol', 'fighter_prototype', session))

        session.combat_hits['sol'] = 3
        session.combat_needs_resolution = True
        self.assertTrue(self.cards.can_play('sol', 'shields_holding', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(session.combat_hits['sol'], 1)

        session.combat_needs_resolution = False
        session.combat_rolls.clear()
        with patch('movement.random.randint', return_value=9):
            self.movement.advance_combat()
        fighter_roll = next(roll for roll in session.combat_rolls['sol']
                            if roll['kind'] == 'fighter')
        dread_roll = next(roll for roll in session.combat_rolls['sol']
                          if roll['kind'] == 'dreadnought')
        self.assertEqual(fighter_roll['modifier'], 3)
        self.assertEqual(dread_roll['modifier'], 1)

    def test_emergency_repairs_is_playable_after_hits_resolve(self):
        target = next(iter(self.board.values()))
        dread = Unit('action-sol-damaged-dread', 'dreadnought', 'sol', self.sol.color_code,
                     UnitLocation(Region.SPACE))
        enemy = next(player for player in self.players if player.faction == 'hacan')
        opponent = Unit('action-hacan-carrier', 'carrier', enemy.faction, enemy.color_code,
                        UnitLocation(Region.SPACE))
        target.units.extend((dread, opponent))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'),
                          combat_hits={'sol': 1, 'hacan': 0},
                          combat_assignments={'sol': ['action-sol-damaged-dread'], 'hacan': []},
                          combat_needs_resolution=True)
        self.movement.session = session
        self.sol.action_cards.append('emergency_repairs')

        self.movement.advance_combat()

        self.assertTrue(dread.damaged)
        self.assertEqual(session.stage, 'combat_end')
        self.assertTrue(self.cards.can_play('sol', 'emergency_repairs', session))
        self.cards.play(self.sol, 0)
        self.assertFalse(dread.damaged)
        self.movement.advance_combat()
        self.assertEqual(session.stage, 'space_combat')

    def test_skilled_retreat_moves_fleet_and_places_a_reinforcement_token(self):
        target = next(tile for tile in self.board.values()
                      if not tile.planets and len(self.movement.neighbors(tile)) >= 2)
        enemy = next(player for player in self.players if player.faction == 'hacan')
        ship = Unit('action-sol-cruiser', 'cruiser', 'sol', self.sol.color_code,
                    UnitLocation(Region.SPACE))
        opponent = Unit('action-hacan-destroyer', 'destroyer', enemy.faction, enemy.color_code,
                        UnitLocation(Region.SPACE))
        target.units.extend((ship, opponent))
        session = Session(self.sol, target, {}, Snapshot.capture(self.board, self.sol, self.players),
                          stage='space_combat', combat_factions=('sol', 'hacan'))
        self.movement.session = session
        self.sol.action_cards.append('skilled_retreat')
        destination = next(tile for tile in self.movement.neighbors(target)
                           if not any(unit.owner != 'sol' and unit.location.region == Region.SPACE
                                      for unit in tile.units) and 'sol' not in tile.command_tokens)

        self.assertTrue(self.cards.can_play('sol', 'skilled_retreat', session))
        self.cards.play(self.sol, 0)
        self.assertEqual(session.stage, 'retreat_selection')
        self.movement.resolve_retreat(destination.position)

        self.assertIn(ship, destination.units)
        self.assertIn('sol', destination.command_tokens)
        self.assertNotIn(ship, target.units)
        self.assertTrue(session.space_combat_resolved)

    def test_politics_draws_two_cards_and_status_draw_is_limited_to_seven(self):
        self.cards.deck[:] = ['flank_speed', 'morale_boost', 'shields_holding']
        turns = TurnOrder(self.players)
        player = turns.active_player
        turns.strategy_assignments[player.faction].append(3)
        strategy = StrategyController(self.board, turns, self.movement, self.cards)

        strategy.start(3)

        self.assertEqual(len(strategy.session.drawn_cards), 2)
        self.assertEqual(len(player.action_cards), 2)
        strategy.choose_speaker('hacan')
        self.cards.deck.extend(('flank_speed', 'morale_boost'))
        responder = strategy.player
        tokens_before = responder.command_pools['strategic']
        self.assertEqual(responder.faction, 'jolnar')
        self.assertFalse(strategy.secondary_unavailable())
        strategy.accept_secondary()
        self.assertEqual(len(responder.action_cards), 2)
        self.assertEqual(responder.command_pools['strategic'], tokens_before - 1)
        self.assertEqual(strategy.player.faction, 'hacan')
        player.action_cards[:] = ['flank_speed'] * 6
        self.assertEqual(len(self.cards.draw(player, 2)), 1)
        self.assertEqual(len(player.action_cards), 7)


class ActionCardArtworkTests(unittest.TestCase):
    def test_all_shortlisted_cards_have_a_face_image(self):
        self.assertEqual(len(CARD_IMAGES), 18)
        self.assertTrue(all((CARD_ART / filename).is_file() for filename in CARD_IMAGES.values()))


if __name__ == '__main__':
    unittest.main()
