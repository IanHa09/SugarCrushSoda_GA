"""agent.validate_decision과 analyze_screen의 안전 검증 로직을 검사합니다."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("cv2", "numpy", "openai", "pydantic")
)


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "agent dependencies are not installed")
class AgentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        from agent import validate_decision
        from schemas import AgentDecision

        self.validate_decision = validate_decision
        self.AgentDecision = AgentDecision

    def _decision(self, **overrides):
        """기본값이 채워진 stable playing swap AgentDecision을 만들고 overrides로 덮어씁니다."""

        data = {
            "board_status": "stable",
            "detected_ui_state": "playing",
            "action": "swap",
            "source": {"row": 1, "col": 1},
            "target": {"row": 1, "col": 2},
            "confidence": 0.9,
            "reason": "test",
        }
        data.update(overrides)
        return self.AgentDecision(**data)

    def test_popup_swap_is_rejected(self) -> None:
        decision = self._decision(detected_ui_state="popup")
        self.assertFalse(self.validate_decision(decision)[0])

    def test_playing_adjacent_swap_is_accepted(self) -> None:
        self.assertTrue(self.validate_decision(self._decision())[0])

    def test_popup_wait_is_accepted(self) -> None:
        decision = self._decision(
            detected_ui_state="popup",
            action="wait",
            source=None,
            target=None,
            confidence=0.0,
        )
        self.assertTrue(self.validate_decision(decision)[0])

    def test_dynamic_grid_bounds_are_used(self) -> None:
        decision = self._decision(
            source={"row": 7, "col": 8},
            target={"row": 7, "col": 9},
        )
        self.assertTrue(self.validate_decision(decision, rows=7, cols=9)[0])

    def test_failed_move_and_reverse_are_rejected(self) -> None:
        decision = self._decision()
        failed = {(1, 1, 1, 2)}
        self.assertFalse(
            self.validate_decision(
                decision,
                rows=7,
                cols=9,
                forbidden_moves=failed,
            )[0]
        )

    def test_analyze_screen_uses_fast_image_settings(self) -> None:
        """analyze_screen이 전체/보드 이미지에 설정된 detail 값을 그대로 요청에 담는지 확인합니다."""

        import numpy as np

        from agent import analyze_screen
        from config import (
            BOARD_IMAGE_DETAIL,
            FULL_IMAGE_DETAIL,
            LLM_MAX_OUTPUT_TOKENS,
        )

        decision = self._decision(action="wait", source=None, target=None)
        captured_request = {}

        class FakeResponses:
            def parse(self, **request):
                captured_request.update(request)
                return SimpleNamespace(output_parsed=decision)

        client = SimpleNamespace(responses=FakeResponses())
        image = np.zeros((64, 64, 3), dtype=np.uint8)

        result = analyze_screen(client, image, image)

        self.assertIs(result, decision)
        self.assertEqual(
            captured_request["max_output_tokens"],
            LLM_MAX_OUTPUT_TOKENS,
        )
        content = captured_request["input"][1]["content"]
        image_inputs = [item for item in content if item["type"] == "input_image"]
        self.assertEqual(image_inputs[0]["detail"], FULL_IMAGE_DETAIL)
        self.assertEqual(image_inputs[1]["detail"], BOARD_IMAGE_DETAIL)

    def test_analyze_screen_promotes_valid_candidate_when_model_waits(self) -> None:
        """모델이 wait를 반환해도 유효한 swap 후보가 있으면 승격되는지 확인합니다."""

        import numpy as np

        from agent import analyze_screen

        decision = self._decision(
            action="wait",
            source=None,
            target=None,
            confidence=0.9,
            action_candidates=[
                {
                    "action": "swap",
                    "source": {"row": 5, "col": 5},
                    "target": {"row": 5, "col": 6},
                    "confidence": 0.6,
                    "reason": "candidate",
                }
            ],
        )

        class FakeResponses:
            def parse(self, **request):
                return SimpleNamespace(output_parsed=decision)

        client = SimpleNamespace(responses=FakeResponses())
        image = np.zeros((64, 64, 3), dtype=np.uint8)

        result = analyze_screen(client, image, image, rows=7, cols=9)

        self.assertEqual(result.action, "swap")
        self.assertEqual(result.source.row, 5)
        self.assertEqual(result.source.col, 5)
        self.assertEqual(result.target.row, 5)
        self.assertEqual(result.target.col, 6)
        self.assertEqual(result.confidence, 0.6)
