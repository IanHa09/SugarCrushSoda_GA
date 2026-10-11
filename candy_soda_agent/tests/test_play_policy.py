"""PLAY_POLICY=avoid: 플레이 진입 차단과 플레이 화면에서 나오기 테스트."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import active_profile
from navigation.graph import bfs_nearest_matching
from navigation.ledger import LedgerStore
from navigation.navigator import _exit_candidates, _Journey, _plan_escape, run_autodrive
from navigation.observer import screen_id
from navigation.policy import navigation_action_key_from_label
from schemas import NavigationEdge, NavigationGraph, ScreenNode, SurveyButtonCandidate, SurveyDecision
from tap_effect import TapEffect
from tap_refine import ExitTarget

EFFECT = TapEffect(changed=True, final_change=0.5, peak_change=0.5, settled=True, waited=0.0, frames=1)


def _button(label: str, x: float = 0.5, y: float = 0.5, role: str = "structure"):
    return SurveyButtonCandidate(label=label, role=role, center={"x": x, "y": y}, confidence=0.9)


def _bundle(value: int):
    return SimpleNamespace(
        full_image=np.full((4, 4, 3), value, dtype=np.uint8),
        full_region={"left": 0, "top": 0, "width": 4, "height": 4},
    )


MAP = SurveyDecision(screen_type="home", button_candidates=[_button("Go")])
BOARD = SurveyDecision(screen_type="play", button_candidates=[_button("부스터", 0.2, 0.9)])
QUIT_DIALOG = SurveyDecision(
    screen_type="popup",
    button_candidates=[
        _button("레벨 종료", 0.5, 0.6, role="safe_navigation"),
        _button("계속 플레이", 0.5, 0.8, role="progression"),
        _button("닫기", 0.9, 0.1, role="safe_navigation"),
    ],
)


class AutodriveHarness(unittest.TestCase):
    def _run(self, *, captures, decisions, max_steps, extra_patches=(), exit_target=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "navigation"
        mocks = {}
        with ExitStack() as stack:
            for target, value in (
                ("navigation.graph.ROOT", root),
                ("navigation.graph.GRAPH_PATH", root / "graph.json"),
                ("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
                ("navigation.graph.IMAGE_DIR", root / "representatives"),
                ("navigation.ledger.NAVIGATION_LEDGER_PATH", root / "ledger.json"),
                ("config.NAVIGATION_UI_CHROME_PATH", root / "ui_chrome.json"),
                ("navigation.navigator.CAPTURE_MODE", "window"),
                ("navigation.navigator.DRY_RUN", False),
                ("navigation.navigator.TAP_REFINE", False),
                ("navigation.navigator.CHROME_DETECTION", False),
                ("navigation.navigator.ADB_PATH", ""),
                ("navigation.navigator.BACK_KEY", "esc"),
                *extra_patches,
            ):
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("navigation.navigator.time.sleep"))
            stack.enter_context(patch("navigation.navigator.capture_bundle", side_effect=captures))
            stack.enter_context(patch("navigation.navigator.analyze_survey_screen", side_effect=decisions))
            stack.enter_context(patch("navigation.navigator.load_recent_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.load_all_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=EFFECT))
            mocks["exit"] = stack.enter_context(patch(
                "navigation.navigator.find_exit_target",
                return_value=exit_target or ExitTarget(found=False),
            ))
            mocks["tap"] = stack.enter_context(patch("navigation.navigator.execute_button_tap", return_value=True))
            mocks["back"] = stack.enter_context(patch("navigation.navigator.execute_back_key", return_value=True))
            mocks["record"] = stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            mocks["report"] = stack.enter_context(
                patch("navigation.navigator.write_survey_report", return_value=Path("r.md"))
            )
            mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
            mss_mock.return_value.__enter__.return_value = object()
            mocks["summary"] = run_autodrive(object(), max_steps=max_steps, allow_taps=True)
            mocks["ledger"] = LedgerStore()
        mocks["outcomes"] = [c.kwargs["action"]["outcome"] for c in mocks["record"].call_args_list]
        mocks["tapped"] = [c.args[0].label for c in mocks["tap"].call_args_list]
        mocks["graph"] = mocks["report"].call_args.kwargs["graph"]
        return mocks


class PlayEntryTests(AutodriveHarness):
    def test_play_button_is_recorded_but_never_tapped(self) -> None:
        home = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("플레이!", role="progression"), _button("Shop")],
        )
        mocks = self._run(
            captures=[_bundle(0), _bundle(200)],
            decisions=[home, SurveyDecision(screen_type="shop")],
            max_steps=2,
        )
        self.assertEqual(mocks["tapped"], ["Shop"])
        key = navigation_action_key_from_label("플레이!")
        entry = mocks["ledger"].ledger.entries[f"{screen_id(home)}:{key}"]
        self.assertEqual((entry.verdict, entry.attempts), ("play_entry", 0))
        self.assertEqual(mocks["summary"].stop_kind, "converged")


class PlayExitFlowTests(AutodriveHarness):
    def test_board_is_left_through_back_then_exit_label(self) -> None:
        # 지도 --Go--> 보드 --뒤로--> 나가기 확인창 --레벨 종료--> 지도
        mocks = self._run(
            captures=[_bundle(0), _bundle(100), _bundle(150), _bundle(0)],
            decisions=[MAP, BOARD, QUIT_DIALOG, MAP],
            max_steps=4,
        )
        self.assertEqual(
            mocks["outcomes"],
            ["tap_executed", "play_back", "play_exit_label", "frontier_converged"],
        )
        self.assertEqual(mocks["tapped"], ["Go", "레벨 종료"])
        graph = mocks["graph"]
        board_id = screen_id(BOARD)
        dialog_id = screen_id(QUIT_DIALOG)
        self.assertEqual(
            {(e.source, e.target, e.action) for e in graph.edges},
            {
                (screen_id(MAP), board_id, "Go"),
                (board_id, dialog_id, "뒤로(키)"),
                (dialog_id, screen_id(MAP), "레벨 종료"),
            },
        )
        # 플레이 화면과 나가는 도중 본 화면의 버튼은 프론티어로 등록하지 않습니다.
        screens = {entry.screen_id for entry in mocks["ledger"].ledger.entries.values()}
        self.assertEqual(screens, {screen_id(MAP)})
        self.assertTrue(graph.nodes[dialog_id].play_flow)
        self.assertFalse(graph.nodes[screen_id(MAP)].play_flow)

    def test_session_that_starts_on_the_board_leaves_before_exploring(self) -> None:
        mocks = self._run(
            captures=[_bundle(100), _bundle(0), _bundle(200)],
            decisions=[BOARD, MAP, SurveyDecision(screen_type="shop")],
            max_steps=3,
        )
        # 보드는 루트가 되지 않고, 지도에 나온 뒤에야 탐색(Go)을 시작합니다.
        self.assertEqual(mocks["outcomes"][:2], ["play_back", "tap_executed"])
        self.assertEqual(mocks["tapped"], ["Go"])

    def test_exit_finder_gets_the_play_hint_when_back_is_off(self) -> None:
        mocks = self._run(
            captures=[_bundle(100)],
            decisions=[BOARD],
            max_steps=1,
            extra_patches=(("navigation.navigator.BACK_KEY", ""),),
            exit_target=ExitTarget(found=True, kind="button", x=1, y=1, label="일시정지", confidence=0.9),
        )
        self.assertEqual(mocks["exit"].call_args.kwargs["hint"], active_profile().play_exit_hint)
        self.assertEqual(mocks["tapped"], ["일시정지"])
        self.assertEqual(mocks["outcomes"], ["play_exit_finder"])

    def test_stops_when_nothing_gets_out_of_play(self) -> None:
        mocks = self._run(
            captures=[_bundle(100)],
            decisions=[BOARD],
            max_steps=3,
            extra_patches=(("navigation.navigator.BACK_KEY", ""),),
        )
        self.assertEqual(mocks["summary"].stop_kind, "play_stuck")
        self.assertEqual(mocks["outcomes"], ["play_exit_failed"])
        self.assertEqual(mocks["tapped"], [])


class LegacyRunGuardTests(unittest.TestCase):
    def test_run_built_with_old_screen_types_stops_before_any_api_call(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "navigation"
        root.mkdir(parents=True)
        graph = NavigationGraph(nodes={"old": ScreenNode(id="old", screen_type="map_or_level_select")})
        (root / "graph.json").write_text(graph.model_dump_json(), encoding="utf-8")
        with ExitStack() as stack:
            for target, value in (
                ("navigation.graph.ROOT", root),
                ("navigation.graph.GRAPH_PATH", root / "graph.json"),
                ("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
                ("navigation.graph.IMAGE_DIR", root / "representatives"),
                ("navigation.ledger.NAVIGATION_LEDGER_PATH", root / "ledger.json"),
                ("config.NAVIGATION_UI_CHROME_PATH", root / "ui_chrome.json"),
                ("navigation.navigator.CAPTURE_MODE", "window"),
                ("navigation.navigator.DRY_RUN", False),
                ("navigation.navigator.ADB_PATH", ""),
                ("navigation.navigator.BACK_KEY", ""),
            ):
                stack.enter_context(patch(target, value))
            analyze = stack.enter_context(patch("navigation.navigator.analyze_survey_screen"))
            summary = run_autodrive(object(), max_steps=3, allow_taps=True)
        self.assertEqual(summary.stop_kind, "blocked")
        analyze.assert_not_called()


class PlayGuardUnitTests(unittest.TestCase):
    def test_exit_finder_choosing_play_is_ignored(self) -> None:
        journey = _Journey()
        with patch(
            "navigation.navigator.find_exit_target",
            return_value=ExitTarget(found=True, kind="button", x=1, y=1, label="플레이", confidence=0.9),
        ), patch("navigation.navigator.BACK_KEY", ""), patch("navigation.navigator.ADB_PATH", ""):
            plan = _plan_escape(
                object(), journey, SurveyDecision(), np.zeros((4, 4, 3), np.uint8),
                profile=active_profile(),
            )
        self.assertIsNone(plan)

    def test_continue_play_is_not_an_exit_candidate(self) -> None:
        labels = [b.label for b in _exit_candidates(QUIT_DIALOG, set(), active_profile())]
        self.assertNotIn("계속 플레이", labels)
        self.assertIn("닫기", labels)

    def test_close_play_entry_keeps_earlier_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            self.assertTrue(ledger.close_play_entry("a", "play", "플레이"))
            self.assertFalse(ledger.close_play_entry("a", "play", "플레이"))
            self.assertEqual(ledger.frontier_at("a"), [])
            # 정책을 켜기 전에 이미 눌러 본 항목은 결과를 지우지 않습니다.
            ledger.mark_attempted("b", "play", "플레이")
            ledger.finalize("b", "play", "new_screen", target_screen_id="board")
            self.assertFalse(ledger.close_play_entry("b", "play", "플레이"))
            self.assertEqual(ledger.ledger.entries["b:play"].verdict, "new_screen")
            # 열려 있던 미시도 항목(예전 run)은 닫힙니다.
            ledger.seen("c", "play", "플레이")
            self.assertTrue(ledger.close_play_entry("c", "play", "플레이"))
            self.assertEqual(ledger.closed_without_tap(), 2)

    def test_path_planning_skips_avoided_nodes(self) -> None:
        graph = NavigationGraph(edges=[
            NavigationEdge(source="a", target="board", action="x", action_key="x"),
            NavigationEdge(source="board", target="goal", action="y", action_key="y"),
            NavigationEdge(source="a", target="b", action="p", action_key="p"),
            NavigationEdge(source="b", target="c", action="q", action_key="q"),
            NavigationEdge(source="c", target="goal", action="r", action_key="r"),
        ])
        short = bfs_nearest_matching(graph, "a", lambda n: n == "goal")
        self.assertEqual([e.target for e in short], ["board", "goal"])
        safe = bfs_nearest_matching(graph, "a", lambda n: n == "goal", avoid=lambda n: n == "board")
        self.assertEqual([e.target for e in safe], ["b", "c", "goal"])


if __name__ == "__main__":
    unittest.main()
