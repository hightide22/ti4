import unittest

from board import load_board
from player import PlanetCard, create_players, command_tokens_in_play
from movement import MovementController
from strategic_action import StrategyController
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
        other_planet = next(p for p in home.planets if p.planet_id != dock.location.planet_id)
        home.units.append(Unit('second-hacan-dock', 'spacedock', 'hacan', self.hacan.color_code,
                               UnitLocation(Region.PLANET, other_planet.planet_id)))
        c.produce_at(dock.unit_id)
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

    def test_diplomacy_readies_exhausted_planet_in_any_controlled_system(self):
        self.start_card(2, owner=1)
        for card in self.hacan.planets:
            card.exhausted = True
        c = self.controller
        selected_system = next(t for t in self.board.values() if 'hacan' in t.planet_owners.values())
        remote_system = next(t for t in self.board.values()
                             if t.position != selected_system.position and t.planets and not t.planet_owners)
        chosen = PlanetCard(remote_system.planets[0], exhausted=True)
        self.hacan.planets.append(chosen)
        remote_system.planet_owners[chosen.planet.planet_id] = self.hacan.faction
        c.select_system(selected_system.position)
        self.assertEqual(c.session.stage, 'ready_planets')
        c.toggle_ready(self.sol.planets[0].planet.planet_id)
        self.assertFalse(c.session.ready_planets, 'A planet controlled by another player must not be selectable.')
        c.toggle_ready(chosen.planet.planet_id)
        self.assertEqual(c.session.ready_planets, {chosen.planet.planet_id})
        c.confirm_ready()
        self.assertFalse(chosen.exhausted)
        self.assertTrue(all(p.exhausted for p in self.hacan.planets if p is not chosen))
        self.assertEqual(selected_system.command_tokens, {'sol', 'jolnar'})

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


if __name__ == '__main__':
    unittest.main()
