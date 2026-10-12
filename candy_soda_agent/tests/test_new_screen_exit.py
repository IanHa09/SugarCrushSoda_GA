"""새 화면에서 나가기 버튼(X·닫기·홈 탭)을 따로 찾아 후보에 더하는지 검증합니다.

실측(run_2026-10-12): 조사 모델이 레벨 시작 팝업의 X와 상점의 탭바를 후보에서 빠뜨려,
분석을 재사용하는 동안 그 화면에서 나갈 버튼이 없었습니다."""

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from game_knowledge import active_profile
from navigation.frontier import StepGoal
from navigation.graph import GraphStore
from navigation.ledger import LedgerStore
from navigation.navigator import _add_exit_button, _choose_action_for_goal, run_autodrive
from navigation.policy import canonical_action_label
from schemas import SurveyDecision
from tap_refine import ExitTarget
from test_screen_index import _popup_over, _screen
from test_screen_reuse import EFFECT, _bundle, _decision, _paths

X_BUTTON = ExitTarget(found=True, kind="button", x=200, y=40, label="X", confidence=0.9)
IMAGE = np.zeros((400, 240, 3), np.uint8)


def _node(screen_type: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"screen_{screen_type}", screen_type=screen_type)


class ExitLabelTests(unittest.TestCase):
    def test_x_icon_means_close(self) -> None:
        for label in ("X", "X 버튼", "X 아이콘", "닫기"):
            self.assertEqual(canonical_action_label(label), "close", label)
        self.assertEqual(canonical_action_label("Box"), "box")

    def test_reset_prefers_close_over_a_tab_hidden_behind_the_popup(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "navigation.ledger.NAVIGATION_LEDGER_PATH", Path(directory) / "ledger.json"
        ):
            ledger = LedgerStore()
            popup = _decision("level", "홈", "X").button_candidates
            button, _, used_home = _choose_action_for_goal(StepGoal(kind="reset"), popup, "n", ledger)
            self.assertEqual((button.label, used_home), ("X", False))
            home_only = _decision("shop", "홈").button_candidates
            button, _, used_home = _choose_action_for_goal(StepGoal(kind="reset"), home_only, "n", ledger)
            self.assertEqual((button.label, used_home), ("홈", True))


class AddExitButtonTests(unittest.TestCase):
    def _add(self, screen_type: str, decision: SurveyDecision, target=X_BUTTON, enabled=True):
        with (
            patch("navigation.navigator.NEW_SCREEN_EXIT_FINDER", enabled),
            patch("navigation.navigator.find_exit_target", return_value=target) as finder,
        ):
            result = _add_exit_button(object(), decision, IMAGE, _node(screen_type), active_profile())
        return result, finder

    def test_found_exit_is_added_first_with_its_position(self) -> None:
        result, finder = self._add("level", _decision("level", "게임 시작!"))
        finder.assert_called_once()
        exit_button = result.button_candidates[0]
        self.assertEqual((exit_button.label, exit_button.role), ("X", "safe_navigation"))
        self.assertAlmostEqual(exit_button.center.x, 200 / 240)
        self.assertAlmostEqual(exit_button.center.y, 40 / 400)
        self.assertEqual(len(result.button_candidates), 2)

    def test_home_and_play_screens_or_switch_off_skip_the_search(self) -> None:
        for screen_type, enabled in (("home", True), ("play", True), ("level", False)):
            decision = _decision(screen_type)
            result, finder = self._add(screen_type, decision, enabled=enabled)
            finder.assert_not_called()
            self.assertIs(result, decision)

    def test_nothing_is_added_when_not_a_button_or_already_listed(self) -> None:
        outside = ExitTarget(found=True, kind="outside", x=5, y=5, label="팝업 바깥", confidence=0.9)
        for target, decision in (
            (outside, _decision("popup")),
            (ExitTarget(found=False), _decision("popup")),
            (X_BUTTON, _decision("popup", "닫기")),  # X와 닫기는 같은 행동(close)
        ):
            result, _ = self._add("popup", decision, target=target)
            self.assertIs(result, decision)


class AutodriveExitTests(unittest.TestCase):
    def test_popup_without_listed_exit_is_left_through_the_found_x(self) -> None:
        """지도(레벨 10) -> 레벨 팝업(후보: 게임 시작!뿐) -> X로 지도에 돌아와 탐색을 마칩니다."""
        home, popup = _decision("home", "레벨 10"), _decision("level", "게임 시작!")
        captures = [_bundle(_screen("map")), _bundle(_popup_over(_screen("map"))), _bundle(_screen("map"))]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "navigation"
        with ExitStack() as stack:
            for target, value in _paths(root) + (
                ("navigation.navigator.CAPTURE_MODE", "window"),
                ("navigation.navigator.DRY_RUN", False),
                ("navigation.navigator.TAP_REFINE", False),
                ("navigation.navigator.BACK_KEY", ""),
                ("navigation.navigator.ADB_PATH", ""),
                ("navigation.navigator.CHROME_DETECTION", False),
            ):
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("navigation.navigator.time.sleep"))
            stack.enter_context(patch("navigation.navigator.capture_bundle", side_effect=captures))
            stack.enter_context(patch("navigation.navigator.analyze_survey_screen", side_effect=[home, popup]))
            stack.enter_context(patch("navigation.navigator.load_recent_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.load_all_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=EFFECT))
            tap = stack.enter_context(patch("navigation.navigator.execute_button_tap", return_value=True))
            finder = stack.enter_context(patch("navigation.navigator.find_exit_target", return_value=X_BUTTON))
            stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            report = stack.enter_context(patch("navigation.navigator.write_survey_report", return_value=Path("r.md")))
            stack.enter_context(patch("navigation.navigator.mss.MSS")).return_value.__enter__.return_value = object()
            summary = run_autodrive(object(), max_steps=3, allow_taps=True)

            graph = report.call_args.kwargs["graph"]
            popup_id = next(node.id for node in graph.nodes.values() if node.screen_type == "level")
            cached = GraphStore().load_decision(popup_id)

        finder.assert_called_once()  # 홈 화면은 건너뛰고 팝업에서만 찾습니다.
        self.assertEqual([call.args[0].label for call in tap.call_args_list], ["레벨 10", "X"])
        self.assertIn((popup_id, "X"), {(edge.source, edge.action) for edge in graph.edges})
        self.assertEqual(cached.button_candidates[0].label, "X")  # 다음에 와도 X를 압니다.
        self.assertEqual(summary.stop_kind, "converged")


class SurveyPromptRuleTests(unittest.TestCase):
    def test_prompt_asks_for_exits_and_marks_product_cards_risky(self) -> None:
        import survey

        prompts = []

        def capture(client, **kwargs):
            prompts.append(kwargs["prompt"])
            return SurveyDecision(screen_type="home")

        with patch("survey.request_structured_vision", side_effect=capture):
            survey.analyze_survey_screen(object(), IMAGE)
        self.assertIn("칸 안의 상품 이름·기간·혜택 글자도", prompts[0])
        self.assertIn("닫기(X 아이콘)·뒤로 버튼이 보이면", prompts[0])
        self.assertIn("가려진 뒤 화면의 버튼(탭바 포함)은 넣지 않는다", prompts[0])


if __name__ == "__main__":
    unittest.main()
