"""navigation 패키지 단위/통합 테스트."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.graph import GraphStore
from navigation.observer import screen_id
from navigation.policy import (
    choose_button,
    navigation_action_id,
    navigation_action_key_from_label,
)
from navigation.navigator import run_autodrive
from schemas import (
    NavigationEdge,
    NavigationGraph,
    ScreenNode,
    SurveyButtonCandidate,
    SurveyDecision,
)
from tap_effect import TapEffect


def _button(label: str, role: str = "structure") -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label,
        role=role,
        center={"x": 0.5, "y": 0.5},
        confidence=0.9,
    )


@contextmanager
def _patched_storage_paths(directory: str):
    """GraphStore/LedgerStore가 실제 output/ 대신 임시 폴더에 쓰도록 경로를 바꿉니다.

    (1단계: 저장 경로가 게임·run별로 분리되면서, 통합 테스트가 실제 프로젝트의
    output/ 디렉터리를 건드리지 않으려면 이 패치가 필요해졌습니다.)"""

    root = Path(directory) / "navigation"
    with (
        patch("navigation.graph.ROOT", root),
        patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
        patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
        patch("navigation.graph.IMAGE_DIR", root / "representatives"),
        patch("navigation.ledger.NAVIGATION_LEDGER_PATH", root / "ledger.json"),
        patch("config.NAVIGATION_UI_CHROME_PATH", root / "ui_chrome.json"),
    ):
        yield


def _tiny_bundle(value: int = 0) -> SimpleNamespace:
    """observe()가 실제로 cv2.imwrite를 호출하므로 진짜(작은) 이미지가 필요합니다.

    value가 다르면 픽셀이 다른 화면입니다. 같은 value의 번들이 연속으로 오면
    navigator는 "화면이 그대로"(탭 빗나감)로 판정합니다."""
    return SimpleNamespace(
        full_image=np.full((4, 4, 3), value, dtype=np.uint8),
        full_region={"left": 0, "top": 0, "width": 4, "height": 4},
    )


@contextmanager
def _no_refine_no_wait(changed: bool = True):
    """확대 재확인(API)과 탭 뒤 화면 대기(캡처)를 끄고, 뒤로 키도 끕니다.
    통합 테스트는 탐색 루프의 흐름만 보므로 이 외부 경계를 막습니다."""

    effect = TapEffect(
        changed=changed, final_change=0.5 if changed else 0.0, peak_change=0.5,
        settled=True, waited=0.0, frames=1,
    )
    with (
        patch("navigation.navigator.TAP_REFINE", False),
        patch("navigation.navigator.BACK_KEY", ""),
        patch("navigation.navigator.wait_for_settle", return_value=effect),
        patch("navigation.navigator.time.sleep"),
    ):
        yield


class ScreenIdentityTests(unittest.TestCase):
    def test_parallel_section_id_ignores_llm_wording_changes(self) -> None:
        """문구가 달라도 병렬 섹션 화면은 같은 ID로 묶입니다."""
        first = SurveyDecision(
            screen_type="home",
            summary="Island map with Level 10",
            visible_text=["Level 10", "Full"],
            button_candidates=[_button("Settings (gear)")],
        )
        second = SurveyDecision(
            screen_type="home",
            summary="World map and highlighted level",
            visible_text=["Level 11"],
            button_candidates=[_button("Settings (cog)")],
        )

        self.assertEqual(screen_id(first), screen_id(second))


class ParallelPolicyTests(unittest.TestCase):
    def test_selects_another_safe_button_after_equivalent_action_used(self) -> None:
        """이미 쓴 행동과 동등한 버튼은 건너뛰고 다른 안전 버튼을 고릅니다."""
        settings = _button("Settings (gear)")
        shop = _button("Shop (bottom-left icon)")
        decision = SurveyDecision(
            screen_type="home",
            button_candidates=[settings, shop],
        )
        node_id = screen_id(decision)
        used_actions = {navigation_action_id(node_id, settings)}

        selected = choose_button(decision, node_id, used_actions)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.label, shop.label)

    def test_action_key_normalizes_icon_wording(self) -> None:
        """아이콘 표기가 달라도 같은 행동 키로 정규화됩니다."""
        self.assertEqual(
            navigation_action_key_from_label("Settings (gear)"),
            navigation_action_key_from_label("Settings (cog icon)"),
        )

    def test_action_key_ignores_trailing_ui_nouns(self) -> None:
        """UI 명사가 괄호 밖으로 나와도 같은 행동 키로 정규화됩니다.

        실제 로그에서 같은 Teams 버튼이 "Teams (bottom tab)"와
        "Teams tab (bottom nav)"로 갈려 화면이 쪼개졌습니다."""
        self.assertEqual(
            navigation_action_key_from_label("Teams (bottom tab)"),
            navigation_action_key_from_label("Teams tab (bottom nav)"),
        )

    def test_action_key_keeps_label_made_only_of_ui_nouns(self) -> None:
        """라벨이 UI 명사뿐이면(예: "Menu") 빈 키로 뭉개지지 않습니다."""
        self.assertEqual(
            navigation_action_key_from_label("Menu"),
            navigation_action_key_from_label("Menu (top-right)"),
        )
        self.assertNotEqual(
            navigation_action_key_from_label("Menu"),
            navigation_action_key_from_label("Shop"),
        )


class GraphNormalizationTests(unittest.TestCase):
    def test_duplicate_edges_are_merged_and_nodes_are_left_alone(self) -> None:
        """불러올 때 같은 행동의 중복 간선만 정리합니다.

        전에는 여기서 화면 타입이 같은 노드를 타입 이름 하나로 합치기도 했는데
        (SINGLETON_SCREEN_TYPES), 실측에서 같은 타입이라도 실제로 다른 화면인
        경우가 있어 과병합이었습니다. 같은 화면인지는 이제 이미지 지문이 판단하므로
        저장된 노드는 그대로 둡니다."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "navigation"
            graph_path = root / "graph.json"
            journey_path = root / "journeys.jsonl"
            image_dir = root / "representatives"
            root.mkdir(parents=True)

            graph = NavigationGraph(
                nodes={
                    "old_a": ScreenNode(
                        id="old_a",
                        screen_type="home",
                        summary="Map screen",
                        visits=1,
                    ),
                    "old_b": ScreenNode(
                        id="old_b",
                        screen_type="home",
                        summary="A more detailed world map screen",
                        visits=2,
                    ),
                },
                edges=[
                    NavigationEdge(
                        source="old_a",
                        target="old_b",
                        action="Settings (gear)",
                    ),
                    NavigationEdge(
                        source="old_a",
                        target="old_b",
                        action="Settings (cog)",
                    ),
                ],
            )
            graph_path.write_text(
                graph.model_dump_json(indent=2),
                encoding="utf-8",
            )

            with (
                patch("navigation.graph.ROOT", root),
                patch("navigation.graph.GRAPH_PATH", graph_path),
                patch("navigation.graph.JOURNEY_PATH", journey_path),
                patch("navigation.graph.IMAGE_DIR", image_dir),
            ):
                store = GraphStore()

            # 노드는 합치지 않습니다.
            self.assertEqual(list(store.graph.nodes), ["old_a", "old_b"])
            self.assertEqual(store.graph.nodes["old_b"].visits, 2)
            # "Settings (gear)"와 "Settings (cog)"는 같은 행동이라 간선은 하나로.
            self.assertEqual(len(store.graph.edges), 1)
            self.assertEqual(store.graph.edges[0].source, "old_a")
            self.assertEqual(store.graph.edges[0].target, "old_b")
            self.assertIn(
                f"old_a:{navigation_action_key_from_label('Settings')}",
                store.explored_actions(),
            )


