"""팝업 판정 하나로 합치기(탭바 띠 밝기 + 팝업 안 나가기 버튼)와 확대 확인이 본 글자 검사.

실측(run_2026-11-05_10-04-35, 애니팡 2, 148스텝):
- 탭바 찾기 건너뛰기(T1: 팝업 안 닫기)와 팝업 나가기(P1: 팝업 안 나가기류)가 팝업을 서로 다르게
  판정했고, 둘 다 조사 모델의 region에 기대 GIFT BOX(X를 안 적음)·환경설정(X를 top_bar로 적음)에서
  뒤로 비친 탭을 눌렀습니다. 화면 아래 10% 띠의 밝기를 루트와 견주면 탭 화면 1.00~1.01, 팝업·광고·
  전체 화면 메뉴 0.07~0.38 또는 1.44~2.54로 갈렸습니다(캔디 run_2026-10-27_06-35-53 포함 19화면).
- '무료 뽑기'를 누르려는데 확대 확인이 바로 위 '광고 보고 무료 뽑기'로 옮겼고(seen_label에 그 글자가
  남음) 광고에 5번 들어갔습니다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import active_profile
from navigation.navigator import (
    _Journey,
    _add_nav_tabs,
    _popup_exit_keys,
    _root_band_brightness,
    _tab_bar_hidden,
    _tab_band_brightness,
)
from navigation.observer import screen_id
from navigation.policy import navigation_action_key_from_label as key_of
from schemas import NormalizedPoint, SurveyButtonCandidate, SurveyDecision
from tap_refine import NavTabTarget, TapRefineResult
from test_play_policy import AutodriveHarness, _button


def _screen(content: int, band: int, box: int | None = None) -> np.ndarray:
    """본문 content, 아래 10% 띠 band(탭바 자리), box면 가운데 창(팝업)."""
    image = np.full((100, 60, 3), content, np.uint8)
    image[90:] = band
    if box is not None:
        image[20:70, 10:50] = box
    return image


def _capture(image: np.ndarray):
    height, width = image.shape[:2]
    return SimpleNamespace(full_image=image, full_region={"left": 0, "top": 0, "width": width, "height": height})


def _candidate(label: str, region: str, role: str = "safe_navigation") -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label, role=role, region=region, center=NormalizedPoint(x=0.9, y=0.1), confidence=0.9
    )


HOME_IMAGE = _screen(100, 200)
POPUP_IMAGE = _screen(30, 60, box=230)  # 팝업이 탭바까지 어둡게 덮음
NAV_IMAGE = np.zeros((1348, 757, 3), np.uint8)  # 탭바 찾기 결과 좌표가 들어가는 크기


class TabBarBandTests(unittest.TestCase):
    def test_band_brightness_ratio_separates_tab_screens_from_popups(self) -> None:
        root = _tab_band_brightness(HOME_IMAGE)
        self.assertFalse(_tab_bar_hidden(_screen(40, 200), root))  # 본문만 다른 탭 화면
        self.assertTrue(_tab_bar_hidden(POPUP_IMAGE, root))  # 0.30
        self.assertTrue(_tab_bar_hidden(_screen(250, 255, box=240), _tab_band_brightness(_screen(100, 150))))  # 1.70
        self.assertFalse(_tab_bar_hidden(POPUP_IMAGE, None))  # 루트를 모르면 판정 안 함

    def test_root_band_is_read_once_and_retried_while_the_image_is_missing(self) -> None:
        journey = _Journey()
        missing = SimpleNamespace(load_image=Mock(return_value=None))
        self.assertIsNone(_root_band_brightness(missing, journey, "root"))
        self.assertIsNone(journey.root_band)
        store = SimpleNamespace(load_image=Mock(return_value=HOME_IMAGE))
        self.assertEqual(_root_band_brightness(store, journey, "root"), 200.0)
        self.assertEqual(_root_band_brightness(store, journey, "root"), 200.0)
        store.load_image.assert_called_once_with("root")
        self.assertIsNone(_root_band_brightness(store, journey, None))


class OnePopupRuleTests(unittest.TestCase):
    found = [NavTabTarget("첫째 탭 (선물 상자)", 62, 1297, index=0),
             NavTabTarget("둘째 탭 (편지 봉투)", 130, 1297, selected=True, index=1)]

    def _nav(self, decision, *, hidden: bool):
        node = SimpleNamespace(id="n", screen_type=decision.screen_type, tab_index=-1, tab_name="")
        with patch("navigation.navigator.find_nav_tabs", return_value=self.found) as finder:
            _add_nav_tabs(object(), decision, NAV_IMAGE, node, active_profile(), tab_bar_hidden=hidden)
        return finder

    def test_gift_box_without_a_listed_x_is_a_popup_by_the_band(self) -> None:
        # X는 나가기 찾기가 region=unknown으로 넣었고 조사 모델은 X를 안 적음.
        gift = SurveyDecision(screen_type="popup", button_candidates=[
            _candidate("X", "unknown"), _candidate("이동하기", "popup", role="progression"),
        ])
        self.assertEqual(_popup_exit_keys(gift, active_profile()), set())
        self.assertEqual(_popup_exit_keys(gift, active_profile(), tab_bar_hidden=True), {key_of("X")})
        self._nav(gift, hidden=True).assert_not_called()

    def test_settings_with_x_on_the_top_bar_skips_the_tab_finder_when_the_band_is_dark(self) -> None:
        settings = SurveyDecision(screen_type="menu", button_candidates=[_candidate("X 아이콘", "top_bar")])
        self._nav(settings, hidden=True).assert_not_called()
        self._nav(settings, hidden=False).assert_called_once()  # 띠가 같으면 예전처럼 찾음

    def test_info_popup_with_only_ok_is_a_popup_for_both_rules(self) -> None:
        # 예전 T1은 X·닫기만 봐서 '확인'만 있는 안내 팝업에서 탭바를 찾았고, P1은 팝업으로 봤습니다.
        info = SurveyDecision(screen_type="popup", button_candidates=[_candidate("확인", "popup")])
        self.assertEqual(_popup_exit_keys(info, active_profile()), {key_of("확인")})
        self._nav(info, hidden=False).assert_not_called()


class PopupByBandAutodriveTests(AutodriveHarness):
    def test_gift_box_x_is_used_to_leave_not_explored(self) -> None:
        # 홈 --A--> GIFT BOX(X는 region=unknown) --X--> 홈 --B--> …
        home = SurveyDecision(screen_type="home", button_candidates=[_button("A"), _button("B")])
        gift = SurveyDecision(screen_type="popup", button_candidates=[_button("X", role="safe_navigation")])
        mocks = self._run(
            captures=[_capture(HOME_IMAGE), _capture(POPUP_IMAGE), _capture(HOME_IMAGE)],
            decisions=[home, gift, home],
            max_steps=3,
        )
        self.assertEqual(mocks["tapped"], ["A", "X", "B"])
        screens = {entry.screen_id for entry in mocks["ledger"].ledger.entries.values()}
        self.assertNotIn(screen_id(gift), screens)  # X는 프론티어가 아님

    def test_run_does_not_end_on_a_popup(self) -> None:
        # 다 확인했어도 팝업 위에서 끝내지 않고 나간 뒤 끝냅니다(나가는 간선이 남음).
        home = SurveyDecision(screen_type="home", button_candidates=[_button("A")])
        gift = SurveyDecision(screen_type="popup", button_candidates=[_button("X", role="safe_navigation")])
        mocks = self._run(
            captures=[_capture(HOME_IMAGE), _capture(POPUP_IMAGE), _capture(HOME_IMAGE)],
            decisions=[home, gift, home],
            max_steps=3,
        )
        self.assertEqual(mocks["tapped"], ["A", "X"])
        self.assertIn((screen_id(gift), "X"), {(e.source, e.action) for e in mocks["graph"].edges})
        self.assertEqual(mocks["summary"].stop_kind, "converged")


class SeenLabelTests(AutodriveHarness):
    def test_refine_landing_on_an_ad_button_is_not_tapped(self) -> None:
        home = SurveyDecision(screen_type="home", button_candidates=[_button("보상 받기", role="progression")])
        refine = Mock(return_value=TapRefineResult(
            found=True, initial=(30, 50), x=30, y=40, used_pass="zoom", seen_label="광고 보고 무료 뽑기",
        ))
        mocks = self._run(
            captures=[_capture(HOME_IMAGE), _capture(HOME_IMAGE)],
            decisions=[home],
            max_steps=2,
            extra_patches=(
                ("navigation.navigator.TAP_REFINE", True),
                ("navigation.navigator.refine_tap_target", refine),
            ),
        )
        # 두 번째 스텝은 실패로 닫힌 항목 다시 시도(FRONTIER_RETRY)인데 또 같은 자리라 또 막힘.
        self.assertEqual(refine.call_count, 2)
        self.assertEqual(mocks["tapped"], [])
        entry = mocks["ledger"].ledger.entries[f"{screen_id(home)}:{key_of('보상 받기')}"]
        self.assertEqual(entry.verdict, "not_found")
        refined = mocks["record"].call_args_list[0].kwargs["action"]["refine"]
        self.assertEqual(refined["blocked_seen_label"], "광고 보고 무료 뽑기")

    def test_refine_landing_on_the_same_button_is_tapped(self) -> None:
        home = SurveyDecision(screen_type="home", button_candidates=[_button("보상 받기", role="progression")])
        refine = Mock(return_value=TapRefineResult(
            found=True, initial=(30, 50), x=30, y=40, used_pass="zoom", seen_label="보상 받기",
        ))
        mocks = self._run(
            captures=[_capture(HOME_IMAGE), _capture(POPUP_IMAGE)],
            decisions=[home, SurveyDecision(screen_type="popup")],
            max_steps=2,
            extra_patches=(
                ("navigation.navigator.TAP_REFINE", True),
                ("navigation.navigator.refine_tap_target", refine),
            ),
        )
        self.assertEqual(mocks["tapped"], ["보상 받기"])


if __name__ == "__main__":
    unittest.main()
