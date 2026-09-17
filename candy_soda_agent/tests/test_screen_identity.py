"""화면 ID 안정화(정규화 라벨 + 버튼 1개 차이 허용 매칭)를 검증합니다."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.graph import GraphStore, find_tolerant_match
from navigation.observer import screen_buttons, screen_id
from schemas import NavigationGraph, ScreenNode, SurveyButtonCandidate, SurveyDecision

IMAGE = np.zeros((4, 4, 3), dtype=np.uint8)


def _button(label: str, confidence: float = 0.9) -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label, role="structure", center={"x": 0.5, "y": 0.5}, confidence=confidence
    )


def _event(*labels: str, screen_type: str = "event_or_mission", texts=()) -> SurveyDecision:
    return SurveyDecision(
        screen_type=screen_type,
        visible_text=list(texts),
        button_candidates=[_button(label) for label in labels],
    )


class ScreenIdTests(unittest.TestCase):
    def test_wording_order_and_icon_notation_do_not_change_id(self) -> None:
        """문구 순서·종류, 아이콘 표기만 다른 같은 화면은 같은 ID입니다."""
        first = _event("Close (X)", "Go", texts=["Daily Quest", "Level 12", "Reward", "Go"])
        second = _event("Close (x icon)", "Go", texts=["Level 12", "Claim"])
        self.assertEqual(screen_id(first), screen_id(second))

    def test_low_confidence_button_is_ignored(self) -> None:
        """확신도가 기준 미만인 버튼은 식별에서 빠집니다."""
        base = _event("Close", "Go")
        noisy = SurveyDecision(
            screen_type="event_or_mission",
            button_candidates=[_button("Close"), _button("Go"), _button("Info", 0.4)],
        )
        self.assertEqual(screen_buttons(noisy), ["close", "go"])
        self.assertEqual(screen_id(base), screen_id(noisy))


class TolerantMatchTests(unittest.TestCase):
    def _observe_all(self, decisions, max_diff: int = 1) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "navigation"
            with (
                patch("navigation.graph.ROOT", root),
                patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
                patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
                patch("navigation.graph.IMAGE_DIR", root / "representatives"),
                patch("navigation.graph.SCREEN_MATCH_MAX_BUTTON_DIFF", max_diff),
            ):
                store = GraphStore()
                return [store.observe(decision, IMAGE).id for decision in decisions]

    def test_one_missing_or_extra_button_maps_to_existing_node(self) -> None:
        """기준 {close, go}에서 버튼이 하나 빠지거나 하나 늘어도 같은 노드입니다."""
        ids = self._observe_all([
            _event("Close", "Go"),
            _event("Close"),
            _event("Close", "Go", "Claim"),
        ])
        self.assertEqual(len(set(ids)), 1)

    def test_larger_difference_or_no_shared_button_creates_new_node(self) -> None:
        """차이가 2개 이상이거나 공통 버튼이 없으면 다른 화면입니다."""
        ids = self._observe_all([
            _event("Close", "Go"),
            _event("Close", "Claim", "Info"),  # 차이 3개
            _event(),                          # 공통 버튼 없음
            _event("Claim"),                   # 기준 {close, go}와 공통 없음
        ])
        self.assertEqual(len(set(ids)), 4)

    def test_tolerance_never_crosses_screen_types(self) -> None:
        ids = self._observe_all([
            _event("Close", "Go"),
            _event("Close", "Go", screen_type="reward_popup"),
        ])
        self.assertNotEqual(ids[0], ids[1])

    def test_anchor_does_not_drift(self) -> None:
        """비교 기준은 처음 관찰한 버튼 목록으로 고정돼, 조금씩 다른 관찰이 이어져도
        원래 화면과 2개 이상 다른 화면까지 끌려오지 않습니다."""
        ids = self._observe_all([
            _event("Close", "Go"),
            _event("Close", "Go", "Claim"),        # 기준과 1개 차이 -> 같은 노드
            _event("Close", "Claim", "Info"),      # 기준과 3개 차이 -> 새 노드
        ])
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])

    def test_zero_tolerance_disables_matching(self) -> None:
        ids = self._observe_all([_event("Close", "Go"), _event("Close")], max_diff=0)
        self.assertNotEqual(ids[0], ids[1])

    def test_ties_prefer_smallest_difference_then_most_visits(self) -> None:
        graph = NavigationGraph(nodes={
            "few_visits": ScreenNode(
                id="few_visits", screen_type="event_or_mission",
                buttons=["a", "b", "x", "y"], visits=1,
            ),
            "many_visits": ScreenNode(
                id="many_visits", screen_type="event_or_mission",
                buttons=["a", "b"], visits=5,
            ),
        })
        # 두 노드 모두 1개 차이 -> 방문이 많은 쪽
        self.assertEqual(
            find_tolerant_match(graph, "event_or_mission", ["a", "b", "x"], 1),
            "many_visits",
        )
        # few_visits만 1개 차이(many_visits는 2개 차이)
        self.assertEqual(
            find_tolerant_match(graph, "event_or_mission", ["a", "b", "x", "y", "z"], 1),
            "few_visits",
        )


if __name__ == "__main__":
    unittest.main()
