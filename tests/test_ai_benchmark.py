"""The acceptance harness must be reproducible and fail on stalled decisions."""
import random
import unittest
from unittest.mock import patch

from scripts.ai_benchmark import run_match


class AIBenchmarkTests(unittest.TestCase):
    def test_seed_replays_real_match_and_restores_global_rng(self):
        before = random.getstate()
        first = run_match(count=3, rounds=2, seed=41)
        self.assertEqual(random.getstate(), before)
        second = run_match(count=3, rounds=2, seed=41)
        self.assertEqual(first['status'], 'passed', first.get('error'))
        self.assertEqual(second['status'], 'passed', second.get('error'))
        for result in (first, second):
            result.pop('wall_seconds')
            result.pop('decision_seconds')
        self.assertEqual(first, second)
        self.assertEqual(len(first['rounds']), 2)
        self.assertTrue(all(first['final'][faction]['planets'] > initial['planets']
                            for faction, initial in first['initial'].items()))

    def test_no_op_loop_reports_precise_phase_instead_of_hanging(self):
        with patch('scripts.ai_benchmark.ObservedAI.step', return_value=None):
            result = run_match(rounds=1, seed=5, stall_steps=3)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('No game-state change', result['error'])
        self.assertTrue(result['state']['draft'])
        self.assertEqual(result['state']['draft_cursor'], 0)
        self.assertEqual(result['steps'], 4)


if __name__ == '__main__':
    unittest.main()
