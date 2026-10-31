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
from navigation.policy import canonical_action_label, navigation_action_key_from_label as key_of
from tap_refine import NavTabTarget, find_nav_tabs, nav_tab_label
from test_play_policy import AutodriveHarness, _bundle

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
            NavTab(text="상점", x=77, y=230),
            NavTab(icon="별 아이콘", is_home=True, x=487, y=230, selected=True),
            NavTab(icon="사람 두 명 아이콘", x=724, y=230),
            NavTab(icon="달력 아이콘", x=906, y=230),
        ])
        request = _answer(answer)
        tabs = find_nav_tabs(object(), IMAGE, request=request)
        # 글자 > 홈 > 순서 이름(아이콘에 기능 이름을 지어 붙이지 않음)
        self.assertEqual(
            [t.label for t in tabs],
            ["상점", "홈", "셋째 탭 (사람 두 명 아이콘)", "넷째 탭 (달력 아이콘)"],
        )
        self.assertEqual((tabs[0].x, tabs[0].y), (57, 1302))
        self.assertEqual(tabs[3].x, 670)
        self.assertTrue(tabs[1].selected)
        self.assertEqual(request.kwargs["full_image_detail"], "high")
        self.assertIn("기능 이름을 지어내지 않는다", request.kwargs["prompt"])
        self.assertNotIn("활동", request.kwargs["prompt"])  # 게임 전용 예시 이름 없음

    def test_ordinal_label_key_ignores_the_icon_description(self) -> None:
        # 화면마다 아이콘 묘사가 달라도 같은 순서의 탭은 같은 버튼 키입니다.
        first = nav_tab_label(3, "", "사람 두 명 아이콘", False)
        second = nav_tab_label(3, "", "두 사람 마름모", False)
        self.assertEqual(key_of(first), key_of(second))
        self.assertNotEqual(key_of(first), key_of(nav_tab_label(4, "", "사람 두 명 아이콘", False)))
        self.assertEqual(canonical_action_label(nav_tab_label(2, "", "별", True)), "home")

    def test_covered_bar_and_errors_give_nothing(self) -> None:
        covered = NavTabs(covered=True, tabs=[NavTab(text="홈", x=10, y=10)])
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


class LocatedButtonsSkipRefineTests(AutodriveHarness):
    def test_tab_finder_buttons_are_tapped_without_refine(self) -> None:
        tab = SurveyButtonCandidate(
            label="넷째 탭 (사람 두 명 아이콘)", role="structure", region="bottom_nav",
            center=NormalizedPoint(x=0.7, y=0.95), confidence=1.0, reason="탭바 찾기",
        )
        home = SurveyDecision(screen_type="home", button_candidates=[tab])
        with patch("navigation.navigator.refine_tap_target") as refine:
            mocks = self._run(
                captures=[_bundle(0), _bundle(200)],
                decisions=[home, SurveyDecision(screen_type="menu")],
                max_steps=2,
                extra_patches=(("navigation.navigator.TAP_REFINE", True),),
            )
        refine.assert_not_called()
        self.assertEqual(mocks["tapped"], ["넷째 탭 (사람 두 명 아이콘)"])


if __name__ == "__main__":
    unittest.main()
