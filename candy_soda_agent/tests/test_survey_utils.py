"""survey_utils의 정규화, 서명, 버튼 필터링 로직을 검증하는 테스트입니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from schemas import SurveyButtonCandidate, SurveyDecision, SurveyElement
from survey_utils import (
    canonical_name,
    collect_element_keys,
    element_key,
    normalize_text,
    safe_button_candidates,
    screen_signature,
)


class CanonicalNameTests(unittest.TestCase):
    """LLM이 같은 요소를 매번 다르게 불러서 한 요소가 여러 항목으로 쪼개졌습니다."""

    def test_presentation_words_are_dropped(self) -> None:
        self.assertEqual(canonical_name("부스터 아이콘"), "부스터")
        self.assertEqual(canonical_name("부스터 슬롯"), "부스터")
        self.assertEqual(canonical_name("부스터 선택 버튼"), "부스터 선택")

    def test_position_and_side_explanation_words_are_dropped(self) -> None:
        self.assertEqual(canonical_name("현재 레벨 목표"), "레벨 목표")
        self.assertEqual(canonical_name("우측 하트 아이콘"), "하트")
        self.assertEqual(canonical_name("장애물 표현"), "장애물")
        self.assertEqual(canonical_name("보상 알림"), "보상")

    def test_meaningful_words_keep_elements_apart(self) -> None:
        """뜻 있는 낱말이 다르면 다른 요소입니다 — 여기까지 합치면 자료가 망가집니다."""
        self.assertNotEqual(canonical_name("레벨 목표"), canonical_name("레벨 번호"))
        self.assertNotEqual(canonical_name("할인 정보"), canonical_name("통화 정보"))
        self.assertNotEqual(canonical_name("부스터"), canonical_name("부스터 선택"))

    def test_english_and_korean_names_meet_in_the_middle(self) -> None:
        self.assertEqual(canonical_name("Coins"), "코인")
        self.assertEqual(canonical_name("Player Character"), "플레이어 캐릭터")

    def test_names_made_only_of_presentation_words_are_kept(self) -> None:
        """전부 떼면 빈 이름이 되어 서로 다른 요소가 한 덩어리가 됩니다."""
        self.assertEqual(canonical_name("UI 요소"), "ui 요소")

    def test_element_key_matches_for_differently_written_names(self) -> None:
        self.assertEqual(
            element_key("currency", "Coins"),
            element_key("currency", "코인"),
        )
        self.assertNotEqual(
            element_key("currency", "코인"),
            element_key("currency", "골드"),
        )


class SurveyUtilsTests(unittest.TestCase):
    def test_normalize_text_reduces_case_and_spacing_noise(self) -> None:
        self.assertEqual(normalize_text("  Green   Bear!! "), "green bear")

    def test_screen_signature_matches_equivalent_survey(self) -> None:
        """표기만 다른 동일 화면의 서명과 요소 키가 같은지 검증합니다."""
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

    def test_risky_buttons_are_never_candidates(self) -> None:
        """위험군 버튼은 안전 후보에서 제외되는지 검증합니다."""
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

        buttons = safe_button_candidates(decision, min_confidence=0.55)

        self.assertEqual([button.label for button in buttons], ["Next"])

    def test_progression_is_preferred_over_going_back(self) -> None:
        """진행 버튼이 되돌리기 버튼보다 우선하는지 검증합니다."""
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

        buttons = safe_button_candidates(decision, min_confidence=0.55)

        self.assertTrue(buttons)
        self.assertEqual(buttons[0].label, "Next")

    def test_current_shop_tab_is_skipped(self) -> None:
        """현재 화면과 같은 상점 탭 버튼은 건너뛰는지 검증합니다."""
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

        buttons = safe_button_candidates(decision, min_confidence=0.55)

        self.assertEqual([button.label for button in buttons], ["Close"])


if __name__ == "__main__":
    unittest.main()
