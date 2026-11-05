"""탭 화면: 선택된 탭 이름 기록(A1), 빗나간 탭 판정(C1), 구조도 탭 묶기·화면 이름(B1·D1).

실측(run_2026-10-27_06-35-53): 친구 화면에서 탭 좌표가 반 칸 밀려 "넷째 탭"이 홈(셋째)을 눌렀고,
그 결과 "넷째 탭 → 홈"이 공통 버튼 지름길로 퍼져 리셋이 팀↔상점을 오갔습니다. 구조도에는 탭
화면끼리의 간선이 다 그려졌고, 서로 다른 안내 팝업 둘이 한 노드로 합쳐져 "확인"이 두 곳으로 갔습니다.
"""

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
from navigation.observer import screen_id
from navigation.policy import nav_tab_index, nav_tab_ordinal, navigation_action_key_from_label
from schemas import NavigationEdge, NavigationGraph, NavTab, NavTabs, ScreenNode, SurveyDecision
from survey_report import generate_survey_markdown
from tap_refine import NavTabTarget, find_nav_tabs
from test_play_policy import AutodriveHarness, _bundle, _button

IMAGE = np.zeros((1348, 757, 3), np.uint8)


class TabOrdinalTests(unittest.TestCase):
    def test_ordinal_labels_round_trip(self) -> None:
        self.assertEqual(nav_tab_index("둘째 탭 (분홍 파랑 사람 말풍선 아이콘)"), 1)
        self.assertEqual(nav_tab_index("다섯째 탭"), 4)
        self.assertEqual(nav_tab_index(f"{nav_tab_ordinal(9)} 탭 (별)"), 9)  # 열째부터는 "10번째"
        for label in ("상점", "홈", "탭바", "친구 탭바", "", None):
            self.assertIsNone(nav_tab_index(label), label)


class SelectedTabTests(unittest.TestCase):
    def test_finder_keeps_the_order_including_the_selected_tab(self) -> None:
        answer = NavTabs(tabs=[
            NavTab(icon="가게 아이콘", x=77, y=230),
            NavTab(text="친구", icon="사람 두 명", x=300, y=230, selected=True),
            NavTab(icon="별 아이콘", x=487, y=230),
        ])
        tabs = find_nav_tabs(object(), IMAGE, request=lambda *a, **k: answer)
        self.assertEqual([(t.label, t.index) for t in tabs],
                         [("첫째 탭 (가게 아이콘)", 0), ("친구", 1), ("셋째 탭 (별 아이콘)", 2)])

    def test_selected_tab_order_and_name_are_kept_on_the_node(self) -> None:
        decision = SurveyDecision(screen_type="popup")
        found = [NavTabTarget("첫째 탭 (가게)", 57, 1302, index=0),
                 NavTabTarget("친구", 187, 1302, selected=True, index=1)]
        named = SimpleNamespace(id="friends", screen_type="popup", tab_index=-1, tab_name="")
        with patch("navigation.navigator.find_nav_tabs", return_value=found):
            _add_nav_tabs(object(), decision, IMAGE, named, active_profile())
        self.assertEqual((named.tab_index, named.tab_name), (1, "친구"))

        # 선택된 탭 글자를 못 읽어 순서 이름이면 순서만 남깁니다.
        unread = SimpleNamespace(id="team", screen_type="popup", tab_index=-1, tab_name="")
        found[1] = NavTabTarget("둘째 탭 (사람 두 명)", 187, 1302, selected=True, index=1)
        with patch("navigation.navigator.find_nav_tabs", return_value=found):
            _add_nav_tabs(object(), decision, IMAGE, unread, active_profile())
        self.assertEqual((unread.tab_index, unread.tab_name), (1, ""))


HOME = SurveyDecision(screen_type="home", button_candidates=[_button("넷째 탭 (사람 세 명)")])


class WrongTabTests(AutodriveHarness):
    """넷째 탭을 눌렀는데 도착 화면에서 셋째 탭이 선택돼 있으면 빗나감입니다."""

    def _press_fourth_tab(self, arrived_selected: int):
        tabs = [
            [NavTabTarget("홈", 2, 3, selected=True, index=2), NavTabTarget("넷째 탭 (사람 세 명)", 3, 3, index=3)],
            [NavTabTarget(f"{nav_tab_ordinal(arrived_selected)} 탭", 1, 3, selected=True, index=arrived_selected)],
        ]
        mocks = self._run(
            captures=[_bundle(0), _bundle(200)],
            decisions=[HOME, SurveyDecision(screen_type="menu")],
            max_steps=2,
            extra_patches=(("navigation.navigator.find_nav_tabs", lambda *a, **k: tabs.pop(0)),),
        )
        key = navigation_action_key_from_label("넷째 탭")
        return mocks, mocks["ledger"].ledger.entries[f"{screen_id(HOME)}:{key}"]

    def test_other_tab_screen_is_a_miss_not_an_edge(self) -> None:
        mocks, entry = self._press_fourth_tab(arrived_selected=2)
        self.assertEqual(entry.verdict, "missed")  # 다시 시도 후보로 남음
        self.assertEqual([e.action for e in mocks["graph"].edges], [])
        home = mocks["graph"].nodes[screen_id(HOME)]
        self.assertEqual((home.tab_index, home.tab_name), (2, "홈"))

    def test_matching_tab_screen_is_a_normal_edge(self) -> None:
        mocks, entry = self._press_fourth_tab(arrived_selected=3)
        self.assertEqual(entry.verdict, "new_screen")
        self.assertEqual([e.action for e in mocks["graph"].edges], ["넷째 탭 (사람 세 명)"])


