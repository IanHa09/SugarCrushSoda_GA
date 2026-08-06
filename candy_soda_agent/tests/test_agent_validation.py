from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("numpy", "openai", "pydantic")
)


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "agent dependencies are not installed")
class AgentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        from agent import validate_decision
        from schemas import AgentDecision

        self.validate_decision = validate_decision
        self.AgentDecision = AgentDecision

    def _decision(self, **overrides):
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
