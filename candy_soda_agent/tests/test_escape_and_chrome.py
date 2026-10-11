"""탈출 사다리와 공통 UI(네비게이션 바) 테스트."""

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

from action_executor import check_game_package, restart_game
from image_utils import board_fingerprint, fingerprint_distance
from navigation.frontier import FrontierWalker
from navigation.graph import GraphStore
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore
from navigation.navigator import run_autodrive
from navigation.observer import screen_id
from navigation.policy import navigation_action_key_from_label
from schemas import (
    ExitLocation,
    NavigationEdge,
    NavigationGraph,
    ScreenNode,
    SurveyButtonCandidate,
    SurveyDecision,
)
from survey_metrics import compute_session_metrics
from survey_report import generate_survey_markdown
from tap_effect import TapEffect
from tap_refine import ExitTarget, find_exit_target
from ui_chrome import ChromeBands, UiChromeStore, can_be_global, detect_chrome_bands


RNG = np.random.default_rng(7)


def _textured(height: int, width: int, seed: int) -> np.ndarray:
    """큰 블록 무늬(축소해도 무늬가 남음). 실제 게임 화면처럼 저주파 무늬입니다."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, size=(max(1, height // 12), max(1, width // 12)), dtype=np.uint8)
    gray = np.kron(blocks, np.ones((12, 12), dtype=np.uint8))
    gray = np.pad(gray, ((0, max(0, height - gray.shape[0])), (0, max(0, width - gray.shape[1]))), mode="edge")
    gray = gray[:height, :width]
    return np.dstack([gray, gray, gray])


def _screen_with_bar(seed: int, bar: np.ndarray | None, height: int = 400, width: int = 240):
    image = _textured(height, width, seed)
    if bar is not None:
        image[height - bar.shape[0]:] = bar
    return image


def _button(label: str, x: float = 0.5, y: float = 0.5, role: str = "structure"):
    return SurveyButtonCandidate(label=label, role=role, center={"x": x, "y": y}, confidence=0.9)


# -- 공통 띠 / 공통 버튼 ------------------------------------------------------


class ChromeBandTests(unittest.TestCase):
    def test_shared_textured_bottom_bar_is_detected(self) -> None:
        bar = _textured(48, 240, seed=99)
        images = [_screen_with_bar(seed, bar) for seed in range(4)]
        bands = detect_chrome_bands(images, min_screens=3)
        self.assertTrue(bands.active)
        self.assertAlmostEqual(bands.bottom, 48, delta=6)
        self.assertEqual(bands.top, 0)
        self.assertGreaterEqual(bands.support, 3)

    def test_flat_dark_backgrounds_are_not_a_bar(self) -> None:
        # 어두운 단색 배경끼리는 줄무늬가 같아도 공통 UI가 아닙니다(실측 오탐 방지).
        images = []
        for seed in range(4):
            image = np.full((400, 240, 3), 30, np.uint8)
            image[100:250] = _textured(150, 240, seed)
            images.append(image)
        self.assertFalse(detect_chrome_bands(images, min_screens=3).active)

    def test_needs_enough_distinct_screens(self) -> None:
        bar = _textured(48, 240, seed=99)
        same = _screen_with_bar(1, bar)
        # 같은 화면이 쪼개진 노드 3개는 근거가 되지 않습니다.
        self.assertFalse(detect_chrome_bands([same, same.copy(), same.copy()], min_screens=3).active)

    def test_content_crop(self) -> None:
        bands = ChromeBands(top=10, bottom=40, height=400, width=240)
        image = np.zeros((400, 240, 3), np.uint8)
        self.assertEqual(bands.content(image).shape[0], 350)
        self.assertTrue(bands.contains(395))
        self.assertTrue(bands.contains(5))
        self.assertFalse(bands.contains(200))


class GlobalActionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "ui_chrome.json"

    def test_same_label_same_spot_on_three_screens_is_global(self) -> None:
        store = UiChromeStore(self.path, global_min_screens=3, max_spread_px=30)
        self.assertFalse(store.observe("a", "friends", "친구", 200, 1290))
        self.assertFalse(store.observe("b", "friends", "친구", 205, 1288))
        self.assertTrue(store.observe("c", "friends", "친구", 198, 1292))
        self.assertTrue(store.is_global("friends"))
        # 위치가 크게 다르면 같은 이름이어도 공통 버튼이 아닙니다.
        for node, y in (("a", 100), ("b", 600), ("c", 1100)):
            store.observe(node, "event", "이벤트", 300, y)
        self.assertFalse(store.is_global("event"))

    def test_close_and_confirm_buttons_are_never_global(self) -> None:
        store = UiChromeStore(self.path, global_min_screens=3)
        for node in "abcd":
            store.observe(node, "close", "닫기", 700, 60)
            store.observe(node, "ok", "확인", 378, 900)
        self.assertFalse(store.is_global("close"))
        self.assertFalse(store.is_global("ok"))
        self.assertFalse(can_be_global("Close (X)"))
        self.assertTrue(can_be_global("상점"))

    def test_two_screens_are_enough_inside_a_detected_bar(self) -> None:
        store = UiChromeStore(self.path, global_min_screens=3)
        store.bands = ChromeBands(bottom=150, height=1348, width=757)
        store.observe("a", "shop", "상점", 60, 1290)
        self.assertFalse(store.is_global("shop"))
        store.observe("b", "shop", "상점", 62, 1291)
        self.assertTrue(store.is_global("shop"))
        store.observe("a", "mid", "가운데", 300, 600)
        store.observe("b", "mid", "가운데", 300, 600)
        self.assertFalse(store.is_global("mid"))  # 띠 밖이면 3개 화면이 필요합니다.

    def test_teleport_edges_from_every_screen_showing_the_button(self) -> None:
        store = UiChromeStore(self.path, global_min_screens=3)
        for node in ("a", "b", "c"):
            store.observe(node, "home", "홈", 378, 1290)
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.mark_attempted(GLOBAL_SCREEN_ID, "home", "홈")
            ledger.finalize(GLOBAL_SCREEN_ID, "home", "known_screen", target_screen_id="a")
            edges = store.teleport_edges(ledger)
        self.assertEqual({(e.source, e.target) for e in edges}, {("b", "a"), ("c", "a")})
        store.save()
        reloaded = UiChromeStore(self.path, global_min_screens=3)
        self.assertEqual(reloaded.global_keys(), ["home"])

    def test_llm_nav_region_tags_cover_noisy_coordinates(self) -> None:
        # 조사 모델 좌표가 탭 하나 폭만큼 어긋나도(실측 '홈' 378 vs 275) 두 화면에서
        # bottom_nav라고 했고 화면 맨 아래에 있으면 공통 버튼입니다.
        store = UiChromeStore(self.path, global_min_screens=3)
        store.observe("a", "home", "홈", 378, 1287, region="bottom_nav", image_height=1348)
        self.assertFalse(store.is_global("home"))
        store.observe("b", "home", "홈", 275, 1282, region="bottom_nav", image_height=1348)
        self.assertTrue(store.is_global("home"))
        # 팝업 안 버튼을 bottom_nav라고 잘못 불러도 화면 아래쪽이 아니면 공통이 아닙니다.
        store.observe("a", "ask", "친구에게 요청", 491, 747, region="bottom_nav", image_height=1348)
        store.observe("b", "ask", "친구에게 요청", 116, 1182, region="bottom_nav", image_height=1348)
        self.assertFalse(store.is_global("ask"))

    def test_promote_reuses_an_earlier_per_screen_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.mark_attempted("a", "shop", "상점")
            ledger.finalize("a", "shop", "new_screen", target_screen_id="shop_screen")
            ledger.seen("b", "shop", "상점")
            closed, seeded = ledger.promote_to_global("shop", "상점")
            self.assertEqual((closed, seeded), (1, True))
            entry = ledger.ledger.entries[f"{GLOBAL_SCREEN_ID}:shop"]
            self.assertEqual((entry.attempts, entry.target_screen_id), (1, "shop_screen"))
            self.assertEqual(ledger.frontier_at(GLOBAL_SCREEN_ID), [])
            self.assertEqual(ledger.ledger.entries["b:shop"].verdict, "global")

    def test_bands_recomputed_only_when_nodes_change(self) -> None:
        store = UiChromeStore(self.path, min_screens=3)
        bar = _textured(48, 240, seed=99)
        images = {f"n{seed}": _screen_with_bar(seed, bar) for seed in range(3)}
        calls = []

        def load(node_id):
            calls.append(node_id)
            return images[node_id]

        self.assertTrue(store.update_bands(list(images), load))
        self.assertFalse(store.update_bands(list(images), load))
        self.assertEqual(len(calls), 3)


class ChromeMaskedIdentityTests(unittest.TestCase):
    def test_bar_difference_no_longer_splits_the_same_screen(self) -> None:
        content = _textured(400, 240, seed=1)
        first, second = content.copy(), content.copy()
        first[250:] = _textured(150, 240, seed=10)  # 아래 띠만 다릅니다(탭 강조 등)
        second[250:] = _textured(150, 240, seed=11)
        self.assertGreater(
            fingerprint_distance(board_fingerprint(first), board_fingerprint(second)), 0.05
        )
        decision = SurveyDecision(screen_type="home")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "navigation"
            with (
                patch("navigation.graph.ROOT", root),
                patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
                patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
                patch("navigation.graph.IMAGE_DIR", root / "representatives"),
                patch("navigation.graph.SCREEN_MATCH_MAX_BUTTON_DIFF", 0),
                patch("navigation.graph.SCREEN_HOG_MAX_DISTANCE", 0.0),  # 지문 단계만 시험
            ):
                plain = GraphStore()
                a = plain.observe(decision, first)
                b = plain.observe(
                    SurveyDecision(screen_type="home", summary="x",
                                   button_candidates=[_button("Shop")]),
                    second,
                )
                self.assertNotEqual(a.id, b.id)

            root2 = Path(directory) / "navigation2"
            with (
                patch("navigation.graph.ROOT", root2),
                patch("navigation.graph.GRAPH_PATH", root2 / "graph.json"),
                patch("navigation.graph.JOURNEY_PATH", root2 / "journeys.jsonl"),
                patch("navigation.graph.IMAGE_DIR", root2 / "representatives"),
                patch("navigation.graph.SCREEN_MATCH_MAX_BUTTON_DIFF", 0),
                patch("navigation.graph.SCREEN_HOG_MAX_DISTANCE", 0.0),  # 지문 단계만 시험
            ):
                masked = GraphStore()
                masked.set_chrome_bands(ChromeBands(bottom=150, height=400, width=240))
                a = masked.observe(decision, first)
                b = masked.observe(
                    SurveyDecision(screen_type="home", summary="x",
                                   button_candidates=[_button("Shop")]),
                    second,
                )
                self.assertEqual(a.id, b.id)


# -- 프론티어: 공통 버튼 --------------------------------------------------------


class GlobalFrontierTests(unittest.TestCase):
    def _walker(self, directory, edges, global_screens=None, extra=None):
        root = Path(directory) / "navigation"
        stack = ExitStack()
        stack.enter_context(patch("navigation.graph.ROOT", root))
        stack.enter_context(patch("navigation.graph.GRAPH_PATH", root / "graph.json"))
        stack.enter_context(patch("navigation.graph.JOURNEY_PATH", root / "j.jsonl"))
        stack.enter_context(patch("navigation.graph.IMAGE_DIR", root / "rep"))
        self.addCleanup(stack.close)
        store = GraphStore()
        for node in ("a", "b", "c"):
            store.graph.nodes[node] = ScreenNode(id=node, screen_type="x")
        store.graph.edges = edges
        ledger = LedgerStore(Path(directory) / "ledger.json")
        walker = FrontierWalker(
            store, ledger, max_back_taps=2, max_replan_attempts=1, root_screen_id="a",
            extra_edges=extra, global_screens=global_screens,
        )
        return walker, ledger

    def test_visible_untried_global_button_is_a_local_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            walker, ledger = self._walker(directory, [])
            ledger.seen(GLOBAL_SCREEN_ID, "shop", "상점")
            goal = walker.decide("a", ["shop"], {"shop"})
            self.assertEqual(goal.kind, "frontier")
            # 공통 버튼이 이 화면에 안 보이면 프론티어가 아닙니다(목표 화면이 없으면 수렴).
            self.assertIsNone(walker.decide("a", [], set()))
            self.assertEqual(walker.stop_reason, "converged")

    def test_walks_to_a_screen_where_untried_global_button_is_shown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            edges = [NavigationEdge(source="a", target="b", action="Go", action_key="go")]
            walker, ledger = self._walker(directory, edges, global_screens=lambda key: ["b"])
            ledger.seen(GLOBAL_SCREEN_ID, "shop", "상점")
            goal = walker.decide("a", [], set())
            self.assertEqual(goal.kind, "replay")
            self.assertEqual(goal.expected_target_screen_id, "b")

    def test_teleport_edges_shorten_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            edges = [NavigationEdge(source="a", target="b", action="Go", action_key="go")]
            teleport = [NavigationEdge(source="c", target="a", action="홈", action_key="home")]
            walker, ledger = self._walker(directory, edges, extra=lambda: teleport)
            ledger.seen("b", "k", "Event")
            goal = walker.decide("c", [], set())
            self.assertEqual(goal.kind, "replay")
            self.assertEqual(goal.action_key, "home")

    def test_abandoned_screen_is_not_retargeted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            edges = [NavigationEdge(source="a", target="b", action="Go", action_key="go")]
            walker, ledger = self._walker(directory, edges, global_screens=lambda key: ["b"])
            ledger.seen(GLOBAL_SCREEN_ID, "shop", "상점")
            walker.decide("a", [], set())
            walker.report_replay_step(executed=True, arrived_screen_id="c")  # 어긋남
            walker.report_replay_step(executed=True, arrived_screen_id="c")
            walker._give_up_on("b")
            self.assertIsNone(walker.decide("a", [], set()))


# -- 탈출 사다리: 헬퍼 ------------------------------------------------------------


class FakeRequest:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, client, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class ExitFinderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = np.zeros((1348, 757, 3), np.uint8)

    def test_button_found_on_full_screen_grid(self) -> None:
        request = FakeRequest(ExitLocation(kind="button", cell="", x=540, y=40, label="X", confidence=0.9))
        target = find_exit_target(object(), self.image, request=request)
        self.assertTrue(target.found)
        self.assertEqual(target.kind, "button")
        scale = 1024 / 1348
        self.assertEqual((target.x, target.y), (round(540 / scale), round(40 / scale)))
        self.assertIn("팝업 바깥", request.calls[0]["prompt"])

    def test_previous_failed_points_are_mentioned(self) -> None:
        request = FakeRequest(ExitLocation(kind="none"))
        find_exit_target(object(), self.image, failed_points=[(700, 50)], request=request)
        self.assertIn("다시 고르지 않는다", request.calls[0]["prompt"])

    def test_risky_none_and_errors_are_not_found(self) -> None:
        risky = FakeRequest(ExitLocation(kind="button", x=10, y=10, label="구매하기", confidence=1.0))
        self.assertFalse(find_exit_target(object(), self.image, request=risky).found)
        none = FakeRequest(ExitLocation(kind="none"))
        self.assertFalse(find_exit_target(object(), self.image, request=none).found)
        error = find_exit_target(object(), self.image, request=FakeRequest(RuntimeError("down")))
        self.assertFalse(error.found)
        self.assertIn("down", error.error)


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class RestartTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.adb = str(Path(directory.name) / "adb.exe")
        Path(self.adb).write_text("", encoding="utf-8")

    def test_restart_force_stops_then_launches(self) -> None:
        sleeps = []
        with patch(
            "action_executor.subprocess.run",
            side_effect=[_completed(), _completed(stdout="Events injected: 1")],
        ) as run_mock:
            ok = restart_game(
                package="com.king.candycrushsodasaga", wait=5, adb_path=self.adb,
                adb_serial="", dry_run=False, allow_taps=True, sleep=sleeps.append,
            )
        self.assertTrue(ok)
        first, second = (call.args[0] for call in run_mock.call_args_list)
        self.assertEqual(first[-3:], ["am", "force-stop", "com.king.candycrushsodasaga"])
        self.assertIn("monkey", second)
        self.assertEqual(sleeps, [1.0, 5])

    def test_restart_failure_raises(self) -> None:
        with patch(
            "action_executor.subprocess.run",
            side_effect=[_completed(), _completed(stdout="** No activities found to run, monkey aborted.")],
        ):
            with self.assertRaises(RuntimeError):
                restart_game(package="x", wait=0, adb_path=self.adb, adb_serial="",
                             dry_run=False, allow_taps=True, sleep=lambda s: None)

    def test_package_check_lists_similar_packages(self) -> None:
        with patch(
            "action_executor.subprocess.run",
            return_value=_completed(stdout="package:com.king.candycrushsodasaga\n"),
        ):
            self.assertTrue(check_game_package("com.king.candycrushsodasaga", self.adb, "")[0])
        with patch(
            "action_executor.subprocess.run",
            side_effect=[_completed(stdout=""), _completed(stdout="package:com.king.other\n")],
        ):
            ok, message = check_game_package("com.king.candycrushsodasaga", self.adb, "")
        self.assertFalse(ok)
        self.assertIn("com.king.other", message)


# -- 탈출 사다리: 탐색 루프 통합 ---------------------------------------------------


def _bundle(value: int):
    return SimpleNamespace(
        full_image=np.full((4, 4, 3), value, dtype=np.uint8),
        full_region={"left": 0, "top": 0, "width": 4, "height": 4},
    )


EFFECT = TapEffect(changed=True, final_change=0.5, peak_change=0.5, settled=True, waited=0.0, frames=1)


class EscapeLadderIntegrationTests(unittest.TestCase):
    """A(루트, Shop 버튼) -> B(버튼 없음, 갇힘). 그래프로 갈 수 없는 화면에 프론티어를 둡니다."""

    decision_a = SurveyDecision(screen_type="home", button_candidates=[_button("Shop")])
    decision_b = SurveyDecision(screen_type="shop")

    def _run(self, *, captures, decisions, max_steps, extra_patches=()):
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
            ):
                stack.enter_context(patch(target, value))
            for target, value in extra_patches:
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("navigation.navigator.time.sleep"))
            stack.enter_context(patch("navigation.navigator.capture_bundle", side_effect=captures))
            stack.enter_context(patch("navigation.navigator.analyze_survey_screen", side_effect=decisions))
            stack.enter_context(patch("navigation.navigator.load_recent_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.load_all_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=EFFECT))
            mocks["tap"] = stack.enter_context(patch("navigation.navigator.execute_button_tap", return_value=True))
            mocks["back"] = stack.enter_context(patch("navigation.navigator.execute_back_key", return_value=True))
            mocks["record"] = stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            mocks["report"] = stack.enter_context(
                patch("navigation.navigator.write_survey_report", return_value=Path("r.md"))
            )
            mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
            mss_mock.return_value.__enter__.return_value = object()
            for name, target in (("exit", "navigation.navigator.find_exit_target"),
                                 ("restart", "navigation.navigator.restart_game")):
                mocks[name] = stack.enter_context(patch(target))
            mocks["exit"].return_value = ExitTarget(found=True, kind="button", x=3, y=0, label="X", confidence=0.9)
            mocks["restart"].return_value = True
            LedgerStore().seen("screen_far_away", "key_event", "Event")
            mocks["summary"] = run_autodrive(object(), max_steps=max_steps, allow_taps=True)
        mocks["outcomes"] = [c.kwargs["action"]["outcome"] for c in mocks["record"].call_args_list]
        return mocks

    def test_ineffective_back_moves_on_to_exit_finder(self) -> None:
        a, b = _bundle(0), _bundle(200)
        mocks = self._run(
            captures=[a, b, b, b, a],
            decisions=[self.decision_a, self.decision_b, self.decision_b, self.decision_b, self.decision_a],
            max_steps=5,
            extra_patches=(
                ("navigation.navigator.BACK_KEY", "esc"),
                ("navigation.navigator.STUCK_BACK_KEY_MAX", 2),
                ("navigation.navigator.ESCAPE_EXIT_FINDER", True),
            ),
        )
        self.assertEqual(
            mocks["outcomes"],
            ["tap_executed", "stuck_back_key", "stuck_back_key", "stuck_exit_finder", "frontier_unreachable"],
        )
        self.assertEqual(mocks["back"].call_count, 2)
        tapped = mocks["tap"].call_args_list[-1].args[0]
        self.assertAlmostEqual(tapped.center.x, 0.75)  # x=3 / 폭 4
        graph = mocks["report"].call_args.kwargs["graph"]
        labels = {(e.source, e.target): e.action for e in graph.edges}
        self.assertEqual(labels[(screen_id(self.decision_b), screen_id(self.decision_a))], "X")

    def test_back_disabled_goes_straight_to_exit_finder(self) -> None:
        a, b = _bundle(0), _bundle(200)
        mocks = self._run(
            captures=[a, b, a],
            decisions=[self.decision_a, self.decision_b, self.decision_a],
            max_steps=3,
            extra_patches=(
                ("navigation.navigator.BACK_KEY", ""),
                ("navigation.navigator.ADB_PATH", ""),
            ),
        )
        mocks["back"].assert_not_called()
        self.assertEqual(mocks["outcomes"][1], "stuck_exit_finder")

    def test_long_streak_restarts_the_game_without_linking_screens(self) -> None:
        a, b = _bundle(0), _bundle(200)
        with tempfile.TemporaryDirectory() as directory:
            adb = Path(directory) / "adb.exe"
            adb.write_text("", encoding="utf-8")
            mocks = self._run(
                captures=[a, b, b, a],
                decisions=[self.decision_a, self.decision_b, self.decision_b, self.decision_a],
                max_steps=4,
                extra_patches=(
                    ("navigation.navigator.ADB_PATH", str(adb)),
                    ("navigation.navigator.ESCAPE_MAX_STREAK", 1),
                    ("navigation.navigator.APP_RESTART_MAX", 1),
                    ("navigation.navigator.check_adb", lambda: (True, "ok")),
                    ("navigation.navigator.check_game_package", lambda package: (True, package)),
                ),
            )
        self.assertEqual(
            mocks["outcomes"],
            ["tap_executed", "stuck_back_key", "stuck_restart", "frontier_unreachable"],
        )
        mocks["restart"].assert_called_once()
        graph = mocks["report"].call_args.kwargs["graph"]
        # 재시작은 "버튼"이 아니라서 B -> A 간선을 만들지 않습니다.
        pairs = {(e.source, e.target) for e in graph.edges}
        self.assertNotIn((screen_id(self.decision_b), screen_id(self.decision_a)), pairs)
        self.assertEqual(mocks["summary"].stop_kind, "unreachable")


class NavBarIntegrationTests(unittest.TestCase):
    def test_nav_tab_seen_on_three_screens_is_tapped_once_globally(self) -> None:
        # 탭 버튼은 우선순위가 낮은 safe_navigation이라 각 화면 고유 버튼을 먼저 누릅니다.
        tab = lambda: _button("상점", x=0.1, y=0.95, role="safe_navigation")  # noqa: E731
        decisions = [
            SurveyDecision(screen_type="home", button_candidates=[tab(), _button("Go A", 0.5, 0.3)]),
            SurveyDecision(screen_type="popup", button_candidates=[tab(), _button("Go B", 0.5, 0.4)]),
            SurveyDecision(screen_type="menu", button_candidates=[tab()]),
            SurveyDecision(screen_type="shop"),
            SurveyDecision(screen_type="shop"),
        ]
        captures = [_bundle(v) for v in (0, 60, 120, 180, 180)]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "navigation"
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
                ("navigation.navigator.BACK_KEY", ""),
                ("navigation.navigator.CHROME_DETECTION", False),
            ):
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("navigation.navigator.time.sleep"))
            stack.enter_context(patch("navigation.navigator.capture_bundle", side_effect=captures))
            stack.enter_context(patch("navigation.navigator.analyze_survey_screen", side_effect=decisions))
            stack.enter_context(patch("navigation.navigator.load_recent_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.load_all_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=EFFECT))
            stack.enter_context(patch("navigation.navigator.find_exit_target",
                                      return_value=ExitTarget(found=False)))
            tap_mock = stack.enter_context(patch("navigation.navigator.execute_button_tap", return_value=True))
            record_mock = stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            stack.enter_context(patch("navigation.navigator.write_survey_report", return_value=Path("r.md")))
            mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
            mss_mock.return_value.__enter__.return_value = object()
            run_autodrive(object(), max_steps=5, allow_taps=True)
            ledger = LedgerStore()

        tapped = [call.args[0].label for call in tap_mock.call_args_list]
        # 1·2번째 화면에서는 각 화면 버튼(Go A/Go B)을 누르고, 3번째 화면에서 공통으로
        # 판정된 '상점'을 한 번만 누릅니다. 1·2번째 화면의 '상점'은 다시 누르지 않습니다.
        self.assertEqual(tapped, ["Go A", "Go B", "상점"])
        shop_key = navigation_action_key_from_label("상점")
        global_entry = ledger.ledger.entries[f"{GLOBAL_SCREEN_ID}:{shop_key}"]
        self.assertEqual(global_entry.attempts, 1)
        per_screen = [
            entry for key, entry in ledger.ledger.entries.items()
            if entry.action_key == shop_key and entry.screen_id != GLOBAL_SCREEN_ID
        ]
        self.assertTrue(all(entry.verdict == "global" for entry in per_screen))
        global_flags = [
            call.kwargs["action"].get("global_action")
            for call in record_mock.call_args_list
            if call.kwargs["action"].get("type") == "tap_button"
        ]
        self.assertIn(True, global_flags)


# -- 보고서 / 지표 ------------------------------------------------------------------


class ReportAndMetricsTests(unittest.TestCase):
    def test_persistent_ui_section_lists_bar_and_global_buttons(self) -> None:
        graph = NavigationGraph(nodes={
            "map": ScreenNode(id="map", screen_type="home"),
            "shop": ScreenNode(id="shop", screen_type="shop"),
        })
        chrome = {
            "bands": {"top": 0, "bottom": 150, "height": 1348, "support": 3},
            "actions": {"shop": {"label": "상점", "positions": {"map": [60, 1290], "x": [60, 1290]}}},
            "global_actions": ["shop"],
        }
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.mark_attempted(GLOBAL_SCREEN_ID, "shop", "상점")
            ledger.finalize(GLOBAL_SCREEN_ID, "shop", "new_screen", target_screen_id="shop")
            ledger.seen(GLOBAL_SCREEN_ID, "friends", "친구")
            report = generate_survey_markdown([], graph, ledger=ledger, chrome=chrome)
        section = report.split("## Persistent UI", 1)[1].split("## ", 1)[0]
        self.assertIn("아래 150px", section)
        self.assertIn("**상점**: 2개 화면에서 보임, → Shop", section)
        self.assertIn("공통 버튼(네비게이션 바)", report)

    def test_metrics_count_escapes_and_exclude_restarts_from_taps(self) -> None:
        def rec(step, screen, action):
            return {"session_id": "s", "timestamp": f"t{step}", "step": step,
                    "mode": "autodrive", "screen_id": screen, "action": action}
        records = [
            rec(1, "A", {"type": "escape", "stage": "back", "executed": True, "tap_effect": {"changed": False}}),
            rec(2, "A", {"type": "escape", "stage": "exit_finder", "executed": True, "tap_effect": {"changed": True},
                         "button": {"label": "X"}}),
            rec(3, "B", {"type": "escape", "stage": "restart", "executed": True}),
            rec(4, "C", {"type": "record_only", "outcome": "frontier_converged"}),
        ]
        (metrics,) = compute_session_metrics(records, image_loader=None)
        self.assertEqual((metrics.escapes, metrics.restarts, metrics.back_keys), (3, 1, 1))
        self.assertEqual((metrics.taps, metrics.taps_changed), (2, 1))


if __name__ == "__main__":
    unittest.main()
