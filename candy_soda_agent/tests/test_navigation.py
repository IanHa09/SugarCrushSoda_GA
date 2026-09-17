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
from navigation.observer import canonical_screen_id, screen_id
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
    ):
        yield


def _tiny_bundle() -> SimpleNamespace:
    """observe()가 실제로 cv2.imwrite를 호출하므로 진짜(작은) 이미지가 필요합니다."""
    return SimpleNamespace(
        full_image=np.zeros((4, 4, 3), dtype=np.uint8),
        full_region={"left": 0, "top": 0, "width": 4, "height": 4},
    )


class ScreenIdentityTests(unittest.TestCase):
    def test_parallel_section_id_ignores_llm_wording_changes(self) -> None:
        """문구가 달라도 병렬 섹션 화면은 같은 ID로 묶입니다."""
        first = SurveyDecision(
            screen_type="map_or_level_select",
            summary="Island map with Level 10",
            visible_text=["Level 10", "Full"],
            button_candidates=[_button("Settings (gear)")],
        )
        second = SurveyDecision(
            screen_type="map_or_level_select",
            summary="World map and highlighted level",
            visible_text=["Level 11"],
            button_candidates=[_button("Settings (cog)")],
        )

        self.assertEqual(screen_id(first), screen_id(second))
        self.assertEqual(
            screen_id(first),
            canonical_screen_id("map_or_level_select"),
        )


class ParallelPolicyTests(unittest.TestCase):
    def test_selects_another_safe_button_after_equivalent_action_used(self) -> None:
        """이미 쓴 행동과 동등한 버튼은 건너뛰고 다른 안전 버튼을 고릅니다."""
        settings = _button("Settings (gear)")
        shop = _button("Shop (bottom-left icon)")
        decision = SurveyDecision(
            screen_type="map_or_level_select",
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
    def test_existing_parallel_screen_duplicates_are_merged(self) -> None:
        """저장된 그래프를 불러올 때 병렬 화면 중복 노드/간선을 병합합니다."""
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
                        screen_type="map_or_level_select",
                        summary="Map screen",
                        visits=1,
                    ),
                    "old_b": ScreenNode(
                        id="old_b",
                        screen_type="map_or_level_select",
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

            canonical = canonical_screen_id("map_or_level_select")
            self.assertEqual(list(store.graph.nodes), [canonical])
            self.assertEqual(store.graph.nodes[canonical].visits, 3)
            self.assertEqual(len(store.graph.edges), 1)
            self.assertEqual(store.graph.edges[0].source, canonical)
            self.assertEqual(store.graph.edges[0].target, canonical)
            self.assertIn(
                f"{canonical}:{navigation_action_key_from_label('Settings')}",
                store.explored_actions(),
            )


class AutodriveIntegrationTests(unittest.TestCase):
    """2단계 이후: GraphStore/LedgerStore를 실제로(임시 폴더에) 돌려 프론티어
    기반 루프 전체를 검증합니다. 화면·버튼 판단만 외부 경계(캡처/LLM/탭 실행)를
    모킹합니다."""

    def test_each_screen_is_saved_as_a_survey_record(self) -> None:
        """안전 버튼 후보가 없으면 전역 프론티어도 비어 있어 1스텝만에
        "탐색 완료"로 멈추고, 그 화면은 survey 기록에 남습니다."""
        decision = SurveyDecision(screen_type="map_or_level_select")  # 버튼 후보 없음
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
            screen_type="map_or_level_select",
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

    def test_unresponsive_button_is_not_retapped_next_run(self) -> None:
        """2단계 핵심 시나리오: 눌러도 화면이 그대로인(no_change) 버튼은 원장에
        시도로 남아, 같은 화면을 다시 프론티어로 스캔해도 다시 뽑히지 않습니다."""

        decision = SurveyDecision(
            screen_type="map_or_level_select",
            button_candidates=[_button("Settings", role="structure")],
        )
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
                patch("navigation.navigator.record_survey_screen"),
                patch("navigation.navigator.write_survey_report"),
                patch("navigation.navigator.execute_button_tap", return_value=True),
            ):
                mss_mock.return_value.__enter__.return_value = object()
                # 2스텝: 1스텝에서 Settings를 누르고, 2스텝에서 같은 화면을 다시
                # 관찰합니다(버튼을 눌러도 화면이 안 바뀌므로 같은 노드로 돌아옴).
                run_autodrive(object(), max_steps=2, allow_taps=True)

                # 경로 패치가 아직 살아있는 동안(같은 임시 폴더를 보도록) 원장을
                # 새로 읽어, 실행이 끝난 뒤에도 결과가 남아 있는지 확인합니다.
                from navigation.ledger import LedgerStore

                reloaded = LedgerStore()
                frontier_entries = reloaded.frontier_at(screen_id(decision))
                self.assertEqual(frontier_entries, [])  # Settings는 더 이상 프론티어가 아님
                key = (
                    f"{screen_id(decision)}:"
                    f"{navigation_action_key_from_label('Settings')}"
                )
                self.assertEqual(reloaded.ledger.entries[key].verdict, "no_change")

    def test_frontier_button_missing_from_candidates_does_not_halt_exploration(
        self,
    ) -> None:
        """2번 수정의 핵심 시나리오: 원장에 남은 버튼(Event)이 다시 방문했을 때 후보에
        안 나와도 멈추지 않고 재관찰하고, 연속으로 안 보이면 정리한 뒤 탐색을
        정상 종료합니다. 그 사이 아무 버튼도 누르지 않아야 합니다."""

        decision = SurveyDecision(screen_type="map_or_level_select")  # 후보 없음
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
            screen_type="map_or_level_select",
            button_candidates=[_button("Shop")],
        )
        decision_b = SurveyDecision(screen_type="shop_or_currency")  # 버튼 후보 없음
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


if __name__ == "__main__":
    unittest.main()
