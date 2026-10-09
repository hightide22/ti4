"""A defending bot may announce and complete a legal space retreat."""

from unittest import TestCase
from unittest.mock import patch

from board import load_board
from movement import MovementController, MovementError
from player import create_players
from units import Region, Unit, UnitLocation


class DefenderRetreatTests(TestCase):
    def test_defending_faction_retreats_after_one_combat_round(self):
        config, board = load_board()
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        controller = MovementController(board, players)
        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'destroyer' for unit in tile.units))
        target = next(tile for tile in controller.neighbors(home) if not tile.command_tokens)
        destination = next(tile for tile in controller.neighbors(target)
                           if tile is not home and not tile.units)

        attacker = next(unit for unit in home.units if
                        unit.owner == 'sol' and unit.kind == 'destroyer')
        defender = Unit('retreating-hacan-cruiser', 'cruiser', hacan.faction,
                        hacan.color_code, UnitLocation(Region.SPACE))
        receiving_ship = Unit('receiving-hacan-cruiser', 'cruiser', hacan.faction,
                              hacan.color_code, UnitLocation(Region.SPACE))
        target.units.append(defender)
        destination.units.append(receiving_ship)

        session = controller.activate(sol, target.position)
        session.toggle(attacker.unit_id)
        controller.confirm()
        self.assertEqual(session.stage, 'space_combat')
        self.assertIn(destination, controller.retreat_options(session, hacan.faction))
        with self.assertRaisesRegex(MovementError, 'not participating'):
            controller.announce_retreat('jolnar')

        controller.announce_retreat(hacan.faction)
        self.assertEqual(session.retreat_announced, hacan.faction)
        with patch('movement.random.randint', return_value=1):
            controller.advance_combat()
        controller.advance_combat()
        self.assertEqual(session.stage, 'retreat_selection')

        controller.resolve_retreat(destination.position)
        self.assertNotIn(defender, target.units)
        self.assertIn(defender, destination.units)
        self.assertIn(receiving_ship, destination.units)
        self.assertIn(hacan.faction, destination.command_tokens)
        self.assertTrue(session.space_combat_resolved)
