import unittest

from turn_order import TurnOrder


class TurnOrderTests(unittest.TestCase):
    def test_players_take_turns_in_order_and_cycle(self):
        players = [object(), object(), object()]
        turns = TurnOrder(players)

        self.assertIs(turns.active_player, players[0])
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[1])
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[2])
        self.assertTrue(turns.end_turn())
        self.assertIs(turns.active_player, players[0])
        self.assertEqual(turns.round_number, 2)
        self.assertFalse(turns.passed_indices)

    def test_one_completed_action_per_turn_resets_when_turn_passes(self):
        turns = TurnOrder([object(), object()])

        self.assertTrue(turns.mark_action_completed())
        self.assertFalse(turns.mark_action_completed())
        turns.end_turn()
        self.assertFalse(turns.action_used)
        self.assertTrue(turns.mark_action_completed())

    def test_players_who_passed_are_skipped_until_round_ends(self):
        players = [object(), object(), object()]
        turns = TurnOrder(players)

        turns.end_turn()
        turns.end_turn()
        self.assertIs(turns.active_player, players[2])
        self.assertNotIn(turns.active_index, turns.passed_indices)
        self.assertTrue(turns.end_turn())
        self.assertEqual(turns.round_number, 2)

    def test_cannot_end_turn_with_unallocated_commands(self):
        player = type('Player', (), {'pending_commands': 1})()
        turns = TurnOrder([player])

        with self.assertRaises(ValueError):
            turns.end_turn()


if __name__ == '__main__':
    unittest.main()
