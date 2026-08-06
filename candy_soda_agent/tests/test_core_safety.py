from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from config import _env_bool
from coordinate_mapper import Point, cell_center
from reward import evaluate_reward
from safety_guard import cancellation_reason, validate_fresh_board


class ConfigTests(unittest.TestCase):
    def test_invalid_boolean_fails_closed(self) -> None:
        with patch.dict(os.environ, {"TEST_FLAG": "tru"}):
            with self.assertRaises(ValueError):
                _env_bool("TEST_FLAG", True)


class CoordinateMapperTests(unittest.TestCase):
    def test_maps_first_and_last_cell_centers(self) -> None:
        region = {"left": 100, "top": 200, "width": 100, "height": 90}
        self.assertEqual(cell_center(1, 1, region, 9, 10), Point(105, 205))
        self.assertEqual(cell_center(9, 10, region, 9, 10), Point(195, 285))

    def test_rejects_out_of_range_cell(self) -> None:
        region = {"left": 0, "top": 0, "width": 100, "height": 100}
        with self.assertRaises(ValueError):
            cell_center(0, 1, region, 9, 10)


class SafetyGuardTests(unittest.TestCase):
    def test_exit_cancels_manual_action(self) -> None:
        exit_requested = Event()
        exit_requested.set()
        self.assertIsNotNone(cancellation_reason("manual", exit_requested, Event()))

    def test_stopped_auto_mode_cancels_action(self) -> None:
        self.assertIsNotNone(cancellation_reason("auto", Event(), Event()))

    def test_fresh_board_requires_both_comparisons(self) -> None:
        self.assertTrue(validate_fresh_board(0.01, 0.01, 0.02)[0])
        self.assertFalse(validate_fresh_board(0.03, 0.01, 0.02)[0])
        self.assertFalse(validate_fresh_board(0.01, 0.03, 0.02)[0])


class RewardTests(unittest.TestCase):
    def test_changed_screen_gets_positive_reward(self) -> None:
        result = evaluate_reward(
            action="swap",
            validation_passed=True,
            executed=True,
            dry_run=False,
            screen_change_score=0.04,
            success_threshold=0.025,
        )
        self.assertEqual(result.reward, 1.0)
        self.assertEqual(result.success_estimate, "positive")

    def test_safety_block_is_not_negative_learning(self) -> None:
        result = evaluate_reward(
            action="swap",
            validation_passed=True,
            executed=False,
            dry_run=False,
            screen_change_score=None,
            success_threshold=0.025,
            blocked_reason="자동 모드 중지",
        )
        self.assertEqual(result.reward, 0.0)
        self.assertEqual(result.success_estimate, "unknown")


if __name__ == "__main__":
    unittest.main()
