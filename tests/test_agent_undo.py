from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app import BoardWindow
from board import ROOT, load_board
from leaders import leader_available
from movement import MovementController
from player import PlanetCard, create_players


class AgentUndoTests(unittest.TestCase):
    def test_acamar_reaction_is_undone_with_its_parent_activation(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('empyrean', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        empyrean, sol = players[:2]
        movement = MovementController(board, players)
        target = next(tile for tile in board.values() if tile.number == 77)
        session = movement.activate(sol, target.position)
        carrier = next(ship for source in session.sources.values()
                       for ship in source.ships if ship.kind == 'carrier')
        carrier_origin = next(tile for tile in board.values()
                              if carrier in tile.units)
        session.toggle(carrier.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'empyrean_agent')

        window = object.__new__(BoardWindow)
        window.movement = movement
        window.board = board
        window.turn_order = SimpleNamespace(active_player=sol)
        window.player_panel = SimpleNamespace(players=players)
        window.action_cards = None
        window.exploration = None
        window.leader_panel = SimpleNamespace(
            reaction_owner_faction='empyrean', close=Mock())
        window.movement_error = None

        window.resolve_leader_action(('use', 'empyreanagent', 'fleet'))
        self.assertEqual(len(movement.history), 0)
        self.assertNotEqual(session.stage, 'empyrean_agent')
        movement.establish_control()
        self.assertIsNone(movement.session)
        self.assertEqual(len(movement.history), 1)

        self.assertTrue(movement.undo())
        self.assertIsNone(movement.session)
        self.assertNotIn('sol', target.command_tokens)
        self.assertIn(carrier, carrier_origin.units)
        self.assertNotIn(carrier, target.units)
        self.assertTrue(empyrean.leaders['empyreanagent'].unlocked)
        self.assertFalse(empyrean.leaders['empyreanagent'].exhausted)
        self.assertEqual(sol.command_pools, {
            'tactical': 3, 'fleet': 3, 'strategic': 2})

    def test_argent_standalone_strategy_production_can_undo_only_its_agent_reaction(self):
        config, board = load_board(
            ROOT / 'maps/four_player.json',
            ('argent', 'sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        argent, sol = players[:2]
        movement = MovementController(board, players)
        home = next(tile for tile in board.values()
                    if any(unit.owner == 'sol' and unit.kind == 'spacedock'
                           for unit in tile.units))
        dock = next(unit for unit in home.units
                    if unit.owner == 'sol' and unit.kind == 'spacedock')
        origin_planet = next(planet for planet in home.planets
                             if planet.planet_id == dock.location.planet_id)
        adjacent = next(tile for tile in movement.neighbors(home)
                        if tile.planets and not any(p.faction_homeworld for p in tile.planets))
        destination = adjacent.planets[0]
        adjacent.planet_owners[destination.planet_id] = sol.faction
        sol.planets.append(PlanetCard(destination))
        sol.trade_goods = 2
        session = movement.start_strategy_production(sol, home, dock)
        session.production_choices = {'infantry': 1}
        session.trade_goods_to_spend = 1
        movement.produce()
        self.assertEqual(session.stage, 'argent_agent')
        produced_id = session.argent_agent_produced_ids[0]
        session.argent_agent_destinations[produced_id] = destination.planet_id

        window = object.__new__(BoardWindow)
        window.movement = movement
        window.board = board
        window.turn_order = SimpleNamespace(active_player=sol)
        window.player_panel = SimpleNamespace(players=players)
        window.action_cards = None
        window.exploration = None
        window.leader_panel = SimpleNamespace(
            reaction_owner_faction='argent', close=Mock())
        window.movement_error = None

        window.resolve_leader_action(('use', 'argentagent'))

        self.assertIsNone(movement.session)
        self.assertEqual(len(movement.history), 1)
        self.assertFalse(leader_available(argent, 'argentagent'))
        self.assertTrue(any(unit.unit_id == produced_id for unit in adjacent.units))
        self.assertTrue(movement.undo())
        self.assertEqual(len(movement.history), 0)
        self.assertTrue(leader_available(argent, 'argentagent'))
        self.assertTrue(any(unit.unit_id == produced_id and
                            unit.location.planet_id == origin_planet.planet_id
                            for unit in home.units))
        self.assertFalse(any(unit.unit_id == produced_id for unit in adjacent.units))


if __name__ == '__main__':
    unittest.main()
