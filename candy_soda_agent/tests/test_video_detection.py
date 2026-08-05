from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from config import VIDEO_PATH  # noqa: E402
from schemas import VideoOutcomeLabel  # noqa: E402
from usage import ApiUsage, UsageBudget  # noqa: E402
from video_detection import (  # noqa: E402
    SwapCandidate,
    detect_swap_candidate,
)
from video_pipeline import (  # noqa: E402
    process_event,
    run_video,
)


def paint_cell(
    image: np.ndarray,
    row: int,
    col: int,
    value: int,
) -> None:
    height, width = image.shape[:2]
    top = round((row - 1) * height / 9)
    bottom = round(row * height / 9)
    left = round((col - 1) * width / 9)
    right = round(col * width / 9)
    image[top:bottom, left:right] = value


def candidate_arguments() -> dict[str, Any]:
    return {
        "rows": 9,
        "cols": 9,
        "margin_ratio": 0.08,
        "active_threshold": 0.02,
        "min_pair_sum": 0.20,
        "min_second_cell_change": 0.05,
        "min_dominance": 0.65,
        "max_active_cells": 2,
        "max_global_difference": 1.0,
    }


def make_candidate() -> SwapCandidate:
    return SwapCandidate(
        source_row=4,
        source_col=4,
        target_row=4,
        target_col=5,
        pair_sum=0.8,
        second_cell_change=0.35,
        dominance=0.9,
        active_cells=2,
        global_difference=0.005,
    )


class SwapCandidateTests(unittest.TestCase):
    def test_detects_adjacent_swap(self) -> None:
        before = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )
        paint_cell(before, 4, 4, 40)
        paint_cell(before, 4, 5, 220)
        after = before.copy()
        paint_cell(after, 4, 4, 220)
        paint_cell(after, 4, 5, 40)

        candidate = detect_swap_candidate(
            before,
            after,
            **candidate_arguments(),
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(
            candidate.normalized_pair,
            ((4, 4), (4, 5)),
        )

    def test_rejects_non_adjacent_and_broad_changes(
        self,
    ) -> None:
        before = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )
        paint_cell(before, 1, 1, 40)
        paint_cell(before, 3, 3, 220)
        non_adjacent = before.copy()
        paint_cell(non_adjacent, 1, 1, 220)
        paint_cell(non_adjacent, 3, 3, 40)

        self.assertIsNone(
            detect_swap_candidate(
                before,
                non_adjacent,
                **candidate_arguments(),
            )
        )

        blank = np.zeros_like(before)
        broad = blank.copy()
        paint_cell(broad, 4, 4, 220)
        paint_cell(broad, 4, 5, 220)
        paint_cell(broad, 4, 6, 220)

        self.assertIsNone(
            detect_swap_candidate(
                blank,
                broad,
                **candidate_arguments(),
            )
        )


