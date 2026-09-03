"""config/coordinate_mapper/safety_guard/reward/cli의 핵심 안전 로직 단위 테스트입니다."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import cli
from config import _env_bool
from coordinate_mapper import Point, cell_center
from reward import classify_action_outcome, evaluate_reward
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
            action_outcome="accepted",
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
            blocked_reason="자동 모드 중지",
        )
        self.assertEqual(result.reward, 0.0)
        self.assertEqual(result.success_estimate, "unknown")

    def test_returned_board_is_rejected(self) -> None:
        observation = classify_action_outcome(
            peak_change=0.04,
            final_change=0.002,
            settled=True,
            accepted_threshold=0.025,
            attempt_threshold=0.01,
            returned_threshold=0.008,
        )
        self.assertEqual(observation.status, "rejected")

    def test_changed_final_board_is_accepted(self) -> None:
        observation = classify_action_outcome(
            peak_change=0.06,
            final_change=0.04,
            settled=True,
            accepted_threshold=0.025,
            attempt_threshold=0.01,
            returned_threshold=0.008,
        )
        self.assertEqual(observation.status, "accepted")

    def test_execution_error_does_not_create_game_rule_penalty(self) -> None:
        result = evaluate_reward(
            action="swap",
            validation_passed=True,
            executed=False,
            dry_run=False,
            screen_change_score=None,
            execution_error="focus failed",
        )
        self.assertEqual(result.reward, 0.0)

    def test_unclassified_outcome_is_held_instead_of_guessed(self) -> None:
        result = evaluate_reward(
            action="swap",
            validation_passed=True,
            executed=True,
            dry_run=False,
            screen_change_score=0.04,
            action_outcome="something_new",
        )
        self.assertEqual(result.reward, 0.0)
        self.assertEqual(result.success_estimate, "unknown")


class AutodriveTapPolicyTests(unittest.TestCase):
    """--autodrive 도 조사 모드 탭 안전장치를 그대로 따라야 합니다."""

    def _args(self, **overrides) -> argparse.Namespace:
        """기본 CLI 플래그로 채운 Namespace를 만들고 overrides로 덮어씁니다."""

        args = argparse.Namespace(
            once=False,
            auto=False,
            survey_once=False,
            survey_auto=False,
            survey_report=False,
            survey_taps=False,
            hotkeys=False,
            autodrive=False,
            autodrive_steps=40,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_autodrive_alone_does_not_enable_taps(self) -> None:
        with patch.object(cli, "SURVEY_ALLOW_TAPS", False):
            plan = cli.build_run_plan(self._args(autodrive=True))
        self.assertFalse(plan.autodrive_taps)

    def test_autodrive_and_survey_auto_agree_on_taps(self) -> None:
        with patch.object(cli, "SURVEY_ALLOW_TAPS", False):
            autodrive = cli.build_run_plan(self._args(autodrive=True))
            survey_auto = cli.build_run_plan(self._args(survey_auto=True))
        self.assertEqual(autodrive.autodrive_taps, survey_auto.autodrive_taps)

    def test_explicit_flag_enables_taps(self) -> None:
        with patch.object(cli, "SURVEY_ALLOW_TAPS", False):
            plan = cli.build_run_plan(
                self._args(autodrive=True, survey_taps=True)
            )
        self.assertTrue(plan.autodrive_taps)

    def test_env_setting_enables_taps(self) -> None:
        with patch.object(cli, "SURVEY_ALLOW_TAPS", True):
            plan = cli.build_run_plan(self._args(autodrive=True))
        self.assertTrue(plan.autodrive_taps)


if __name__ == "__main__":
    unittest.main()
