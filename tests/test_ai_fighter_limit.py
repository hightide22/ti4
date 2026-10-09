"""Tactical forecasts must account for Fighter II fleet-supply overflow."""

import unittest
from types import SimpleNamespace

from ai_combat import win_probability
from board import load_board
from game_ai import GameAI
from movement import MovementController, MovementError
from player import create_players
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class FighterLimitTests(unittest.TestCase):
    def test_full_carrier_cannot_bring_fighter_iis_as_free_escort(self):
        config, board = load_board()
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        sol.technologies = frozenset(set(sol.technologies) | {'ff2'})
        sol.command_pools['fleet'] = 2

        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in board.neighbors(home.position)
                      if tile.name == 'Thibah')
        carrier = next(unit for unit in home.units if
                       unit.owner == 'sol' and unit.kind == 'carrier')
        destroyer = next(unit for unit in home.units if
                         unit.owner == 'sol' and unit.kind == 'destroyer')
        cruiser = Unit('ai-limit-cruiser', 'cruiser', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE))
        fighters = [Unit(f'ai-limit-fighter-{index}', 'fighter', 'sol', sol.color_code,
                         UnitLocation(Region.SPACE), profile_id='fighter2')
                    for index in range(2)]
        infantry = [Unit(f'ai-limit-infantry-{index}', 'infantry', 'sol', sol.color_code,
                         UnitLocation(Region.TRANSPORT, carrier_id=carrier.unit_id))
                    for index in range(carrier.capacity)]
        home.units[:] = [carrier, destroyer, cruiser, *fighters, *infantry]

        enemies = [Unit('ai-limit-enemy-cruiser', 'cruiser', 'hacan',
                        hacan.color_code, UnitLocation(Region.SPACE)),
                   Unit('ai-limit-enemy-fighter', 'fighter', 'hacan',
                        hacan.color_code, UnitLocation(Region.SPACE),
                        profile_id='fighter2')]
        target.units[:] = enemies
        movement = MovementController(board, players)
        window = SimpleNamespace(board={home.position: home, target.position: target},
                                 movement=movement, turn_order=TurnOrder(players))
        ai = GameAI(window, {'sol'})

        safe_fleet = [carrier, cruiser]
        unsafe_fleet = safe_fleet + fighters
        self.assertLess(win_probability(safe_fleet, enemies, players), .80)
        self.assertGreater(win_probability(unsafe_fleet, enemies, players), .80)
        self.assertIsNone(ai.best_activation(sol))

        # The attractive four-ship forecast is illegal: the loaded carrier has
        # no room for Fighter II, so both fighters exceed the two fleet slots.
        session = movement.activate(sol, target.position)
        for ship in unsafe_fleet:
            session.toggle(ship.unit_id)
        movement.confirm()
        self.assertEqual(session.stage, 'fleet_overflow')
        self.assertEqual(session.overflow_required, 2)

    def test_fighter_ii_loaded_as_passenger_still_uses_gravity_drive(self):
        config, board = load_board()
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        hacan = next(player for player in players if player.faction == 'hacan')
        sol.technologies = frozenset(set(sol.technologies) | {'cv2', 'ff2', 'gd'})

        home = next(tile for tile in board.values() if any(
            unit.owner == 'sol' and unit.kind == 'carrier' for unit in tile.units))
        target = next(tile for tile in board.values()
                      if tile.name == 'Starpoint/New Albion')
        carrier = next(unit for unit in home.units if
                       unit.owner == 'sol' and unit.kind == 'carrier')
        carrier.profile_id = 'sol_carrier2'
        fighter = Unit('gravity-fighter-ii', 'fighter', 'sol', sol.color_code,
                       UnitLocation(Region.SPACE), profile_id='fighter2')
        infantry = [unit for unit in home.units if
                    unit.owner == 'sol' and unit.kind == 'infantry']
        home.units[:] = [carrier, fighter, *infantry]
        target.units[:] = [Unit('gravity-enemy-destroyer', 'destroyer', 'hacan',
                              hacan.color_code, UnitLocation(Region.SPACE))]

        movement = MovementController(board, players)
        window = SimpleNamespace(board={home.position: home, target.position: target},
                                 movement=movement, turn_order=TurnOrder(players))
        ai = GameAI(window, {'sol'})
        self.assertIsNone(movement.route(home, target, carrier, sol))
        self.assertIsNone(movement.route(home, target, fighter, sol))
        self.assertIsNotNone(movement.route(home, target, carrier, sol, bonus=1))
        self.assertIsNotNone(movement.route(home, target, fighter, sol, bonus=1))

        # Both need the same once-per-action boost. The fighter's use of
        # Gravity Drive still counts if the planner loads it as a passenger.
        self.assertIsNone(ai.best_activation(sol))
        session = movement.activate(sol, target.position)
        self.assertIn(carrier.unit_id, session.gravity_bonus_ids)
        self.assertIn(fighter.unit_id, session.gravity_bonus_ids)
        session.toggle(carrier.unit_id)
        with self.assertRaisesRegex(MovementError, 'Gravity Drive can boost only one ship'):
            session.toggle(fighter.unit_id)


if __name__ == '__main__':
    unittest.main()