class AutodriveIntegrationTests(unittest.TestCase):
    """2단계 이후: GraphStore/LedgerStore를 실제로(임시 폴더에) 돌려 프론티어
    기반 루프 전체를 검증합니다. 화면·버튼 판단만 외부 경계(캡처/LLM/탭 실행)를
    모킹합니다."""

    def test_each_screen_is_saved_as_a_survey_record(self) -> None:
        """안전 버튼 후보가 없으면 전역 프론티어도 비어 있어 1스텝만에
        "탐색 완료"로 멈추고, 그 화면은 survey 기록에 남습니다."""
        decision = SurveyDecision(screen_type="home")  # 버튼 후보 없음
        bundle = _tiny_bundle()

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle", return_value=bundle),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    return_value=decision,
                ),
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen") as record_mock,
                patch(
                    "navigation.navigator.write_survey_report",
                    return_value=Path("output/game_survey_report.md"),
                ) as report_mock,
            ):
                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(object(), max_steps=1, allow_taps=True)

            record_mock.assert_called_once()
            self.assertEqual(
                record_mock.call_args.kwargs["action"]["outcome"],
                "frontier_converged",
            )
            self.assertEqual(
                record_mock.call_args.kwargs["screen_id"], screen_id(decision)
            )
            # 보고서는 프레임마다가 아니라 종료 시 한 번, 실제 그래프·원장과 함께 만듭니다.
            report_mock.assert_called_once()
            reported_graph = report_mock.call_args.kwargs["graph"]
            self.assertEqual(len(reported_graph.nodes), 1)
            self.assertIn("ledger", report_mock.call_args.kwargs)

    def test_stops_before_calling_the_api_when_taps_are_disabled(self) -> None:
        """탭이 막혀 있으면 API 호출 전에 탐색을 시작하지 않습니다."""
        with (
            patch("navigation.navigator.CAPTURE_MODE", "window"),
            patch("navigation.navigator.DRY_RUN", True),
            patch("navigation.navigator.GraphStore") as store_mock,
            patch("navigation.navigator.analyze_survey_screen") as analyze_mock,
            patch("navigation.navigator.write_survey_report") as report_mock,
        ):
            run_autodrive(object(), max_steps=5, allow_taps=False)

        store_mock.assert_not_called()
        analyze_mock.assert_not_called()
        report_mock.assert_not_called()

    def test_cancel_check_stops_before_first_capture_and_still_writes_report(
        self,
    ) -> None:
        """이미 취소된 상태면 캡처/분석 없이 멈추지만 보고서는 남깁니다."""

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle") as capture_mock,
                patch("navigation.navigator.analyze_survey_screen") as analyze_mock,
                patch("navigation.navigator.record_survey_screen") as record_mock,
                patch(
                    "navigation.navigator.write_survey_report",
                    return_value=Path("output/game_survey_report.md"),
                ) as report_mock,
            ):
                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(
                    object(),
                    max_steps=5,
                    allow_taps=True,
                    cancel_check=lambda: "테스트에서 취소를 요청했습니다.",
                )

            capture_mock.assert_not_called()
            analyze_mock.assert_not_called()
            record_mock.assert_not_called()
            report_mock.assert_called_once()

    def test_cancel_check_is_forwarded_to_button_tap(self) -> None:
        """execute_button_tap에도 cancel_check가 실제로 전달돼야 합니다."""

        decision = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("Shop")],
        )
        bundle = _tiny_bundle()
        sentinel_cancel_check = lambda: None

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle", return_value=bundle),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    return_value=decision,
                ),
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen"),
                patch("navigation.navigator.write_survey_report"),
                patch(
                    "navigation.navigator.execute_button_tap",
                    return_value=True,
                ) as tap_mock,
                _no_refine_no_wait(),
            ):
                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(
                    object(),
                    max_steps=1,
                    allow_taps=True,
                    cancel_check=sentinel_cancel_check,
                )

            tap_mock.assert_called_once()
            self.assertIs(
                tap_mock.call_args.kwargs["cancel_check"],
                sentinel_cancel_check,
            )

    def test_unresponsive_button_is_retried_once_then_closed(self) -> None:
        """눌렀는데 화면 픽셀이 그대로인 버튼은 빗나감(missed)으로 보고 한 번 더 누릅니다.
        다시 눌러도 그대로면 no_change로 닫혀 프론티어에서 빠지고, 같은 화면으로
        돌아오는 간선은 만들지 않습니다(예전엔 빗나간 탭마다 self-loop 간선이 생겼습니다)."""

        decision = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("Settings", role="structure")],
        )
        bundle = _tiny_bundle()  # 매 스텝 같은 픽셀 = 탭이 화면을 못 바꿈

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.TAP_MISS_RETRIES", 1),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle", return_value=bundle),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    return_value=decision,
                ),
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen") as record_mock,
                patch(
                    "navigation.navigator.write_survey_report",
                    return_value=Path("output/game_survey_report.md"),
                ) as report_mock,
                patch("navigation.navigator.execute_button_tap", return_value=True) as tap_mock,
                _no_refine_no_wait(changed=False),
            ):
                mss_mock.return_value.__enter__.return_value = object()
                # 1스텝: Settings 탭 / 2스텝: 화면 그대로 -> missed, 재시도 탭 /
                # 3스텝: 또 그대로 -> no_change로 닫힘, 프론티어 소진으로 종료.
                summary = run_autodrive(object(), max_steps=5, allow_taps=True)

                from navigation.ledger import LedgerStore

                reloaded = LedgerStore()
                self.assertEqual(reloaded.frontier_at(screen_id(decision)), [])
                key = (
                    f"{screen_id(decision)}:"
                    f"{navigation_action_key_from_label('Settings')}"
                )
                self.assertEqual(reloaded.ledger.entries[key].verdict, "no_change")
                self.assertEqual(reloaded.ledger.entries[key].attempts, 2)

            self.assertEqual(tap_mock.call_count, 2)
            self.assertTrue(summary.converged)
            self.assertEqual(summary.steps, 3)
            reported_graph = report_mock.call_args.kwargs["graph"]
            self.assertEqual(len(reported_graph.nodes), 1)
            self.assertEqual(reported_graph.edges, [])
            outcomes = [
                call.kwargs["action"]["outcome"] for call in record_mock.call_args_list
            ]
            self.assertEqual(outcomes, ["tap_executed", "tap_executed", "frontier_converged"])

    def test_same_pixels_reuse_node_even_if_llm_renames_screen_type(self) -> None:
        """직전과 픽셀이 같은 화면을 LLM이 다른 화면 타입으로 불러도 새 노드를 만들지
        않습니다(실측: 같은 화면이 menu/다른 타입으로 갈려 노드가 쪼개짐)."""

        first = SurveyDecision(
            screen_type="menu",
            button_candidates=[_button("Help", role="structure")],
        )
        renamed = SurveyDecision(
            screen_type="popup",
            button_candidates=[_button("Help", role="structure")],
        )
        bundle = _tiny_bundle()

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.TAP_MISS_RETRIES", 0),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle", return_value=bundle),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    side_effect=[first, renamed],
                ),
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen"),
                patch(
                    "navigation.navigator.write_survey_report",
                    return_value=Path("output/game_survey_report.md"),
                ) as report_mock,
                patch("navigation.navigator.execute_button_tap", return_value=True),
                _no_refine_no_wait(changed=False),
            ):
                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(object(), max_steps=2, allow_taps=True)

            reported_graph = report_mock.call_args.kwargs["graph"]
            self.assertEqual(len(reported_graph.nodes), 1)
            node = next(iter(reported_graph.nodes.values()))
            self.assertEqual(node.screen_type, "menu")
            self.assertEqual(node.visits, 2)

    def test_frontier_button_missing_from_candidates_does_not_halt_exploration(
        self,
    ) -> None:
        """2번 수정의 핵심 시나리오: 원장에 남은 버튼(Event)이 다시 방문했을 때 후보에
        안 나와도 멈추지 않고 재관찰하고, 연속으로 안 보이면 정리한 뒤 탐색을
        정상 종료합니다. 그 사이 아무 버튼도 누르지 않아야 합니다."""

        decision = SurveyDecision(screen_type="home")  # 후보 없음
        bundle = _tiny_bundle()

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.FRONTIER_MAX_MISSES", 2),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.time.sleep"),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch("navigation.navigator.capture_bundle", return_value=bundle),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    return_value=decision,
                ) as analyze_mock,
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen") as record_mock,
                patch("navigation.navigator.write_survey_report"),
                patch("navigation.navigator.execute_button_tap") as tap_mock,
            ):
                from navigation.ledger import LedgerStore

                # 이전 실행에서 이 화면에 Event 버튼을 봤다고 가정합니다.
                event_key = navigation_action_key_from_label("Event")
                LedgerStore().seen(screen_id(decision), event_key, "Event")

                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(object(), max_steps=10, allow_taps=True)

                entry = LedgerStore().ledger.entries[f"{screen_id(decision)}:{event_key}"]
                self.assertEqual(entry.verdict, "not_visible")

            tap_mock.assert_not_called()
            self.assertEqual(analyze_mock.call_count, 2)  # 재관찰 1번 후 정리·종료
            outcomes = [
                call.kwargs["action"]["outcome"] for call in record_mock.call_args_list
            ]
            self.assertEqual(outcomes, ["reobserve_missing_frontier", "frontier_converged"])

    def test_two_distinct_screens_are_linked_and_second_screens_frontier_is_seeded(
        self,
    ) -> None:
        """실제로 다른 화면(A -> B)으로 넘어가는 다단계 흐름 전체를 검증합니다:
        간선 연결(store.connect), 직전 시도의 new_screen 판정, 다음 화면의 프론티어
        등록이 전부 한 루프 안에서 맞물려야 합니다."""

        decision_a = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("Shop")],
        )
        decision_b = SurveyDecision(screen_type="shop")  # 버튼 후보 없음
        bundle_a = _tiny_bundle(0)
        bundle_b = _tiny_bundle(200)  # 픽셀이 달라야 실제로 화면이 바뀐 것입니다.

        with tempfile.TemporaryDirectory() as directory:
            with (
                _patched_storage_paths(directory),
                patch("navigation.navigator.CAPTURE_MODE", "window"),
                patch("navigation.navigator.DRY_RUN", False),
                patch("navigation.navigator.mss.MSS") as mss_mock,
                patch(
                    "navigation.navigator.capture_bundle",
                    side_effect=[bundle_a, bundle_b],
                ),
                patch(
                    "navigation.navigator.analyze_survey_screen",
                    side_effect=[decision_a, decision_b],
                ),
                patch("navigation.navigator.load_recent_survey_records", return_value=[]),
                patch("navigation.navigator.load_all_survey_records", return_value=[]),
                patch("navigation.navigator.record_survey_screen"),
                patch(
                    "navigation.navigator.write_survey_report",
                    return_value=Path("output/game_survey_report.md"),
                ) as report_mock,
                patch("navigation.navigator.execute_button_tap", return_value=True),
                _no_refine_no_wait(),
            ):
                mss_mock.return_value.__enter__.return_value = object()
                # A의 Shop을 누르면 B로 넘어가고, B는 프론티어가 없어 2스텝만에 끝나야 합니다.
                run_autodrive(object(), max_steps=5, allow_taps=True)

            reported_graph = report_mock.call_args.kwargs["graph"]
            self.assertEqual(len(reported_graph.nodes), 2)
            self.assertEqual(len(reported_graph.edges), 1)
            edge = reported_graph.edges[0]
            self.assertEqual(edge.action, "Shop")

            reported_ledger = report_mock.call_args.kwargs["ledger"]
            a_id, b_id = edge.source, edge.target
            shop_key = navigation_action_key_from_label("Shop")
            self.assertEqual(
                reported_ledger.ledger.entries[f"{a_id}:{shop_key}"].verdict,
                "new_screen",
            )
            self.assertEqual(
                reported_ledger.ledger.entries[f"{a_id}:{shop_key}"].target_screen_id,
                b_id,
            )



