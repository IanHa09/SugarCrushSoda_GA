from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from agent import (  # noqa: E402
    analyze_board,
    label_video_outcome,
    model_supports_reasoning,
    validate_decision,
    validate_transition_label,
)
from config import VIDEO_PATH  # noqa: E402
from image_utils import (  # noqa: E402
    add_grid_overlay,
    crop_roi,
    image_to_data_url,
    make_swap_zoom,
    make_video_observation,
)
from schemas import (  # noqa: E402
    AgentDecision,
    Cell,
    VideoOutcomeLabel,
    VideoTransitionLabel,
)
from usage import (  # noqa: E402
    ApiBudgetExceeded,
    ApiUsage,
    UsageBudget,
)
from video_pipeline import (  # noqa: E402
    actions_match,
    run_video,
    validate_roi_bounds,
)


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            input_tokens_details=SimpleNamespace(
                cached_tokens=10
            ),
            output_tokens_details=SimpleNamespace(
                reasoning_tokens=0
            ),
        )
        return SimpleNamespace(
            output_parsed=output,
            usage=usage,
        )


class FakeClient:
    def __init__(self, outputs):
        self.responses = FakeResponses(outputs)


def make_decision() -> AgentDecision:
    return AgentDecision(
        board_status="stable",
        board_summary="안정된 9×9 보드",
        move_type="normal_match",
        visible_elements=["파란 사탕"],
        action="swap",
        source=Cell(row=1, col=1),
        target=Cell(row=1, col=2),
        confidence=0.9,
        reason="가로 매치를 만든다.",
    )


def make_label() -> VideoTransitionLabel:
    return VideoTransitionLabel(
        action_observable=True,
        actual_action="swap",
        source=Cell(row=1, col=1),
        target=Cell(row=1, col=2),
        move_type="normal_match",
        board_summary="행동 전 보드",
        score_before=100,
        score_after=200,
        confidence=0.9,
        reason="두 칸의 교환이 보인다.",
    )


def make_outcome() -> VideoOutcomeLabel:
    return VideoOutcomeLabel(
        move_type="normal_match",
        board_summary="교환 뒤 일반 매치가 발생함",
        score_before=100,
        score_after=200,
        confidence=0.9,
        reason="점수와 보드 반응이 선명함",
    )


class UsageTests(unittest.TestCase):
    def test_usage_budget_limits_calls(self):
        budget = UsageBudget(
            max_calls=1,
            max_total_tokens=1000,
        )
        budget.start_call()
        with self.assertRaises(ApiBudgetExceeded):
            budget.start_call()

    def test_usage_addition(self):
        usage = ApiUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        ) + ApiUsage(
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
        )
        self.assertEqual(usage.total_tokens, 45)


class ImageTests(unittest.TestCase):
    def test_grid_and_observation_shapes(self):
        board = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )
        score = np.zeros(
            (30, 60, 3),
            dtype=np.uint8,
        )

        grid = add_grid_overlay(board, 9, 9)
        self.assertEqual(grid.shape, (130, 130, 3))

        observation = make_video_observation(
            board,
            score,
            9,
            9,
        )
        self.assertEqual(
            observation.shape,
            (270, 130, 3),
        )
        self.assertTrue(
            image_to_data_url(grid).startswith(
                "data:image/png;base64,"
            )
        )

        zoom = make_swap_zoom(
            board,
            board,
            4,
            4,
            4,
            5,
            9,
            9,
        )
        self.assertEqual(zoom.shape[0], 318)
        self.assertGreater(zoom.shape[1], zoom.shape[0])

    def test_out_of_bounds_roi_is_rejected(self):
        frame = np.zeros(
            (100, 100, 3),
            dtype=np.uint8,
        )
        with self.assertRaises(ValueError):
            crop_roi(
                frame,
                {
                    "left": 50,
                    "top": 50,
                    "width": 60,
                    "height": 60,
                },
            )


class AgentTests(unittest.TestCase):
    def test_gpt4o_is_not_treated_as_reasoning_model(self):
        self.assertFalse(
            model_supports_reasoning(
                "gpt-4o-mini-2024-07-18"
            )
        )
        self.assertTrue(
            model_supports_reasoning("gpt-5-mini")
        )

    def test_board_analysis_uses_memory_and_returns_usage(self):
        decision = make_decision()
        client = FakeClient([decision])
        image = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )

        result, usage = analyze_board(
            client,
            image,
            "특수 사탕 결합을 우선",
        )

        self.assertEqual(result, decision)
        self.assertEqual(usage.total_tokens, 120)
        request = client.responses.calls[0]
        self.assertNotIn("reasoning", request)
        prompt = request["input"][1]["content"][0][
            "text"
        ]
        self.assertIn(
            "특수 사탕 결합을 우선",
            prompt,
        )

    def test_video_outcome_sends_three_images(self):
        outcome = make_outcome()
        client = FakeClient([outcome])
        image = np.zeros(
            (90, 90, 3),
            dtype=np.uint8,
        )

        result, usage = label_video_outcome(
            client,
            image,
            image,
            image,
            Cell(row=4, col=5),
            Cell(row=5, col=5),
        )

        self.assertEqual(result, outcome)
        self.assertEqual(usage.input_tokens, 100)
        content = client.responses.calls[0][
            "input"
        ][1]["content"]
        image_count = sum(
            item["type"] == "input_image"
            for item in content
        )
        self.assertEqual(image_count, 3)
        prompt = content[0]["text"]
        self.assertIn("첫 번째 칸: (4, 5)", prompt)
        self.assertIn("두 번째 칸: (5, 5)", prompt)
        self.assertIn("행동 발생 여부는 재판정하지 않는다", prompt)
        self.assertIs(
            client.responses.calls[0]["text_format"],
            VideoOutcomeLabel,
        )

    def test_decision_and_label_validation(self):
        decision = make_decision()
        label = make_label()

        self.assertTrue(
            validate_decision(decision)[0]
        )
        self.assertTrue(
            validate_transition_label(label)[0]
        )
        self.assertTrue(
            actions_match(decision, label)
        )


class VideoTests(unittest.TestCase):
    def test_roi_bounds(self):
        validate_roi_bounds(
            "board",
            {
                "left": 440,
                "top": 0,
                "width": 700,
                "height": 720,
            },
            1280,
            720,
        )

        with self.assertRaises(ValueError):
            validate_roi_bounds(
                "board",
                {
                    "left": 500,
                    "top": 180,
                    "width": 600,
                    "height": 600,
                },
                1280,
                720,
            )

    @unittest.skipUnless(
        Path(VIDEO_PATH).exists(),
        "configured video is unavailable",
    )
    def test_real_video_event_loop_without_api(self):
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
                return_value=True,
            ) as process_event,
            patch("video_pipeline.append_jsonl"),
        ):
            run_video(
                mode="train",
                video_path=VIDEO_PATH,
                start_seconds=20.0,
                end_seconds=40.0,
                memory_limit=None,
                experiment_label="smoke_test",
                max_events_override=1,
            )

        self.assertTrue(process_event.called)
        arguments = process_event.call_args.kwargs
        candidate = arguments["candidate"]
        self.assertLess(
            arguments["timestamp"],
            25.5,
        )
        self.assertLessEqual(
            (
                arguments["action_timestamp"]
                - arguments["timestamp"]
            ),
            0.25,
        )
        self.assertEqual(
            abs(
                candidate.source_row
                - candidate.target_row
            )
            + abs(
                candidate.source_col
                - candidate.target_col
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