def _tab_graph() -> NavigationGraph:
    """06-35 run을 줄인 모양: 홈(셋째)·상점(첫째)·팀(넷째)이 탭 화면, 안내 팝업 둘, 레벨 창."""
    def node(node_id, screen_type, tab_index=-1, tab_name=""):
        return ScreenNode(id=node_id, screen_type=screen_type, tab_index=tab_index, tab_name=tab_name)

    def edge(source, target, action):
        return NavigationEdge(source=source, target=target, action=action,
                              action_key=navigation_action_key_from_label(action))

    return NavigationGraph(
        nodes={
            "home": node("home", "home", 2, "홈"),
            "shop": node("shop", "shop", 0, "상점"),
            "team": node("team", "popup", 3, "팀"),
            "info_a": node("info_a", "popup"),
            "info_b": node("info_b", "popup"),
            # 팝업 뒤로 비친 탭바를 읽어 선택 탭이 있어도, 그 탭을 눌러 온 게 아니면 탭 화면이 아닙니다.
            "level": node("level", "level", 2, "홈"),
        },
        edges=[
            edge("home", "shop", "첫째 탭 (분홍하양 점포 아이콘)"),
            edge("shop", "team", "넷째 탭 (사람 세 명 아이콘)"),     # 홈에서 직접 누른 적 없음
            edge("team", "shop", "첫째 탭 (가게 천막 아이콘)"),      # 탭끼리: 생략
            edge("team", "home", "셋째 탭 (노란 별)"),               # 탭끼리: 생략
            edge("shop", "shop", "메뉴"),                            # 같은 화면: 생략 아님
            edge("team", "info_a", "i 아이콘"),
            edge("shop", "info_b", "i 아이콘"),
            edge("info_a", "team", "확인"),
            edge("info_a", "shop", "확인"),                          # 합쳐짐 의심
            edge("home", "level", "8"),
            edge("level", "home", "뒤로(키)"),
            edge("level", "shop", "뒤로(키)"),                       # 뒤로는 의심에서 뺌
            edge("level", "info_b", "다섯째 탭 (달력)"),
            edge("info_b", "level", "다섯째 탭 (체크 달력)"),
        ],
    )


class StructureViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = generate_survey_markdown([], _tab_graph())
        self.diagram = self.report.split("```mermaid")[1].split("```")[0]

    def test_tab_screens_sit_side_by_side_under_the_root(self) -> None:
        self.assertIn('screen_home -->|"첫째 탭 (상점)"| screen_shop', self.diagram)
        self.assertIn('screen_home -->|"넷째 탭 (팀)"| screen_team', self.diagram)
        for source, target in (("shop", "team"), ("team", "shop"), ("team", "home")):
            self.assertNotRegex(self.diagram, rf'(?m)screen_{source} -\.?->\|"[^"]*"\| screen_{target}$')
        self.assertIn("탭 화면끼리 오간 간선 3개는 그리지 않았습니다", self.report)
        self.assertIn("눌러도 화면이 그대로인 버튼 1개 — 메뉴", self.report)

    def test_names_tabs_by_tab_text_and_numbers_repeated_types(self) -> None:
        for name in ('["홈"]', '["상점"]', '["팀"]', '["Popup(1)"]', '["Popup(2)"]', '["Level Start / Result"]'):
            self.assertIn(name, self.diagram)
        # 들어가 본 적 없는 탭은 처음 기록된 라벨 하나로 고정합니다.
        self.assertEqual(self.diagram.count("다섯째 탭 (달력)"), 2)
        self.assertNotIn("체크 달력", self.diagram)

    def test_same_button_to_two_screens_is_flagged_except_back(self) -> None:
        warnings = [line for line in self.report.splitlines() if line.startswith("- ⚠️ 합쳐짐 의심")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("**Popup(1)**의 '확인' → 팀, 상점", warnings[0])


if __name__ == "__main__":
    unittest.main()
