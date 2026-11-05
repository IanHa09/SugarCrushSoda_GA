"""캡처 전에 게임 창 올리기(W1), 팝업에 덮인 화면은 탭바 찾기 건너뛰기(T1), 고객센터 = 외부(X).

실측(run_2026-11-05_09-45-48, 애니팡 2): 1스텝 캡처에 게임 대신 Claude 앱이 찍혔고, 그 화면의 버튼
자리를 게임에서 눌러 환경설정이 열렸습니다. 이어서 탭바 찾기가 팝업 뒤로 비친 탭 6개를 후보에 넣어
눌러도 안 바뀌는 탭을 2번씩(12스텝) 눌렀습니다.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from action_executor import bring_game_to_front
from game_knowledge import active_profile
from navigation.navigator import _add_nav_tabs
from schemas import NormalizedPoint, SurveyButtonCandidate, SurveyDecision
from tap_refine import NavTabTarget
from test_play_policy import AutodriveHarness, _bundle, _button

IMAGE = np.zeros((1348, 757, 3), np.uint8)


class BringGameToFrontTests(unittest.TestCase):
    def test_raises_the_window_only_when_another_one_is_in_front(self) -> None:
        with patch("action_executor.active_application_name", return_value="LDPlayer"), \
                patch("action_executor._ensure_game_window_active") as ensure:
            self.assertFalse(bring_game_to_front("LDPlayer"))
        ensure.assert_not_called()
        with patch("action_executor.active_application_name", return_value="Claude"), \
                patch("action_executor._ensure_game_window_active") as ensure:
            self.assertTrue(bring_game_to_front("LDPlayer"))
        ensure.assert_called_once_with("LDPlayer")
        self.assertFalse(bring_game_to_front(""))  # 창 제목 설정이 없으면 아무것도 안 함


class FocusBeforeCaptureTests(AutodriveHarness):
    def _run_with_focus(self, focus):
        order = []

        def captures():
            for value in (0, 200):
                order.append("capture")
                yield _bundle(value)

        def tracked_focus():
            order.append("focus")
            return focus()

        home = SurveyDecision(screen_type="home", button_candidates=[_button("Shop")])
        output = io.StringIO()
        with redirect_stdout(output):
            self._run(
                captures=captures(),
                decisions=[home, SurveyDecision(screen_type="shop")],
                max_steps=2,
                extra_patches=(("navigation.navigator.bring_game_to_front", tracked_focus),),
            )
        return order, output.getvalue()

    def test_game_window_is_raised_before_every_capture(self) -> None:
        order, _ = self._run_with_focus(lambda: True)
        self.assertEqual(order, ["focus", "capture", "focus", "capture"])

    def test_focus_failure_is_logged_and_the_run_goes_on(self) -> None:
        def fail():
            raise RuntimeError("창 없음")

        order, printed = self._run_with_focus(fail)
        self.assertEqual(order.count("capture"), 2)
        self.assertIn("[FOCUS] 게임 창을 앞으로 올리지 못했습니다(창 없음)", printed)


def _candidate(label, region, role="safe_navigation"):
    return SurveyButtonCandidate(
        label=label, role=role, region=region, center=NormalizedPoint(x=0.9, y=0.05), confidence=0.9
    )


class CoveredByPopupTests(unittest.TestCase):
    found = [NavTabTarget("첫째 탭 (선물 상자)", 62, 1297, index=0),
             NavTabTarget("둘째 탭 (편지 봉투)", 130, 1297, selected=True, index=1)]

    def test_popup_close_button_skips_the_tab_finder(self) -> None:
        settings = SurveyDecision(screen_type="menu", button_candidates=[
            _candidate("고객 센터", "popup", role="structure"), _candidate("닫기", "popup"),
        ])
        node = SimpleNamespace(id="settings", screen_type="menu", tab_index=-1, tab_name="")
        with patch("navigation.navigator.find_nav_tabs", return_value=self.found) as finder:
            result = _add_nav_tabs(object(), settings, IMAGE, node, active_profile())
        finder.assert_not_called()
        self.assertIs(result, settings)
        self.assertEqual(node.tab_index, -1)  # 팝업 뒤로 비친 탭을 이 화면의 선택 탭으로 적지 않음

    def test_close_outside_a_popup_still_finds_tabs(self) -> None:
        page = SurveyDecision(screen_type="menu", button_candidates=[_candidate("X", "top_bar")])
        node = SimpleNamespace(id="page", screen_type="menu", tab_index=-1, tab_name="")
        with patch("navigation.navigator.find_nav_tabs", return_value=self.found) as finder:
            result = _add_nav_tabs(object(), page, IMAGE, node, active_profile())
        finder.assert_called_once()
        self.assertEqual([b.label for b in result.button_candidates][:1], ["첫째 탭 (선물 상자)"])


class CustomerCenterIsExternalTests(unittest.TestCase):
    def test_customer_center_labels_are_external(self) -> None:
        profile = active_profile()
        for label in ("고객 센터", "고객센터", "고객센터 바로가기"):
            self.assertTrue(profile.is_external(label), label)
        self.assertFalse(profile.is_external("센터"))


if __name__ == "__main__":
    unittest.main()
