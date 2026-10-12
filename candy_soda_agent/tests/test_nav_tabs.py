"""탭바 전용 찾기: 조사 모델이 빠뜨린 아이콘 탭을 찾아 탭바 후보를 바꿉니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import active_profile
from navigation.navigator import _add_nav_tabs
from schemas import NavTab, NavTabs, NormalizedPoint, SurveyButtonCandidate, SurveyDecision
from tap_refine import NavTabTarget, find_nav_tabs

IMAGE = np.zeros((1348, 757, 3), np.uint8)


def _answer(answer):
    def request(*args, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        request.kwargs = kwargs
        return answer
    return request


class FindNavTabsTests(unittest.TestCase):
    def test_tabs_are_mapped_back_to_screen_pixels(self) -> None:
        # 띠는 y=1132부터(1348 * 0.84), 긴 변 757 -> 1024로 키움(배율 약 1.353).
        answer = NavTabs(tabs=[
            NavTab(label="상점", x=77, y=230),
            NavTab(label="홈", x=487, y=230, selected=True),
            NavTab(label="", x=700, y=230),  # 이름 없는 탭은 버림
            NavTab(label="활동", x=906, y=230),
        ])
        request = _answer(answer)
        tabs = find_nav_tabs(object(), IMAGE, request=request)
        self.assertEqual([t.label for t in tabs], ["상점", "홈", "활동"])
        self.assertEqual((tabs[0].x, tabs[0].y), (57, 1302))
        self.assertEqual(tabs[2].x, 670)
        self.assertTrue(tabs[1].selected)
        self.assertEqual(request.kwargs["full_image_detail"], "high")

    def test_covered_bar_and_errors_give_nothing(self) -> None:
        covered = NavTabs(covered=True, tabs=[NavTab(label="홈", x=10, y=10)])
        self.assertEqual(find_nav_tabs(object(), IMAGE, request=_answer(covered)), [])
        self.assertEqual(find_nav_tabs(object(), IMAGE, request=_answer(RuntimeError("down"))), [])


def _button(label, x, region, role="safe_navigation"):
    return SurveyButtonCandidate(
        label=label, role=role, region=region, center=NormalizedPoint(x=x, y=0.97), confidence=0.9
    )


class AddNavTabsTests(unittest.TestCase):
    decision = SurveyDecision(
        screen_type="home",
        button_candidates=[
            _button("히스토리 아이콘", 0.31, "bottom_nav"),  # 조사 모델이 잘못 적은 탭
            _button("친구 아이콘", 0.43, "bottom_nav"),
            _button("설정", 0.9, "top_bar"),
        ],
    )
    node = SimpleNamespace(id="home", screen_type="home")
    found = [
        NavTabTarget("상점", 57, 1302),
        NavTabTarget("친구", 187, 1302),
        NavTabTarget("홈", 360, 1302, selected=True),
        NavTabTarget("팀", 535, 1302),
        NavTabTarget("활동", 670, 1302),
    ]

    def test_bottom_nav_candidates_are_replaced_by_found_tabs(self) -> None:
        with patch("navigation.navigator.find_nav_tabs", return_value=self.found):
            result = _add_nav_tabs(object(), self.decision, IMAGE, self.node, active_profile())
        labels = [b.label for b in result.button_candidates]
        self.assertEqual(labels, ["상점", "친구", "팀", "활동", "설정"])  # 선택된 홈은 뺌
        team = result.button_candidates[2]
        self.assertEqual((team.role, team.region), ("structure", "bottom_nav"))
        self.assertAlmostEqual(team.center.x, 535 / 757)

    def test_nothing_found_or_no_tab_bar_keeps_the_survey(self) -> None:
        with patch("navigation.navigator.find_nav_tabs", return_value=[]):
            self.assertIs(_add_nav_tabs(object(), self.decision, IMAGE, self.node, active_profile()), self.decision)
        no_bar = active_profile().__class__(**{**active_profile().__dict__, "nav_bar_position": "none"})
        with patch("navigation.navigator.find_nav_tabs", return_value=self.found) as finder:
            self.assertIs(_add_nav_tabs(object(), self.decision, IMAGE, self.node, no_bar), self.decision)
        finder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
