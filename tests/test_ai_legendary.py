"""Bots use end-of-turn legendary abilities through the player action controller."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from action_cards import ActionCardController
from board import load_board
from game_ai import GameAI
from movement import MovementController
from player import PlanetCard, create_players
from turn_order import TurnOrder


class AILegendaryTests(unittest.TestCase):
    def test_bot_uses_ready_planets_before_ending_turn(self):
        root = Path(__file__).resolve().parents[1]
        config, board = load_board(root / 'maps/three_player.json',
                                   ('sol', 'jolnar', 'hacan'))
        players = create_players(board, config)
        sol = players[0]
        for number in (65, 66, 82):
            tile = next(tile for tile in board.values() if tile.number == number)
            planet = tile.planets[0]
            tile.planet_owners[planet.planet_id] = sol.faction
            sol.planets.append(PlanetCard(planet))

        turn = TurnOrder(players)
        turn.mark_action_completed()
        movement = MovementController(board, players)
        cards = ActionCardController(players, movement, deck=['fs1'])
        window = SimpleNamespace(
            board=board, turn_order=turn, movement=movement, action_cards=cards,
            strategy=SimpleNamespace(session=None),
            transaction=SimpleNamespace(session=None), pass_turn=Mock())
        ai = GameAI(window, {'sol'})
        before_infantry = sum(unit.owner == 'sol' and unit.kind == 'infantry'
                              for tile in board.values() for unit in tile.units)
        before_goods = sol.trade_goods

        ai.step()
        self.assertEqual(before_infantry + 2, sum(
            unit.owner == 'sol' and unit.kind == 'infantry'
            for tile in board.values() for unit in tile.units))
        self.assertIn('primor', sol.exhausted_legendary_abilities)
        window.pass_turn.assert_not_called()

        ai.step()
        self.assertTrue(any(unit.owner == 'sol' and unit.kind == 'mech'
                            for tile in board.values() for unit in tile.units))
        self.assertIn('hopesend', sol.exhausted_legendary_abilities)
        window.pass_turn.assert_not_called()

        ai.step()
        self.assertEqual(before_goods + 2, sol.trade_goods)
        self.assertIn('mallice', sol.exhausted_legendary_abilities)
        window.pass_turn.assert_not_called()

        ai.step()
        window.pass_turn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
