"""고장 난 간선 빼기(A), 아는 길로 루트 돌아가기(B), 같은 화면 맴돌기 반복 시 포기(C)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.frontier import FrontierWalker, StepGoal
from navigation.graph import GraphStore
from navigation.ledger import LedgerStore
from navigation.navigator import (
    STUCK_CYCLE_GIVE_UP,
    _choose_action_for_goal,
    _count_cycle,
    _Journey,
)
from schemas import NavigationEdge, NormalizedPoint, SurveyButtonCandidate


@contextmanager
def _stores():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "navigation"
        with (
            patch("navigation.graph.ROOT", root),
            patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
            patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
            patch("navigation.graph.IMAGE_DIR", root / "representatives"),
        ):
            yield GraphStore(), LedgerStore(root / "ledger.json")


def _walker(graph, ledger, root="home"):
    return FrontierWalker(graph, ledger, max_back_taps=6, max_replan_attempts=5, root_screen_id=root)


def _edge(graph, source, target, key):
    return next(
        e for e in graph.graph.edges
        if e.source == source and e.target == target and e.action_key == key
    )


class BrokenEdgeTests(unittest.TestCase):
    def test_two_failures_remove_the_edge_until_it_works_again(self) -> None:
        with _stores() as (graph, _):
            graph.connect("team", "home", "뒤로(키)", "back")
            graph.connect("team", "shop", "상점", "shop")
            graph.connect("shop", "home", "홈", "home")
            back = _edge(graph, "team", "home", "back")
            self.assertEqual([e.action_key for e in graph.bfs_path("team", "home")], ["back"])

            graph.note_edge_result(back, ok=False)
            self.assertEqual(len(graph.bfs_path("team", "home")), 1)  # 한 번은 봐줌
            graph.note_edge_result(back, ok=False)
            self.assertEqual([e.action_key for e in graph.bfs_path("team", "home")], ["shop", "home"])
            # 저장되어 다음 세션(새 GraphStore)에도 빠져 있습니다.
            self.assertEqual([e.action_key for e in GraphStore().bfs_path("team", "home")], ["shop", "home"])

            graph.connect("team", "home", "뒤로(키)", "back")  # 실제로 다시 이어짐
            self.assertEqual([e.action_key for e in graph.bfs_path("team", "home")], ["back"])

    def test_shortcut_edges_are_tracked_in_memory(self) -> None:
        with _stores() as (graph, _):
            shortcut = NavigationEdge(source="team", target="home", action="홈 탭", action_key="tab3")
            for _ in range(2):
                graph.note_edge_result(shortcut, ok=False)
            self.assertIsNone(graph.bfs_path("team", "home", [shortcut]))

    def test_failed_replay_is_planned_around(self) -> None:
        with _stores() as (graph, ledger):
            graph.connect("home", "team", "팀", "team")
            graph.connect("team", "event", "뒤로(키)", "back")
            graph.connect("team", "shop", "상점", "shop")
            graph.connect("shop", "event", "이벤트", "event")
            ledger.seen("event", "action_x", "X")
            walker = _walker(graph, ledger, root="team")
            for _ in range(2):
                goal = walker.decide("team")
                self.assertEqual(goal.action_key, "back")
                walker.report_replay_step(executed=True, arrived_screen_id="team")  # 그대로
                walker.abort()
            goal = walker.decide("team")
            self.assertEqual((goal.kind, goal.action_key), ("replay", "shop"))


class ResetByKnownPathTests(unittest.TestCase):
    def test_reset_follows_the_known_path_to_root_then_resumes(self) -> None:
        with _stores() as (graph, ledger):
            graph.connect("team", "home", "셋째 탭 (별 아이콘)", "tab3")
            graph.connect("home", "event", "이벤트", "event")
            ledger.seen("event", "action_x", "X")
            walker = _walker(graph, ledger)
            walker.begin_reset_to_root()

            goal = walker.decide("team")
            self.assertEqual((goal.kind, goal.action_key, goal.expected_target_screen_id), ("reset", "tab3", "home"))
            walker.report_reset_step(executed=True, arrived_screen_id="home", used_home=False)
            self.assertFalse(walker.is_resetting)
            self.assertEqual(walker.decide("home").kind, "replay")

    def test_unknown_path_falls_back_to_buttons_and_root_ends_reset(self) -> None:
        with _stores() as (graph, ledger):
            walker = _walker(graph, ledger)
            walker.begin_reset_to_root()
            self.assertEqual(walker.decide("team"), StepGoal(kind="reset"))
            ledger.seen("home", "action_x", "X")
            self.assertEqual(walker.decide("home").kind, "frontier")  # 이미 루트면 리셋 끝
            self.assertFalse(walker.is_resetting)

    def test_navigator_uses_the_path_button_or_falls_back(self) -> None:
        def button(label):
            return SurveyButtonCandidate(
                label=label, role="structure", center=NormalizedPoint(x=0.5, y=0.9), confidence=1.0
            )
        from navigation.policy import navigation_action_key_from_label as key_of

        tab, home = button("셋째 탭 (별 아이콘)"), button("Home")
        goal = StepGoal(kind="reset", action_key=key_of(tab.label), expected_target_screen_id="home")
        with _stores() as (_, ledger):
            chosen, key, used_home = _choose_action_for_goal(goal, [home, tab], "team", ledger)
            self.assertEqual((chosen.label, used_home), (tab.label, False))
            chosen, _, used_home = _choose_action_for_goal(goal, [home], "team", ledger)
            self.assertEqual((chosen.label, used_home), ("Home", True))


class CycleGiveUpTests(unittest.TestCase):
    def test_third_cycle_on_the_same_screens_gives_up_there(self) -> None:
        with _stores() as (graph, ledger):
            ledger.seen("team", "action_a", "A")
            ledger.seen("event", "action_b", "B")
            walker = _walker(graph, ledger)
            walker._replay_target = "event"  # 맴돌 때 가려던 목표
            journey = _Journey()
            results = []
            for _ in range(STUCK_CYCLE_GIVE_UP):
                journey.stuck_cycle = {"team", "shop"}
                results.append(_count_cycle(journey, walker))
            self.assertEqual(results[:-1], [None] * (STUCK_CYCLE_GIVE_UP - 1))
            self.assertEqual(results[-1], 2)
            self.assertEqual(ledger.frontier_at("team") + ledger.frontier_at("event"), [])
            self.assertEqual((journey.cycle_hits, journey.stuck_cycle), ({}, set()))


if __name__ == "__main__":
    unittest.main()
