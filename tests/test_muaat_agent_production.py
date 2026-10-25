"""Umbat uses the production rules with its specific limits and placements."""

import unittest

from board import ROOT, load_board
from leader_effects import muaat_agent_systems, use_muaat_agent
from leaders import leader_available
from movement import MovementController, MovementError, Snapshot
from player import create_players
from units import Region, Unit, UnitLocation


class MuaatAgentProductionTests(unittest.TestCase):
    def setUp(self):
        config, self.board = load_board(ROOT / 'maps/four_player.json',
                                        ('muaat', 'sol', 'jolnar', 'hacan'))
        self.players = create_players(self.board, config)
        self.muaat = next(player for player in self.players if player.faction == 'muaat')
        self.sol = next(player for player in self.players if player.faction == 'sol')
        self.movement = MovementController(self.board, self.players)
        self.target = next(tile for tile in self.board.values() if tile.number == 18)
        self.target.units.append(Unit('sol-umbat-flagship', 'flagship', 'sol',
                                      self.sol.color_code, UnitLocation(Region.SPACE)))

    def test_umbat_selects_eligible_system_and_exhausts_once(self):
        self.assertIn(self.target, muaat_agent_systems(self.sol, self.board))
        self.assertTrue(leader_available(self.muaat, 'muaatagent'))

        use_muaat_agent(self.muaat, self.sol, self.target)

        self.assertFalse(leader_available(self.muaat, 'muaatagent'))
        with self.assertRaisesRegex(ValueError, 'not ready'):
            use_muaat_agent(self.muaat, self.sol, self.target)

    def test_umbat_caps_cost_disables_production_discounts_and_places_ground_forces_in_space(self):
        self.sol.technologies = self.sol.technologies | {'st', 'aida'}
        session = self.movement.start_agent_production(
            self.sol, self.target, Snapshot.capture(self.board, self.muaat, self.players))
        self.assertTrue(session.agent_production)
        self.assertEqual(session.production_limit, 2)
        self.assertEqual(session.production_unit_cost_cap, 4)
        self.assertEqual(self.movement.production_sites(session), [])
        self.assertEqual(self.movement.production_cost(session), 0)

        with self.assertRaisesRegex(MovementError, 'cost of 4 or less'):
            self.movement.adjust_production('flagship', 1)
        with self.assertRaisesRegex(MovementError, 'AI Development Algorithm'):
            self.movement.toggle_ai_development_discount()

        self.movement.adjust_production('infantry', 1)
        self.assertEqual(session.production_choices['infantry'], 2)
        self.movement.adjust_production('infantry', -1)
        self.assertEqual(session.production_choices['infantry'], 1)
        self.assertEqual(self.movement.production_cost(session), 1)
        self.sol.trade_goods = 1
        self.movement.change_production_trade_goods(1)
        self.movement.produce()

        produced = [unit for unit in self.target.units
                    if unit.owner == 'sol' and unit.kind == 'infantry' and
                    unit.unit_id.startswith('sol-built-')]
        self.assertEqual(len(produced), 1)
        self.assertEqual(produced[0].location, UnitLocation(Region.SPACE_AREA))
        self.assertIsNone(self.movement.session)

    def test_umbat_rejects_a_player_system_without_their_war_sun_or_flagship(self):
        self.target.units[:] = [unit for unit in self.target.units
                                if unit.unit_id != 'sol-umbat-flagship']
        self.assertNotIn(self.target, muaat_agent_systems(self.sol, self.board))
        with self.assertRaisesRegex(ValueError, 'War Sun or flagship'):
            use_muaat_agent(self.muaat, self.sol, self.target)
        self.assertTrue(leader_available(self.muaat, 'muaatagent'))


if __name__ == '__main__':
    unittest.main()
