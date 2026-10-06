import unittest

from turn_order import TurnOrder


class TurnOrderTests(unittest.TestCase):
    def test_players_take_turns_in_order_and_cycle(self):
        players = [object(), object(), object()]
        turns = TurnOrder(players)

        self.assertIs(turns.active_player, players[0])
        self.assertIs(turns.pass_turn(), players[1])
        self.assertIs(turns.pass_turn(), players[2])
        self.assertIs(turns.pass_turn(), players[0])
        self.assertEqual(turns.turn_number, 4)

    def test_one_completed_action_per_turn_resets_when_turn_passes(self):
        turns = TurnOrder([object(), object()])

        self.assertTrue(turns.mark_action_completed())
        self.assertFalse(turns.mark_action_completed())
        turns.pass_turn()
        self.assertFalse(turns.action_used)
        self.assertTrue(turns.mark_action_completed())


if __name__ == '__main__':
    unittest.main()
