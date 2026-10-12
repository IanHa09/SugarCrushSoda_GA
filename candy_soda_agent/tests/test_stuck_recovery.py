"""막힘 대응: 이름 합치기, 리셋이 못 찾은 버튼 건너뛰기, 다른 버튼 고르기, 순환 감지."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.frontier import StepGoal
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore
from navigation.navigator import (
    _choose_action_for_goal,
    _is_cycling,
    _Journey,
    _merge_candidates,
    _plan_escape,
    _stuck_hint,
    ESCAPE_STAGES,
)
from navigation.policy import canonical_action_label, navigation_action_key_from_label as key_of
from schemas import NormalizedPoint, SurveyButtonCandidate, SurveyDecision

IMAGE = np.zeros((100, 60, 3), np.uint8)


def _button(label: str, role: str = "structure", confidence: float = 0.9):
    return SurveyButtonCandidate(
        label=label, role=role, center=NormalizedPoint(x=0.5, y=0.5), confidence=confidence
    )


class AliasTests(unittest.TestCase):
    def test_phrases_with_an_alias_word_stay_separate(self) -> None:
        # 실측: "친구 더 추가"가 탭바 "친구"와 같은 키가 되어 한 번도 안 눌림.
        for label in ("친구 더 추가", "친구 초대", "Team Shop", "Back to map", "Background music"):
            self.assertNotIn(canonical_action_label(label), {"friends", "shop", "back", "map"}, label)

    def test_label_variants_of_one_button_still_merge(self) -> None:
        cases = {
            "friends": ("친구", "친구 아이콘", "Friends tab"),
            "settings": ("Settings (gear)", "Settings gear", "설정 아이콘", "설정창"),
            "home": ("Home button", "홈 (별 아이콘)", "홈으로"),
            "back": ("Back arrow", "뒤로가기", "뒤로 가기"),
            "close": ("X", "Close (X)", "X 닫기", "Close popup"),
            "shop": ("Shop (bottom-left icon)", "상점 아이콘"),
        }
        for canonical, labels in cases.items():
            for label in labels:
                self.assertEqual(canonical_action_label(label), canonical, label)


class _LedgerCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = LedgerStore(Path(directory.name) / "ledger.json")

    def close_not_found(self, screen: str, label: str) -> None:
        self.ledger.seen(screen, key_of(label), label)
        self.ledger.close_not_found(screen, key_of(label))

    def tapped(self, screen: str, label: str, verdict: str, target: str) -> None:
        self.ledger.mark_attempted(screen, key_of(label), label)
        self.ledger.finalize(screen, key_of(label), verdict, target_screen_id=target)


class ResetSkipTests(_LedgerCase):
    def test_reset_skips_a_close_button_that_was_not_found(self) -> None:
        candidates = [_button("X", "safe_navigation"), _button("Home", "safe_navigation")]
        self.close_not_found("friends", "X")
        journey = _Journey()
        button, _, used_home = _choose_action_for_goal(
            StepGoal(kind="reset"), candidates, "friends", self.ledger,
            skip_keys=journey.dead_keys(self.ledger, "friends"),
        )
        self.assertEqual((button.label, used_home), ("Home", True))

    def test_session_misses_are_skipped_too(self) -> None:
        journey = _Journey()
        journey.missing_keys["friends"] = {key_of("X")}
        self.assertEqual(journey.dead_keys(self.ledger, "friends"), {key_of("X")})


class OtherButtonsTests(_LedgerCase):
    def _journey_at(self, stage: str) -> _Journey:
        journey = _Journey()
        journey.escape_stage = ESCAPE_STAGES.index(stage)
        return journey

    def test_order_and_exclusions(self) -> None:
        decision = SurveyDecision(
            screen_type="menu",
            button_candidates=[
                _button("Help", "progression"),
                _button("Info", "structure"),
                _button("X", "safe_navigation"),         # 화면에 없음(not_found)
                _button("Home", "safe_navigation", 0.5),  # 나가기류라 맨 먼저
                _button("Gems", "risky_monetization"),
                _button("구매하기", "structure"),          # 위험 낱말
            ],
        )
        self.close_not_found("friends", "X")
        self.tapped("friends", "Info", "new_screen", "info_popup")  # 오가던 화면으로 감
        journey = self._journey_at("other_buttons")
        journey.stuck_cycle = {"friends", "info_popup"}
        picked = []
        with patch("navigation.navigator.STUCK_OTHER_BUTTONS_MAX", 5):
            for _ in range(3):
                plan = _plan_escape(None, journey, decision, IMAGE, node_id="friends", ledger=self.ledger)
                picked.append(plan.label if plan and plan.stage == "other_buttons" else None)
        self.assertEqual(picked, ["Home", "Help", None])

    def test_tap_is_refined_and_capped_per_screen(self) -> None:
        decision = SurveyDecision(
            screen_type="menu", button_candidates=[_button(f"Item {i}") for i in "ABC"]
        )
        journey = self._journey_at("other_buttons")
        with patch("navigation.navigator.STUCK_OTHER_BUTTONS_MAX", 2):
            plans = [
                _plan_escape(None, journey, decision, IMAGE, node_id="s", ledger=self.ledger)
                for _ in range(3)
            ]
        self.assertTrue(plans[0].refine)
        self.assertEqual([p.stage if p else None for p in plans], ["other_buttons", "other_buttons", None])

    def test_resurvey_once_per_screen(self) -> None:
        decision = SurveyDecision(screen_type="menu")
        journey = self._journey_at("resurvey")
        first = _plan_escape(object(), journey, decision, IMAGE, node_id="s", ledger=self.ledger)
        self.assertEqual((first.stage, first.kind), ("resurvey", "resurvey"))
        journey.escape_stage = ESCAPE_STAGES.index("resurvey")
        self.assertIsNone(_plan_escape(object(), journey, decision, IMAGE, node_id="s", ledger=self.ledger))
        # 다른 화면은 따로 한 번.
        journey.escape_stage = ESCAPE_STAGES.index("resurvey")
        self.assertEqual(
            _plan_escape(object(), journey, decision, IMAGE, node_id="t", ledger=self.ledger).stage,
            "resurvey",
        )


class ResurveyMergeTests(_LedgerCase):
    def test_hint_lists_tried_and_missing_buttons(self) -> None:
        decision = SurveyDecision(
            screen_type="popup",
            button_candidates=[_button("i 아이콘"), _button("X", "safe_navigation"), _button("친구 아이콘")],
        )
        self.tapped("friends", "i 아이콘", "new_screen", "info")
        self.close_not_found("friends", "X")
        self.tapped(GLOBAL_SCREEN_ID, "친구 아이콘", "new_screen", "friends")
        hint = _stuck_hint(decision, self.ledger, "friends")
        self.assertIn("이미 눌러 본 버튼: i 아이콘, 친구 아이콘", hint)
        self.assertIn("화면에 없다고 확인된 버튼: X", hint)
        self.assertIn("탭바", hint)

    def test_new_buttons_go_first_and_missing_ones_are_dropped(self) -> None:
        old = SurveyDecision(screen_type="popup", button_candidates=[_button("i"), _button("X")])
        fresh = SurveyDecision(
            screen_type="menu",
            button_candidates=[_button("X"), _button("Home"), _button("Shop"), _button("i")],
        )
        merged, added = _merge_candidates(old, fresh, {key_of("X")})
        self.assertEqual([b.label for b in added], ["Home", "Shop"])
        self.assertEqual([b.label for b in merged.button_candidates], ["Home", "Shop", "i"])
        self.assertEqual(merged.screen_type, "popup")  # 노드의 화면 타입은 그대로


class CycleTests(unittest.TestCase):
    def test_two_screens_without_progress_is_a_cycle(self) -> None:
        journey = _Journey()
        with patch("navigation.navigator.STUCK_CYCLE_STEPS", 4):
            journey.idle_nodes = ["a", "b", "a"]
            self.assertFalse(_is_cycling(journey))
            journey.idle_nodes.append("b")
            self.assertTrue(_is_cycling(journey))
            journey.idle_nodes = ["a", "b", "c", "a"]  # 여러 화면을 지나는 이동은 순환이 아님
            self.assertFalse(_is_cycling(journey))
            journey.idle_nodes = ["a"] * 4
            journey.note_progress()
            self.assertFalse(_is_cycling(journey))
        with patch("navigation.navigator.STUCK_CYCLE_STEPS", 0):
            journey.idle_nodes = ["a"] * 10
            self.assertFalse(_is_cycling(journey))


if __name__ == "__main__":
    unittest.main()
