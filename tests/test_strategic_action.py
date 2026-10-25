import unittest
from types import SimpleNamespace

from board import ROOT, load_board
from exploration import ExplorationController, PendingExploration
from player import PlanetCard, create_players, command_tokens_in_play
from movement import MovementController
from strategic_action import StrategyController, StrategyResolution
from turn_order import TurnOrder
from units import Unit, UnitLocation, Region


class StrategicActionTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board()
        by_faction = {p.faction: p for p in create_players(self.board, config)}
        self.players = [by_faction[f] for f in ('sol', 'hacan', 'jolnar')]
        self.sol, self.hacan, self.jolnar = self.players
        self.turn = TurnOrder(self.players, strategy_enabled=True)
        for card in (1, 2, 3, 4, 5, 6):
            self.turn.choose_strategy_card(card)
        self.movement = MovementController(self.board, self.players)
        self.controller = StrategyController(self.board, self.turn, self.movement)

    def start_card(self, card, owner=0):
        # Exercise abilities independently of the draft's chosen test cards.
        for cards in self.turn.strategy_assignments.values():
            if card in cards:
                cards.remove(card)
        self.turn.strategy_assignments[self.players[owner].faction].append(card)
        self.turn.active_index = owner
        self.controller.start(card)

    def allocate_all(self):
        player = self.controller.player
        while player.pending_commands:
            self.controller.allocate('tactical')

    def test_payment_is_explicit_and_allocation_precedes_each_secondary(self):
        self.sol.trade_goods = 3
        self.start_card(1)
        c = self.controller
        c.change_purchase(1)
        with self.assertRaises(ValueError):
            c.pay_leadership()
        self.assertEqual(self.sol.trade_goods, 3)
        self.assertFalse(self.sol.planets[0].exhausted)
        planet = self.sol.planets[0].planet
        c.toggle_payment(planet.planet_id)
        self.assertTrue(self.sol.planets[0].exhausted)
        c.change_goods(3 - planet.influence)
        self.assertEqual(c.payment(), 3)
        c.pay_leadership()
        self.assertEqual(c.session.stage, 'allocate')
        self.assertIs(c.player, self.sol)
        self.assertEqual(self.sol.pending_commands, 4)
        with self.assertRaises(ValueError):
            self.turn.end_turn()
        for _ in range(3):
            c.allocate('strategic')
            self.assertIs(c.player, self.sol)
        c.allocate('fleet')
        self.assertIs(c.player, self.hacan)
        self.assertIs(self.turn.active_player, self.sol)
        self.assertEqual(c.session.stage, 'offer')
        self.assertFalse(self.turn.action_used)
        self.assertTrue(self.turn.can_use_secondary(self.hacan, 1))
        self.assertFalse(self.turn.can_use_secondary(self.jolnar, 1))
        c.decline_secondary()
        self.jolnar.command_pools['strategic'] = 0
        self.jolnar.trade_goods = 3
        c.accept_secondary()
        c.change_purchase(1)
        c.change_goods(3)
        c.pay_leadership()
        self.assertEqual(c.session.stage, 'allocate')
        c.allocate('fleet')
        self.assertIsNone(c.session)
        self.assertTrue(self.turn.action_used)
        self.assertIs(self.turn.active_player, self.sol)
        self.assertFalse(self.turn.can_use_secondary(self.hacan, 1))
        with self.assertRaises(ValueError):
            c.start(4)

    def test_payment_can_be_unselected_and_unselected_assets_stay_ready(self):
        self.start_card(1, owner=1)
        c = self.controller
        card = self.hacan.planets[0]
        c.toggle_payment(card.planet.planet_id)
        c.toggle_payment(card.planet.planet_id)
        self.assertFalse(card.exhausted)
        self.assertEqual(c.payment(), 0)
        self.hacan.trade_goods = 4
        c.change_purchase(1)
        c.change_goods(3)
        c.pay_leadership()
        self.assertEqual(self.hacan.trade_goods, 1)
        self.assertTrue(all(not p.exhausted for p in self.hacan.planets))

    def test_cabal_agent_reaction_is_queued_for_trade_primary_and_secondary(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('sol', 'cabal', 'hacan', 'jolnar'))
        by_faction = {p.faction: p for p in create_players(board, config)}
        players = [by_faction[f] for f in ('sol', 'cabal', 'hacan', 'jolnar')]
        sol, cabal, hacan, _jolnar = players
        turn = TurnOrder(players, strategy_enabled=True)
        turn.strategy_selection = False
        turn.strategy_assignments = {player.faction: [] for player in players}
        turn.strategy_assignments['sol'] = [5]
        turn.active_index = players.index(sol)
        movement = MovementController(board, players)
        controller = StrategyController(board, turn, movement)

        controller.start(5)
        controller.confirm_trade()
        self.assertEqual(controller.pending_cabal_replenishments,
                         [('cabal', 'sol', sol.commodity_limit)])
        controller.pending_cabal_replenishments.clear()  # Resolve the reaction.

        self.assertIs(controller.player, cabal)
        controller.decline_secondary()
        self.assertIs(controller.player, hacan)
        controller.accept_secondary()
        self.assertEqual(controller.pending_cabal_replenishments,
                         [('cabal', 'hacan', hacan.commodity_limit)])

    def test_cabal_agent_reacts_to_replenishment_exploration_not_commodity_gain(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('sol', 'cabal', 'hacan', 'jolnar'))
        players = create_players(board, config)
        sol, cabal = players[:2]
        exploration = ExplorationController(board, players)
        movement = MovementController(board, players)
        movement.exploration = exploration
        controller = StrategyController(board, TurnOrder(players), movement,
                                        exploration=exploration)
        movement.on_commodities_replenished = controller.note_commodities_replenished
        empty = next(tile for tile in board.values() if not tile.planets)
        exploration.pending = PendingExploration(
            sol.faction, empty.position, None, 'ms1',
            ('replenish_commodities', 'convert_commodities'))

        movement.resolve_exploration_choice('replenish_commodities')

        self.assertEqual(sol.commodities, sol.commodity_limit)
        self.assertEqual(controller.pending_cabal_replenishments,
                         [('cabal', 'sol', sol.commodity_limit)])
        controller.pending_cabal_replenishments.clear()
        exploration.pending = PendingExploration(
            sol.faction, empty.position, None, 'aw1',
            ('gain_commodities', 'convert_commodities'))

        movement.resolve_exploration_choice('gain_commodities')

        self.assertEqual(controller.pending_cabal_replenishments, [])

    def test_exploration_attachment_modifies_leadership_payment(self):
        card = self.sol.planets[0]
        self.controller.exploration = SimpleNamespace(
            planet_values=lambda planet: (planet.resources, planet.influence + 2))
        self.start_card(1)
        self.controller.change_purchase(1)
        self.controller.toggle_payment(card.planet.planet_id)
        self.assertEqual(self.controller.payment(), card.planet.influence + 2)
        self.controller.pay_leadership()

    def test_hero_planet_attachment_benefits_new_controller(self):
        planet = self.hacan.planets[0].planet
        self.sol.hero_attachments.add(planet.planet_id)
        self.start_card(1, owner=1)
        values = self.controller.planet_values(self.hacan.planets[0])
        self.assertEqual(values, (planet.resources + 3, planet.influence + 3))

    def test_dynamis_core_changes_trade_replenishment(self):
        self.controller.exploration = SimpleNamespace(
            relics=SimpleNamespace(commodity_limit=lambda player: player.commodity_limit + 2))
        self.start_card(5)
        self.controller.confirm_trade()
        self.assertEqual(self.sol.commodities, self.sol.commodity_limit + 2)
        self.controller.accept_secondary()
        self.assertEqual(self.hacan.commodities, self.hacan.commodity_limit + 2)

    def test_trade_primary_refreshes_commander_unlocks_before_secondary_phase(self):
        self.hacan.trade_goods = 7
        self.start_card(5, owner=1)

        self.controller.confirm_trade()

        self.assertEqual(10, self.hacan.trade_goods)
        self.assertTrue(self.hacan.leaders['hacancommander'].unlocked)
        self.assertIs(self.controller.player, self.jolnar)

    def test_psychoarchaeology_uses_exhausted_attachment_specialty(self):
        card = self.sol.planets[0]
        card.exhausted = True
        self.sol.technologies |= {'pa'}
        self.controller.exploration = SimpleNamespace(
            planet_values=lambda planet: (planet.resources, planet.influence),
            planet_specialties=lambda planet: ('WARFARE',))
        self.start_card(7)
        self.controller.select_technology('md')
        self.controller.toggle_technology_planet(card.planet.planet_id, specialty=True)
        self.controller.research_selected()
        self.assertIn('md', self.sol.technologies)
        self.assertTrue(card.exhausted, 'Psychoarchaeology does not ready a planet.')

    def test_aida_exhausts_only_when_upgrade_needs_its_skip(self):
        self.sol.technologies |= {'aida'}
        self.start_card(7)
        self.controller.select_technology('ac2')
        self.controller.research_selected()
        self.assertIn('ac2', self.sol.technologies)
        self.assertIn('aida', self.sol.exhausted_technologies)

    def test_leadership_supply_includes_board_and_allows_partial_free_gain(self):
        self.sol.command_pools = dict(tactical=6, fleet=5, strategic=3)
        self.board[(0, 0)].command_tokens.add('sol')
        self.start_card(1)
        self.assertEqual(self.controller.session.base_gain, 1)
        self.controller.change_purchase(100)
        self.assertEqual(self.controller.session.purchases, 0)
        self.controller.pay_leadership()
        self.assertEqual(command_tokens_in_play(self.sol, self.board), 16)
        self.allocate_all()
        self.assertIs(self.controller.player, self.hacan)

    def test_trade_responders_include_passed_players_and_free_choice_costs_nothing(self):
        self.start_card(5)
        c = self.controller
        self.turn.passed_indices.add(1)
        self.hacan.command_pools['strategic'] = 0
        c.session.free_trade.add('hacan')
        c.confirm_trade()
        self.assertIs(c.player, self.hacan)
        c.accept_secondary()
        self.assertEqual(self.hacan.commodities, self.hacan.commodity_limit)
        self.assertEqual(self.hacan.command_pools['strategic'], 0)
        self.assertIs(c.player, self.jolnar)
        previous = self.jolnar.command_pools['strategic']
        c.accept_secondary()
        self.assertEqual(self.jolnar.command_pools['strategic'], previous - 1)
        self.assertIsNone(c.session)
        self.assertEqual(self.turn.passed_indices, {1})
        self.assertEqual(self.sol.trade_goods, 3)

    def test_construction_uses_chosen_planets_and_places_secondary_token(self):
        self.start_card(4)
        c = self.controller
        planet = self.sol.planets[0].planet.planet_id
        c.session.structure = 'pds'
        before = sum(len(t.units) for t in self.board.values())
        first_tile = next(t for t in self.board.values() if planet in t.planet_owners)
        c.select_system(first_tile.position)
        c.build(planet)
        self.assertEqual(sum(len(t.units) for t in self.board.values()), before + 1)
        self.assertTrue(c.session.primary)
        c.continue_stage()
        c.accept_secondary()
        target = self.hacan.planets[1].planet.planet_id
        tile = next(t for t in self.board.values() if target in t.planet_owners)
        self.assertNotIn('hacan', tile.command_tokens)
        c.select_system(tile.position)
        c.build(target)
        self.assertIn('hacan', tile.command_tokens)
        self.assertTrue(any(u.kind == 'pds' and u.location.planet_id == target for u in tile.units))
        self.assertIs(c.player, self.jolnar)

    def test_construction_respects_player_pds_limit_and_dmz(self):
        from exploration import ExplorationController

        self.start_card(4)
        c = self.controller
        card = self.sol.planets[0]
        tile = c.planet_system(card.planet.planet_id)
        tile.units.extend((
            Unit('sol-first-pds', 'pds', 'sol', self.sol.color_code,
                 UnitLocation(Region.PLANET, card.planet.planet_id)),
            Unit('sol-second-pds', 'pds', 'sol', self.sol.color_code,
                 UnitLocation(Region.PLANET, card.planet.planet_id)),
            Unit('hacan-pds-on-sol-planet', 'pds', 'hacan', self.hacan.color_code,
                 UnitLocation(Region.PLANET, card.planet.planet_id)),
        ))
        c.session.structure = 'pds'
        eligible = {candidate.planet.planet_id
                    for candidate in c.buildable_planets(selected_only=False)}
        self.assertNotIn(card.planet.planet_id, eligible,
                         'A player cannot build a third PDS on a planet')

        exploration = ExplorationController(self.board, self.players, relic_deck=[])
        exploration.attachments[card.planet.planet_id] = ['dmz']
        c.exploration = exploration
        tile.units = [unit for unit in tile.units
                      if unit.unit_id not in {'sol-first-pds', 'sol-second-pds',
                                              'hacan-pds-on-sol-planet'}]
        c.session.structure = 'spacedock'
        eligible = {candidate.planet.planet_id
                    for candidate in c.buildable_planets(selected_only=False)}
        self.assertNotIn(card.planet.planet_id, eligible,
                         'Construction cannot place structures on a DMZ planet')

    def test_construction_secondary_places_saar_floating_factory_in_space_and_it_can_move(self):
        config, board = load_board(factions=('sol', 'saar', 'hacan'))
        players_by_faction = {player.faction: player for player in create_players(board, config)}
        players = [players_by_faction[faction] for faction in ('sol', 'saar', 'hacan')]
        sol, saar, _ = players
        turn = TurnOrder(players, strategy_enabled=True)
        for card in (1, 2, 3, 4, 5, 6):
            turn.choose_strategy_card(card)
        for cards in turn.strategy_assignments.values():
            if 4 in cards:
                cards.remove(4)
        turn.strategy_assignments['sol'].append(4)
        turn.active_index = 0
        movement = MovementController(board, players)
        controller = StrategyController(board, turn, movement)
        controller.start(4)

        sol_home = next(tile for tile in board.values()
                        if any(planet.faction_homeworld == 'sol' for planet in tile.planets))
        sol_planet = next(planet.planet_id for planet in sol_home.planets
                          if sol_home.planet_owners.get(planet.planet_id) == 'sol')
        controller.session.structure = 'pds'
        controller.select_system(sol_home.position)
        controller.build(sol_planet)  # First primary PDS.
        controller.select_system(sol_home.position)
        controller.build(sol_planet)  # Second primary PDS.
        self.assertIs(controller.player, saar)
        controller.accept_secondary()
        saar_home = next(tile for tile in board.values()
                         if any(planet.faction_homeworld == 'saar' for planet in tile.planets))
        starting_factory_ids = {unit.unit_id for unit in saar_home.units
                                if unit.owner == 'saar' and unit.kind == 'spacedock'}
        saar_planet = next(planet.planet_id for planet in saar_home.planets
                           if saar_home.planet_owners.get(planet.planet_id) == 'saar')
        controller.session.structure = 'spacedock'
        controller.select_system(saar_home.position)
        controller.build(saar_planet)

        factory = next(unit for unit in saar_home.units
                       if unit.owner == 'saar' and unit.kind == 'spacedock' and
                       unit.unit_id not in starting_factory_ids)
        self.assertEqual(factory.location.region, Region.SPACE)
        self.assertEqual(factory.move_value, 1)
        self.assertIn('saar', saar_home.command_tokens)

        # Construction's secondary activates the chosen system, so the
        # factory becomes movable after that command token is returned.
        saar_home.command_tokens.remove('saar')
        destination = next(tile for tile in movement.neighbors(saar_home)
                           if 'saar' not in tile.command_tokens and
                           movement.route(saar_home, tile, factory, saar))
        activation = movement.activate(saar, destination.position)
        self.assertIn(factory, activation.sources[saar_home.position].ships)

    def test_warfare_secondary_waits_for_one_dock_production_and_does_not_advance_turn(self):
        self.start_card(6)
        c = self.controller
        tile = self.board[(0, 0)]
        tile.command_tokens.add('sol')
        c.select_system(tile.position)
        c.select_warfare_token(tile.position, 'sol')
        self.assertIn('sol', tile.command_tokens)
        self.assertEqual(c.session.stage, 'warfare_system')
        c.confirm_warfare_removal()
        self.assertNotIn('sol', tile.command_tokens)
        c.allocate('tactical')
        self.assertTrue(c.session.primary)
        c.continue_stage()
        c.accept_secondary()
        home, dock = c.home_docks()[0]
        self.assertNotIn(self.hacan.faction, home.command_tokens)
        tokens_before = {position: set(system.command_tokens)
                         for position, system in self.board.items()}
        strategy_tokens_before = self.hacan.command_pools['strategic']
        other_planet = next(p for p in home.planets if p.planet_id != dock.location.planet_id)
        home.units.append(Unit('second-hacan-dock', 'spacedock', 'hacan', self.hacan.color_code,
                               UnitLocation(Region.PLANET, other_planet.planet_id)))
        c.produce_at(dock.unit_id)
        self.assertEqual(self.hacan.command_pools['strategic'], strategy_tokens_before - 1)
        self.assertEqual({position: set(system.command_tokens)
                          for position, system in self.board.items()}, tokens_before)
        self.assertEqual(len(self.movement.session.production_sites), 1)
        self.assertEqual(self.movement.session.production_sites[0][0], dock.location.planet_id)
        self.assertEqual(c.session.stage, 'production')
        self.assertIs(self.turn.active_player, self.sol)
        self.movement.skip_production()
        c.poll()
        self.assertIs(c.player, self.jolnar)
        self.assertFalse(self.movement.history)
        self.assertFalse(self.turn.action_used)
        c.decline_secondary()
        self.assertTrue(self.turn.action_used)

    def test_codex_diplomacy_readies_any_two_planets_after_system_choice(self):
        self.start_card(2, owner=1)
        for card in self.hacan.planets:
            card.exhausted = True
        c = self.controller
        selected_system = next(t for t in self.board.values() if 'hacan' in t.planet_owners.values())
        remote_system = next(t for t in self.board.values()
                             if t.position != selected_system.position and t.planets and not t.planet_owners)
        stale_system = next(t for t in self.board.values()
                            if t.position not in (selected_system.position, remote_system.position)
                            and t.planets and not t.planet_owners)
        chosen = PlanetCard(remote_system.planets[0], exhausted=True)
        stale = PlanetCard(stale_system.planets[0], exhausted=True)
        self.hacan.planets.append(chosen)
        self.hacan.planets.append(stale)
        remote_system.planet_owners[chosen.planet.planet_id] = self.hacan.faction
        stale_system.planet_owners[stale.planet.planet_id] = self.hacan.faction
        c.select_system(selected_system.position)
        self.assertEqual(c.session.stage, 'ready_planets')
        self.assertTrue(chosen.exhausted)
        self.assertTrue(stale.exhausted)
        c.toggle_ready(chosen.planet.planet_id)
        c.toggle_ready(stale.planet.planet_id)
        c.confirm_ready()
        self.assertEqual(c.session.stage, 'offer')
        self.assertIs(c.player, self.jolnar)
        c.decline_secondary()
        self.assertIs(c.player, self.sol)
        self.assertTrue(all(card.exhausted for card in self.hacan.planets
                            if selected_system.planet_owners.get(card.planet.planet_id) == 'hacan'))
        self.assertFalse(chosen.exhausted, 'Codex primary can ready a planet outside the chosen system.')
        self.assertFalse(stale.exhausted)
        self.assertEqual(selected_system.command_tokens, {'sol', 'jolnar'})
        self.sol.planets[0].exhausted = True
        c.accept_secondary()
        self.assertEqual(c.session.stage, 'ready_planets')
        c.toggle_ready(chosen.planet.planet_id)
        self.assertFalse(c.session.ready_planets, 'A planet controlled by another player must not be selectable.')
        c.toggle_ready(self.sol.planets[0].planet.planet_id)
        c.confirm_ready()
        self.assertFalse(self.sol.planets[0].exhausted)
        self.assertFalse(chosen.exhausted)

    def test_completing_secondary_production_keeps_next_offer_and_owner_turn(self):
        self.start_card(6, owner=2)
        c = self.controller
        c.continue_stage()
        c.continue_stage()
        self.assertIs(c.player, self.sol)
        c.accept_secondary()
        tile, dock = c.home_docks()[0]
        c.produce_at(dock.unit_id)
        self.sol.trade_goods = 1
        before = len(tile.units)
        self.movement.adjust_production('infantry', 1)
        self.movement.change_production_trade_goods(1)
        self.movement.produce()
        c.poll()
        self.assertEqual(len(tile.units), before + 2)
        self.assertEqual(self.sol.trade_goods, 0)
        self.assertFalse(self.movement.history)
        self.assertEqual(c.session.stage, 'offer')
        self.assertIs(c.player, self.hacan)
        self.assertIs(self.turn.active_player, self.jolnar)
        self.assertFalse(self.turn.action_used)


class NaazHeroSecondaryTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board(ROOT / 'maps/three_player.json',
                                       ('naaz', 'sol', 'hacan'))
        self.players = create_players(self.board, config)
        self.naaz = self.players[0]
        self.turn = TurnOrder(self.players, strategy_enabled=False)
        self.movement = MovementController(self.board, self.players)
        self.controller = StrategyController(self.board, self.turn, self.movement)
        self.naaz.leaders['naazhero'].unlocked = True
        self.naaz.leaders['naazhero'].purged = True
        self.turn.mark_action_completed()

    def test_two_different_secondaries_are_free_and_do_not_complete_another_action(self):
        c = self.controller
        self.naaz.command_pools['strategic'] = 0
        c.start_hero_secondary(self.naaz, 5)
        self.assertEqual(c.secondary_cost(), 0)
        c.accept_secondary()
        self.assertIsNone(c.session)
        self.assertEqual(self.naaz.commodities, self.naaz.commodity_limit)
        self.assertEqual(self.turn.actions_used, 1)
        c.start_hero_secondary(self.naaz, 4)
        c.accept_secondary()
        c.session.structure = 'pds'
        planet = c.buildable_planets()[0]
        tile = c.planet_system(planet.planet.planet_id)
        before = set(tile.command_tokens)
        c.select_system(tile.position)
        c.build(planet.planet.planet_id)
        self.assertIsNone(c.session)
        self.assertEqual(self.naaz.command_pools['strategic'], 0)
        self.assertEqual(tile.command_tokens, before)
        self.assertEqual(self.turn.actions_used, 1)
        with self.assertRaises(ValueError):
            c.start_hero_secondary(self.naaz, 4)
        with self.assertRaises(ValueError):
            c.start_hero_secondary(self.naaz, 7)

    def test_warfare_production_does_not_spend_strategy_token(self):
        c = self.controller
        self.naaz.command_pools['strategic'] = 0
        c.start_hero_secondary(self.naaz, 6)
        c.accept_secondary()
        tile, dock = c.home_docks()[0]
        c.produce_at(dock.unit_id)
        self.assertEqual(self.naaz.command_pools['strategic'], 0)
        self.assertEqual(c.session.stage, 'production')
        self.movement.skip_production()
        c.poll()
        self.assertIsNone(c.session)
        self.assertEqual(self.turn.actions_used, 1)


