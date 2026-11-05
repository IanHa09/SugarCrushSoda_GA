"""팝업은 들렀다 나가는 곳(P1)과 광고·뽑기 버튼 기록만 하기(R1).

실측: 캔디 활동 안내 팝업에서 '확인'으로 나간 뒤 'X'를 따로 눌러 보려고 팝업을 다시 열었고,
애니팡 환경설정에서는 닫기가 탭보다 뒤 순서라 끝까지 안 눌렸습니다. 이제 팝업의 나가기 버튼
(X·닫기·확인·나중에·취소)은 프론티어가 아니라 나가는 수단이고, 팝업에서 더 볼 것이 없으면
X·닫기 → 확인류 → 나가기 찾기(바깥 어두운 곳) → 뒤로 → 재시작 순으로 화면이 바뀔 때까지 씁니다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import active_profile
from navigation.navigator import _popup_exit_keys
from navigation.observer import screen_id
from navigation.policy import navigation_action_key_from_label as key_of
from schemas import NormalizedPoint, SurveyButtonCandidate, SurveyDecision
from test_play_policy import AutodriveHarness, _bundle, _button


def _in_popup(label: str, confidence: float = 0.9, role: str = "safe_navigation"):
    return SurveyButtonCandidate(
        label=label, role=role, region="popup", center=NormalizedPoint(x=0.5, y=0.5),
        confidence=confidence,
    )


HOME = SurveyDecision(screen_type="home", button_candidates=[_button("A"), _button("B")])
# '확인'이 확신도가 더 높아도 X·닫기를 먼저 씁니다.
INFO_POPUP = SurveyDecision(
    screen_type="popup", button_candidates=[_in_popup("확인", 0.95), _in_popup("X", 0.6)]
)


class PopupExitKeysTests(unittest.TestCase):
    def test_exit_buttons_inside_a_popup_are_not_frontier(self) -> None:
        settings = SurveyDecision(screen_type="menu", button_candidates=[
            _in_popup("쿠폰 등록", role="structure"), _in_popup("닫기"), _in_popup("확인"),
        ])
        self.assertEqual(_popup_exit_keys(settings, active_profile()), {key_of("닫기"), key_of("확인")})

    def test_screen_without_a_popup_exit_is_not_a_popup(self) -> None:
        # 캔디 친구 탭 화면: 팝업 영역에는 진행 버튼뿐이고 i는 위쪽 바에 있습니다.
        friends = SurveyDecision(screen_type="popup", button_candidates=[
            _in_popup("친구 소식 보기", role="progression"),
            SurveyButtonCandidate(label="i 아이콘", role="safe_navigation", region="top_bar",
                                  center=NormalizedPoint(x=0.1, y=0.05), confidence=0.9),
        ])
        self.assertEqual(_popup_exit_keys(friends, active_profile()), set())


class LeavePopupTests(AutodriveHarness):
    def test_info_popup_is_left_with_x_first_and_its_exits_are_not_frontier(self) -> None:
        # 홈 --A--> 안내 팝업 --X--> 홈 --B--> …
        mocks = self._run(
            captures=[_bundle(0), _bundle(200), _bundle(0)],
            decisions=[HOME, INFO_POPUP, HOME],
            max_steps=3,
        )
        self.assertEqual(mocks["tapped"], ["A", "X", "B"])
        popup_id, home_id = screen_id(INFO_POPUP), screen_id(HOME)
        self.assertIn((popup_id, home_id, "X"), {(e.source, e.target, e.action) for e in mocks["graph"].edges})
        screens = {entry.screen_id for entry in mocks["ledger"].ledger.entries.values()}
        self.assertNotIn(popup_id, screens)  # X·확인은 원장(프론티어)에 없음

    def test_next_way_out_is_tried_until_the_screen_changes(self) -> None:
        # X를 눌러도 그대로 → 확인 → 홈. (그대로인 화면은 다시 분석하지 않아 분석 결과는 셋)
        mocks = self._run(
            captures=[_bundle(0), _bundle(200), _bundle(200), _bundle(0)],
            decisions=[HOME, INFO_POPUP, HOME],
            max_steps=4,
        )
        self.assertEqual(mocks["tapped"], ["A", "X", "확인", "B"])

    def test_popup_with_something_to_see_is_explored_before_leaving(self) -> None:
        settings = SurveyDecision(screen_type="menu", button_candidates=[
            _in_popup("닫기"), _in_popup("쿠폰 등록", role="structure"),
        ])
        mocks = self._run(
            captures=[_bundle(0), _bundle(200)],
            decisions=[HOME, settings],
            max_steps=2,
        )
        self.assertEqual(mocks["tapped"], ["A", "쿠폰 등록"])
        keys = {e.action_key for e in mocks["ledger"].ledger.entries.values()
                if e.screen_id == screen_id(settings)}
        self.assertEqual(keys, {key_of("쿠폰 등록")})


class AdAndGachaAreNotTappedTests(unittest.TestCase):
    def test_ad_and_gacha_labels_are_record_only(self) -> None:
        profile = active_profile()
        for label in ("광고 보고 하트 받기", "광고보기", "Watch Ad", "무료 뽑기", "10회 뽑기", "가챠"):
            self.assertTrue(profile.never_tap(label), label)
        for label in ("확률", "Add friend", "쿠폰 등록"):
            self.assertFalse(profile.never_tap(label), label)


if __name__ == "__main__":
    unittest.main()
