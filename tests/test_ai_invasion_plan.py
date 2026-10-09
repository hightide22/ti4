"""The invasion forecast and actual landing must target the same planet."""

import unittest
from types import SimpleNamespace

from ai_planner import _capture_value, planet_value
from board import load_board
from game_ai import GameAI
from player import create_players
from turn_order import TurnOrder
from units import Region, Unit, UnitLocation


class InvasionPlanTests(unittest.TestCase):
    def test_first_round_mellon_zohbat_landing_matches_forecast(self):
        config, board = load_board()
        players = create_players(board, config)
        sol = next(player for player in players if player.faction == 'sol')
        target = next(tile for tile in board.values()
                      if tile.name == 'Mellon/Zohbat')
        mellon = next(planet for planet in target.planets
                      if planet.name == 'Mellon')
        zohbat = next(planet for planet in target.planets
                      if planet.name == 'Zohbat')
        target.planet_owners[mellon.planet_id] = 'hacan'
        target.planet_owners[zohbat.planet_id] = None
        troop = Unit('forecast-landing', 'infantry', 'sol', sol.color_code,
                     UnitLocation(Region.TRANSPORT, carrier_id='forecast-carrier'))
        target.units.append(troop)

        turn = TurnOrder(players)
        window = SimpleNamespace(board=board, turn_order=turn)
        ai = GameAI(window, {'sol', 'jolnar'})
        self.assertEqual(ai.human_faction(), 'hacan')
        forecast, _ = _capture_value(ai, target, sol, [troop], 'hacan')
        self.assertEqual(forecast, planet_value(zohbat, None, 'hacan', 1))

        session = SimpleNamespace(player=sol, target=target,
                                  landings={troop.unit_id: None})
        ai.plan_invasion(session)
        self.assertEqual(session.landings[troop.unit_id], zohbat.planet_id)


if __name__ == '__main__':
    unittest.main()
