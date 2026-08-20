from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from schemas import SurveyButtonCandidate, SurveyDecision, SurveyElement
from survey_utils import (
    choose_next_button,
    collect_element_keys,
    normalize_text,
    screen_signature,
)


class SurveyUtilsTests(unittest.TestCase):
    def test_normalize_text_reduces_case_and_spacing_noise(self) -> None:
        self.assertEqual(normalize_text("  Green   Bear!! "), "green bear")

    def test_screen_signature_matches_equivalent_survey(self) -> None:
        first = SurveyDecision(
            screen_type="level_complete",
            visible_text=["Next", "Level 9"],
            game_elements=[
                SurveyElement(category="reward", name="Three Stars"),
            ],
        )
        second = SurveyDecision(
            screen_type="level_complete",
            visible_text=["level 9", "NEXT"],
            game_elements=[
                SurveyElement(category="reward", name="three stars"),
            ],
        )

        self.assertEqual(screen_signature(first), screen_signature(second))
        self.assertEqual(collect_element_keys(first), collect_element_keys(second))

    def test_choose_next_button_skips_risky_buttons(self) -> None:
        decision = SurveyDecision(
            button_candidates=[
                SurveyButtonCandidate(
                    label="Buy",
                    role="risky_monetization",
                    center={"x": 0.5, "y": 0.5},
                    confidence=0.99,
                ),
                SurveyButtonCandidate(
                    label="Next",
                    role="progression",
                    center={"x": 0.5, "y": 0.8},
                    confidence=0.7,
                ),
            ],
        )

        button = choose_next_button(decision, min_confidence=0.55)

        self.assertIsNotNone(button)
        self.assertEqual(button.label, "Next")

    def test_choose_next_button_prefers_progression_over_close(self) -> None:
        decision = SurveyDecision(
            screen_type="level_complete",
            button_candidates=[
                SurveyButtonCandidate(
                    label="Close (X)",
                    role="safe_navigation",
                    center={"x": 0.92, "y": 0.12},
                    confidence=0.8,
                ),
                SurveyButtonCandidate(
                    label="Next",
                    role="progression",
                    center={"x": 0.5, "y": 0.78},
                    confidence=0.9,
                ),
            ],
        )

        button = choose_next_button(decision, min_confidence=0.55)

        self.assertIsNotNone(button)
        self.assertEqual(button.label, "Next")

    def test_choose_next_button_skips_current_shop_tab(self) -> None:
        decision = SurveyDecision(
            screen_type="shop_or_currency",
            button_candidates=[
                SurveyButtonCandidate(
                    label="Shop (bottom dock)",
                    role="structure",
                    center={"x": 0.12, "y": 0.92},
                    confidence=0.95,
                ),
                SurveyButtonCandidate(
                    label="Close",
                    role="safe_navigation",
                    center={"x": 0.92, "y": 0.07},
                    confidence=0.8,
                ),
            ],
        )

        button = choose_next_button(decision, min_confidence=0.55)

        self.assertIsNotNone(button)
        self.assertEqual(button.label, "Close")


if __name__ == "__main__":
    unittest.main()