def _common_patches(directory: str, *, capture, analyze):
    """통합 테스트 공통 경계(저장 경로·캡처·LLM·보고서)를 막습니다."""

    capture_kwargs = {"side_effect": capture} if isinstance(capture, list) else {"return_value": capture}
    analyze_kwargs = {"side_effect": analyze} if isinstance(analyze, list) else {"return_value": analyze}
    return [
        _patched_storage_paths(directory),
        patch("navigation.navigator.CAPTURE_MODE", "window"),
        patch("navigation.navigator.DRY_RUN", False),
        patch("navigation.navigator.capture_bundle", **capture_kwargs),
        patch("navigation.navigator.analyze_survey_screen", **analyze_kwargs),
        patch("navigation.navigator.load_recent_survey_records", return_value=[]),
        patch("navigation.navigator.load_all_survey_records", return_value=[]),
        patch("navigation.navigator.time.sleep"),
    ]


class TapRefineIntegrationTests(unittest.TestCase):
    """누르기 직전 확대 재확인이 탐색 루프에 맞물리는지 봅니다."""

    def _run(self, refine_result, *, max_steps: int = 3):
        from contextlib import ExitStack

        decision = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("Shop")],
        )
        effect = TapEffect(changed=True, final_change=0.5, peak_change=0.5,
                           settled=True, waited=0.0, frames=1)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with ExitStack() as stack:
            for context in _common_patches(directory.name, capture=_tiny_bundle(), analyze=decision):
                stack.enter_context(context)
            mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
            stack.enter_context(patch("navigation.navigator.TAP_REFINE", True))
            stack.enter_context(patch("navigation.navigator.BACK_KEY", ""))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=effect))
            refine_mock = stack.enter_context(
                patch("navigation.navigator.refine_tap_target", return_value=refine_result)
            )
            tap_mock = stack.enter_context(
                patch("navigation.navigator.execute_button_tap", return_value=True)
            )
            record_mock = stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            stack.enter_context(patch("navigation.navigator.write_survey_report"))
            mss_mock.return_value.__enter__.return_value = object()
            run_autodrive(object(), max_steps=max_steps, allow_taps=True)

            from navigation.ledger import LedgerStore

            ledger = LedgerStore()
        return decision, refine_mock, tap_mock, record_mock, ledger

    def test_refined_point_is_what_gets_tapped(self) -> None:
        from tap_refine import TapRefineResult

        result = TapRefineResult(found=True, initial=(2, 2), x=1, y=3, used_pass="zoom", api_calls=1)
        _, refine_mock, tap_mock, record_mock, _ = self._run(result, max_steps=1)

        refine_mock.assert_called_once()
        tapped = tap_mock.call_args.args[0]
        self.assertAlmostEqual(tapped.center.x, 0.25)
        self.assertAlmostEqual(tapped.center.y, 0.75)
        action = record_mock.call_args.kwargs["action"]
        self.assertEqual(action["refine"]["refined_px"], [1, 3])
        self.assertTrue(action["tap_effect"]["changed"])

    def test_button_not_found_is_not_tapped_and_closed(self) -> None:
        from tap_refine import TapRefineResult

        result = TapRefineResult(found=False, initial=(2, 2), api_calls=2)
        decision, _, tap_mock, record_mock, ledger = self._run(result)

        tap_mock.assert_not_called()
        outcomes = [call.kwargs["action"]["outcome"] for call in record_mock.call_args_list]
        self.assertEqual(outcomes[0], "tap_target_not_found")
        self.assertEqual(outcomes[-1], "frontier_converged")
        key = f"{screen_id(decision)}:{navigation_action_key_from_label('Shop')}"
        self.assertEqual(ledger.ledger.entries[key].verdict, "not_found")
        self.assertEqual(ledger.ledger.entries[key].attempts, 0)

    def test_refine_error_falls_back_to_original_point(self) -> None:
        from tap_refine import TapRefineResult

        result = TapRefineResult(found=False, initial=(2, 2), error="RuntimeError: down")
        _, _, tap_mock, _, _ = self._run(result, max_steps=1)

        tapped = tap_mock.call_args.args[0]
        self.assertEqual((tapped.center.x, tapped.center.y), (0.5, 0.5))


