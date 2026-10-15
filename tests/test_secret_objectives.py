"""Secret-objective draws without the yet-unimplemented scoring flow."""
import random
import unittest

from board import Board, TILES
from exploration import ExplorationController
from player import PlayerState
from secret_objectives import SecretObjectiveController, secret_objective_catalog
from units import Region, Unit, UnitLocation


class SecretObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.sol = PlayerState('sol', 'Sol', 'blu', 4)
        self.hacan = PlayerState('hacan', 'Hacan', 'org', 6)

    def test_base_and_pok_catalog_and_hidden_hands(self):
        self.assertEqual(40, len(secret_objective_catalog()))
        deck = SecretObjectiveController([self.sol, self.hacan],
                                         deck=['ans', 'btgk', 'ctr'])
        deck.deal_starting_hands()
        self.assertEqual(('ctr',), tuple(card.alias for card in deck.hand_for('sol')))
        self.assertEqual(('btgk',), tuple(card.alias for card in deck.hand_for('hacan')))
        self.assertNotEqual(deck.hand_for('sol'), deck.hand_for('hacan'))
        deck.deal_starting_hands()
        self.assertEqual(1, len(deck.hand_for('sol')))

    def test_limit_counts_scored_and_unscored_and_returns_to_shuffled_deck(self):
        deck = SecretObjectiveController([self.sol], rng=random.Random(2),
                                         deck=['ans', 'btgk', 'ctr', 'csl'])
        self.sol.scored_secret_objectives = 1
        deck.draw(self.sol, 3)
        self.assertEqual(3, len(deck.hand_for('sol')))
        self.assertEqual(1, deck.pending_returns['sol'])
        with self.assertRaises(ValueError):
            deck.draw(self.sol)
        returned = deck.hand_for('sol')[0].alias
        deck.return_unscored('sol', returned)
        self.assertEqual(2, len(deck.hand_for('sol')))
        self.assertIn(returned, deck.deck)
        self.assertNotIn('sol', deck.pending_returns)

    def test_derelict_vessel_draws_into_owners_secret_hand(self):
        number = next(number for number, definition in TILES.by_number.items()
                      if not definition.planets)
        tile = TILES.create(number, (0, 0))
        board = Board([tile])
        tile.units.append(Unit('derelict-ship', 'cruiser', self.sol.faction,
                               self.sol.color_code, UnitLocation(Region.SPACE)))
        exploration = ExplorationController(board, [self.sol, self.hacan],
                                            decks={'cultural': [], 'hazardous': [],
                                                   'industrial': [], 'frontier': ['dv1']},
                                            relic_deck=[], secret_objective_deck=['ans'])

        result = exploration.explore_frontier(self.sol, tile, auto_resolve=True)

        self.assertEqual('dv1', result.card.alias)
        self.assertIn({'type': 'draw_secret_objective', 'count': 1}, result.events)
        self.assertEqual(('ans',), tuple(card.alias for card in
                                        exploration.secret_objectives.hand_for('sol')))
        self.assertEqual((), exploration.secret_objectives.hand_for('hacan'))

    def test_snapshot_restores_secret_hands_and_pending_returns(self):
        deck = SecretObjectiveController([self.sol], deck=['ans', 'btgk', 'ctr', 'csl'])
        state = deck.snapshot()
        deck.draw(self.sol, 4)
        self.assertEqual(1, deck.pending_returns['sol'])
        deck.restore(state)
        self.assertEqual((), deck.hand_for('sol'))
        self.assertFalse(deck.pending_returns)
        self.assertEqual(4, len(deck.deck))


if __name__ == '__main__':
    unittest.main()
