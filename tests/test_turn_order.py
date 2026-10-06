import unittest

from turn_order import TurnOrder


class TurnOrderTests(unittest.TestCase):
    def test_three_player_strategy_selection_uses_two_cards_and_lowest_initiative(self):
        players = [type('Player', (), {'faction': faction, 'pending_commands': 0})()
                   for faction in ('sol', 'hacan', 'jolnar')]
        turns = TurnOrder(players, strategy_enabled=True)

        # Sol is Speaker: pick order is Sol, Hacan, Jol-Nar, then repeat.
        self.assertIs(turns.active_player, players[0])
        for card in (5, 2, 1):
            turns.choose_strategy_card(card)
        self.assertIs(turns.active_player, players[0])
        for card in (4, 6, 3):
            turns.choose_strategy_card(card)
        self.assertFalse(turns.strategy_selection)
        self.assertEqual(turns.strategy_assignments['sol'], [5, 4])
        self.assertEqual(turns.strategy_assignments['hacan'], [2, 6])
        self.assertEqual(turns.strategy_assignments['jolnar'], [1, 3])
        self.assertEqual(turns.strategy_initiative, [2, 1, 0])
        self.assertIs(turns.active_player, players[2])

    def test_cannot_pass_before_using_both_strategy_cards(self):
        players = [type('Player', (), {'faction': faction, 'pending_commands': 0})()
                   for faction in ('sol', 'hacan', 'jolnar')]
        turns = TurnOrder(players, strategy_enabled=True)
        for card in (1, 2, 3, 4, 5, 6):
            turns.choose_strategy_card(card)
        with self.assertRaises(ValueError):
            turns.end_turn()
        turns.mark_strategy_used(players[0], 1)
        turns.action_used = False
        with self.assertRaises(ValueError):
            turns.end_turn()
        turns.mark_strategy_used(players[0], 4)
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[1])

    def test_secondary_cannot_be_taken_later_in_a_normal_turn(self):
        players = [type('Player', (), {'faction': faction, 'pending_commands': 0})()
                   for faction in ('sol', 'hacan', 'jolnar')]
        turns = TurnOrder(players, strategy_enabled=True)
        for card in (1, 2, 3, 4, 5, 6):
            turns.choose_strategy_card(card)
        self.assertFalse(turns.can_use_secondary(players[1], 1))
        turns.mark_strategy_used(players[0], 1)
        self.assertFalse(turns.can_use_secondary(players[1], 1))
        with self.assertRaises(ValueError):
            turns.mark_secondary_used(players[1], 1)

    def test_players_take_turns_in_order_and_cycle(self):
        players = [object(), object(), object()]
        turns = TurnOrder(players)

        self.assertIs(turns.active_player, players[0])
        turns.mark_action_completed()
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[1])
        self.assertFalse(turns.passed_indices)
        turns.mark_action_completed()
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[2])
        turns.mark_action_completed()
        self.assertFalse(turns.end_turn())
        self.assertIs(turns.active_player, players[0])
        turns.mark_action_completed()
        turns.end_turn()
        turns.mark_action_completed()
        turns.end_turn()
        self.assertIs(turns.active_player, players[2])
        self.assertFalse(turns.action_used)
        self.assertFalse(turns.end_turn())  # Jol-Nar passes.
        self.assertEqual(turns.passed_indices, {2})
        self.assertIs(turns.active_player, players[0])
        turns.mark_action_completed()
        turns.end_turn()
        self.assertIs(turns.active_player, players[1])
        self.assertFalse(turns.end_turn())  # Hacan passes.
        self.assertEqual(turns.passed_indices, {1, 2})
        self.assertIs(turns.active_player, players[0])
        turns.mark_action_completed()
        self.assertFalse(turns.end_turn())  # Sol gets another turn while others are passed.
        self.assertIs(turns.active_player, players[0])
        self.assertFalse(turns.action_used)
        self.assertTrue(turns.end_turn())  # Sol passes; only now does the round end.
        self.assertEqual(turns.round_number, 2)
        self.assertFalse(turns.passed_indices)

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

    def test_all_players_allocate_round_commands_before_turns_resume(self):
        players = [type('Player', (), {'pending_commands': count})()
                   for count in (3, 2, 2)]
        turns = TurnOrder(players)

        turns.begin_command_allocation()
        self.assertTrue(turns.command_allocation)
        self.assertIs(turns.active_player, players[0])
        players[0].pending_commands = 0
        self.assertFalse(turns.finish_player_command_allocation())
        self.assertIs(turns.active_player, players[1])
        players[1].pending_commands = 0
        self.assertFalse(turns.finish_player_command_allocation())
        self.assertIs(turns.active_player, players[2])
        players[2].pending_commands = 0
        self.assertTrue(turns.finish_player_command_allocation())
        self.assertFalse(turns.command_allocation)
        self.assertIs(turns.active_player, players[0])


if __name__ == '__main__':
    unittest.main()