class BackKeyIntegrationTests(unittest.TestCase):
    """3단계: 막혔을 때 뒤로 키 탈출과 시작 화면 정렬."""

    def test_stuck_screen_escapes_with_back_key_and_links_back(self) -> None:
        from contextlib import ExitStack

        decision_a = SurveyDecision(
            screen_type="home",
            button_candidates=[_button("Shop")],
        )
        decision_b = SurveyDecision(screen_type="shop")  # 버튼 없음 = 막힘
        bundle_a, bundle_b = _tiny_bundle(0), _tiny_bundle(200)
        effect = TapEffect(changed=True, final_change=0.5, peak_change=0.5,
                           settled=True, waited=0.0, frames=1)

        with tempfile.TemporaryDirectory() as directory:
            with ExitStack() as stack:
                for context in _common_patches(
                    directory,
                    capture=[bundle_a, bundle_b, bundle_a],
                    analyze=[decision_a, decision_b, decision_a],
                ):
                    stack.enter_context(context)
                mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
                stack.enter_context(patch("navigation.navigator.TAP_REFINE", False))
                stack.enter_context(patch("navigation.navigator.BACK_KEY", "esc"))
                stack.enter_context(patch("navigation.navigator.STUCK_BACK_KEY_MAX", 3))
                stack.enter_context(
                    patch("navigation.navigator.wait_for_settle", return_value=effect)
                )
                stack.enter_context(
                    patch("navigation.navigator.execute_button_tap", return_value=True)
                )
                back_mock = stack.enter_context(
                    patch("navigation.navigator.execute_back_key", return_value=True)
                )
                record_mock = stack.enter_context(
                    patch("navigation.navigator.record_survey_screen")
                )
                report_mock = stack.enter_context(
                    patch(
                        "navigation.navigator.write_survey_report",
                        return_value=Path("output/game_survey_report.md"),
                    )
                )
                from navigation.ledger import LedgerStore

                # 그래프로는 갈 길을 모르는 화면에 프론티어가 남아 있다고 둡니다.
                LedgerStore().seen("screen_far_away", "key_event", "Event")
                mss_mock.return_value.__enter__.return_value = object()
                summary = run_autodrive(object(), max_steps=5, allow_taps=True)

        back_mock.assert_called_once()
        outcomes = [call.kwargs["action"]["outcome"] for call in record_mock.call_args_list]
        self.assertEqual(outcomes, ["tap_executed", "stuck_back_key", "frontier_unreachable"])
        graph = report_mock.call_args.kwargs["graph"]
        a_id, b_id = screen_id(decision_a), screen_id(decision_b)
        labels = {(edge.source, edge.target): edge.action for edge in graph.edges}
        self.assertEqual(labels[(a_id, b_id)], "Shop")
        self.assertEqual(labels[(b_id, a_id)], "뒤로(키)")
        # 루트(A)에서는 뒤로 키를 누르지 않고 멈춥니다(게임 종료 팝업 방지).
        self.assertEqual(summary.stop_kind, "unreachable")

    def test_session_that_starts_off_root_goes_back_to_root_first(self) -> None:
        from contextlib import ExitStack

        from image_utils import board_fingerprint

        decision_root = SurveyDecision(screen_type="home")
        decision_shop = SurveyDecision(
            screen_type="shop",
            button_candidates=[_button("Special Offer")],
        )
        bundle_root, bundle_shop = _tiny_bundle(0), _tiny_bundle(200)
        effect = TapEffect(changed=True, final_change=0.5, peak_change=0.5,
                           settled=True, waited=0.0, frames=1)

        with tempfile.TemporaryDirectory() as directory:
            # 이전 세션이 맵(루트)을 첫 화면으로 기록해 둔 그래프입니다.
            root_dir = Path(directory) / "navigation"
            root_dir.mkdir(parents=True)
            root_id = screen_id(decision_root)
            (root_dir / "graph.json").write_text(
                NavigationGraph(nodes={
                    root_id: ScreenNode(
                        id=root_id,
                        screen_type="home",
                        visits=1,
                        fingerprint=board_fingerprint(bundle_root.full_image),
                    ),
                }).model_dump_json(),
                encoding="utf-8",
            )
            with ExitStack() as stack:
                for context in _common_patches(
                    directory,
                    capture=[bundle_shop, bundle_root],
                    analyze=[decision_shop, decision_root],
                ):
                    stack.enter_context(context)
                mss_mock = stack.enter_context(patch("navigation.navigator.mss.MSS"))
                stack.enter_context(patch("navigation.navigator.TAP_REFINE", False))
                stack.enter_context(patch("navigation.navigator.BACK_KEY", "esc"))
                stack.enter_context(patch("navigation.navigator.ROOT_SCREEN_ID", None))
                stack.enter_context(
                    patch("navigation.navigator.wait_for_settle", return_value=effect)
                )
                tap_mock = stack.enter_context(
                    patch("navigation.navigator.execute_button_tap", return_value=True)
                )
                back_mock = stack.enter_context(
                    patch("navigation.navigator.execute_back_key", return_value=True)
                )
                stack.enter_context(patch("navigation.navigator.record_survey_screen"))
                report_mock = stack.enter_context(
                    patch(
                        "navigation.navigator.write_survey_report",
                        return_value=Path("output/game_survey_report.md"),
                    )
                )
                mss_mock.return_value.__enter__.return_value = object()
                run_autodrive(object(), max_steps=2, allow_taps=True)

        # 상점의 'Special Offer'를 누르지 않고 먼저 뒤로 키로 루트에 돌아갑니다.
        tap_mock.assert_not_called()
        back_mock.assert_called_once()
        graph = report_mock.call_args.kwargs["graph"]
        edge = graph.edges[0]
        self.assertEqual((edge.source, edge.target), (screen_id(decision_shop), root_id))
        self.assertEqual(edge.action, "뒤로(키)")


if __name__ == "__main__":
    unittest.main()