class CabalRiftmeldTests(unittest.TestCase):
    def test_matching_captured_unit_ignores_all_upgrade_prerequisites(self):
        config, board = load_board(ROOT / 'maps/three_player.json',
                                   ('cabal', 'sol', 'hacan'))
        players = create_players(board, config)
        cabal = players[0]
        cabal.captured_units['destroyer'] = 1
        turn = TurnOrder(players, strategy_enabled=False)
        movement = MovementController(board, players)
        controller = StrategyController(board, turn, movement)
        session = StrategyResolution(7, cabal, cabal, [], stage='technology')
        controller.session = turn.strategy_resolution = session
        controller.select_technology('dd2')
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            controller.research_selected()
        self.assertEqual(cabal.captured_units['destroyer'], 1)
        controller.toggle_riftmeld()
        self.assertEqual(session.riftmeld_kind, 'destroyer')
        controller.research_selected()
        self.assertIn('dd2', cabal.technologies)
        self.assertNotIn('destroyer', cabal.captured_units)


class MuaatCommanderTests(unittest.TestCase):
    def test_strategy_secondary_spend_gains_one_trade_good(self):
        config, board = load_board(ROOT / 'maps/three_player.json',
                                   ('muaat', 'sol', 'hacan'))
        players = create_players(board, config)
        muaat, sol, _ = players
        muaat.leaders['muaatcommander'].unlocked = True
        turn = TurnOrder(players, strategy_enabled=False)
        movement = MovementController(board, players)
        controller = StrategyController(board, turn, movement)
        session = StrategyResolution(5, sol, muaat, [], primary=False, stage='offer')
        controller.session = turn.strategy_resolution = session
        tokens = muaat.command_pools['strategic']
        goods = muaat.trade_goods
        controller.accept_secondary()
        self.assertEqual(muaat.command_pools['strategic'], tokens - 1)
        self.assertEqual(muaat.trade_goods, goods + 1)


if __name__ == '__main__':
    unittest.main()