class EventProcessingTests(unittest.TestCase):
    def test_low_outcome_confidence_still_saves_local_action(
        self,
    ) -> None:
        board = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )
        score = np.zeros(
            (30, 60, 3),
            dtype=np.uint8,
        )
        outcome = VideoOutcomeLabel(
            move_type="unknown",
            board_summary="결과 유형은 불명확함",
            score_before=0,
            score_after=500,
            confidence=0.0,
            reason="결과 분류 확신도가 낮음",
        )
        budget = UsageBudget(
            max_calls=4,
            max_total_tokens=10_000,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture_dir = root / "captures"
            discard_path = root / "discards.jsonl"
            experience_path = root / "experiences.jsonl"

            with (
                patch(
                    "video_pipeline.VIDEO_CAPTURE_DIR",
                    capture_dir,
                ),
                patch(
                    "video_pipeline.VIDEO_DISCARD_PATH",
                    discard_path,
                ),
                patch(
                    "video_pipeline.VIDEO_EXPERIENCE_PATH",
                    experience_path,
                ),
                patch(
                    "video_pipeline.label_video_outcome",
                    return_value=(
                        outcome,
                        ApiUsage(
                            input_tokens=100,
                            output_tokens=20,
                            total_tokens=120,
                        ),
                    ),
                ),
            ):
                stored = process_event(
                    client=cast(Any, object()),
                    budget=budget,
                    mode="train",
                    before_board=board,
                    before_score=score,
                    action_board=board,
                    action_score=score,
                    after_board=board,
                    after_score=score,
                    timestamp=10.0,
                    action_timestamp=10.05,
                    candidate=make_candidate(),
                    memory_limit=None,
                    experiment_label="discard_test",
                )

            self.assertTrue(stored)
            self.assertEqual(budget.calls, 1)
            self.assertTrue(experience_path.exists())
            self.assertFalse(discard_path.exists())

            record = json.loads(
                experience_path.read_text(
                    encoding="utf-8"
                ).strip()
            )
            self.assertEqual(
                record["action_source"],
                "local_detector",
            )
            self.assertEqual(
                record["api_usage"]["total_tokens"],
                120,
            )
            self.assertEqual(record["score_delta"], 500)
            self.assertEqual(record["outcome_confidence"], 0.0)
            for path in record["images"].values():
                self.assertTrue(Path(path).exists())

    def test_local_action_saves_training_experience(
        self,
    ) -> None:
        board = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )
        score = np.zeros(
            (30, 60, 3),
            dtype=np.uint8,
        )
        outcome = VideoOutcomeLabel(
            move_type="normal_match",
            board_summary="인접한 두 사탕을 교환함",
            score_before=100,
            score_after=220,
            confidence=0.95,
            reason="로컬 후보와 같은 두 칸이 움직임",
        )
        budget = UsageBudget(
            max_calls=4,
            max_total_tokens=10_000,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            experience_path = root / "experiences.jsonl"

            with (
                patch(
                    "video_pipeline.VIDEO_CAPTURE_DIR",
                    root / "captures",
                ),
                patch(
                    "video_pipeline.VIDEO_DISCARD_PATH",
                    root / "discards.jsonl",
                ),
                patch(
                    "video_pipeline.VIDEO_EXPERIENCE_PATH",
                    experience_path,
                ),
                patch(
                    "video_pipeline.label_video_outcome",
                    return_value=(
                        outcome,
                        ApiUsage(
                            input_tokens=100,
                            output_tokens=20,
                            total_tokens=120,
                        ),
                    ),
                ),
            ):
                stored = process_event(
                    client=cast(Any, object()),
                    budget=budget,
                    mode="train",
                    before_board=board,
                    before_score=score,
                    action_board=board,
                    action_score=score,
                    after_board=board,
                    after_score=score,
                    timestamp=10.0,
                    action_timestamp=10.05,
                    candidate=make_candidate(),
                    memory_limit=None,
                    experiment_label="valid_test",
                )

            self.assertTrue(stored)
            record = json.loads(
                experience_path.read_text(
                    encoding="utf-8"
                ).strip()
            )
            self.assertEqual(
                record["score_delta"],
                120,
            )
            self.assertEqual(
                record["local_candidate"]["source"],
                {
                    "row": 4,
                    "col": 4,
                },
            )
            self.assertFalse(
                (root / "discards.jsonl").exists()
            )


class AttemptLimitTests(unittest.TestCase):
    @unittest.skipUnless(
        Path(VIDEO_PATH).exists(),
        "configured video is unavailable",
    )
    def test_attempt_limit_is_separate_from_saved_events(
        self,
    ) -> None:
        with (
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "test-key"},
            ),
            patch(
                "video_pipeline.OpenAI",
                return_value=object(),
            ),
            patch(
                "video_pipeline.process_event",
                return_value=False,
            ) as process_event_mock,
            patch(
                "video_pipeline.append_jsonl",
            ) as append_mock,
            patch(
                "video_pipeline.save_detector_rejection",
            ),
        ):
            run_video(
                mode="train",
                video_path=VIDEO_PATH,
                start_seconds=20.0,
                end_seconds=40.0,
                memory_limit=None,
                experiment_label="attempt_limit",
                max_events_override=40,
                max_attempts_override=3,
            )

        self.assertEqual(
            process_event_mock.call_count,
            3,
        )
        usage_record = append_mock.call_args.args[1]
        self.assertEqual(
            usage_record["attempted_events"],
            3,
        )
        self.assertEqual(
            usage_record["saved_events"],
            0,
        )
        self.assertEqual(
            usage_record["stop_reason"],
            "max_attempts_reached",
        )


if __name__ == "__main__":
    unittest.main()
