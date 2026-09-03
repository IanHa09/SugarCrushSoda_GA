"""navigation 패키지 단위/통합 테스트."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


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
    def test_each_screen_is_saved_as_a_survey_record(self) -> None:
        """탐색한 화면마다 survey 기록을 남기고 종료 시 보고서를 씁니다."""
        decision = SurveyDecision(screen_type="map_or_level_select")
        node = ScreenNode(id="map", screen_type=decision.screen_type)
        graph = NavigationGraph(nodes={node.id: node})
        store = MagicMock()
        store.graph = graph
        store.explored_actions.return_value = set()
        store.observe.return_value = node
        bundle = SimpleNamespace(full_image=object(), full_region={})

        with (
            patch("navigation.navigator.CAPTURE_MODE", "window"),
            patch("navigation.navigator.DRY_RUN", False),
            patch("navigation.navigator.GraphStore", return_value=store),
            patch("navigation.navigator.mss.MSS") as mss_mock,
            patch(
                "navigation.navigator.capture_bundle",
                return_value=bundle,
            ),
            patch(
                "navigation.navigator.analyze_survey_screen",
                return_value=decision,
            ),
            patch(
                "navigation.navigator.load_recent_survey_records",
                return_value=[],
            ),
            patch(
                "navigation.navigator.load_all_survey_records",
                return_value=[],
            ),
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
            "no_safe_action",
        )
        # 보고서는 프레임마다가 아니라 종료 시 한 번, 실제 그래프와 함께 만듭니다.
        report_mock.assert_called_once()
        self.assertIs(report_mock.call_args.kwargs["graph"], graph)

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

        store = MagicMock()
        store.explored_actions.return_value = set()
        store.graph = NavigationGraph()

        with (
            patch("navigation.navigator.CAPTURE_MODE", "window"),
            patch("navigation.navigator.DRY_RUN", False),
            patch("navigation.navigator.GraphStore", return_value=store),
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
        node = ScreenNode(id="map", screen_type=decision.screen_type)
        graph = NavigationGraph(nodes={node.id: node})
        store = MagicMock()
        store.graph = graph
        store.explored_actions.return_value = set()
        store.observe.return_value = node
        bundle = SimpleNamespace(full_image=object(), full_region={})
        sentinel_cancel_check = lambda: None

        with (
            patch("navigation.navigator.CAPTURE_MODE", "window"),
            patch("navigation.navigator.DRY_RUN", False),
            patch("navigation.navigator.GraphStore", return_value=store),
            patch("navigation.navigator.mss.MSS") as mss_mock,
            patch(
                "navigation.navigator.capture_bundle",
                return_value=bundle,
            ),
            patch(
                "navigation.navigator.analyze_survey_screen",
                return_value=decision,
            ),
            patch(
                "navigation.navigator.load_recent_survey_records",
                return_value=[],
            ),
            patch(
                "navigation.navigator.load_all_survey_records",
                return_value=[],
            ),
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


if __name__ == "__main__":
    unittest.main()
