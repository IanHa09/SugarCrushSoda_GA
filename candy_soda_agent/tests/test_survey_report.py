"""survey_report의 조사 보고서 생성 함수를 검증하는 테스트입니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from survey_report import generate_survey_markdown
from schemas import NavigationEdge, NavigationGraph, ScreenNode


class SurveyReportTests(unittest.TestCase):
    def test_report_diagram_lists_observed_screens_without_guessing_edges(self) -> None:
        """관찰된 화면만으로 다이어그램을 구성하고 전환선은 추정하지 않는지 검증합니다."""
        records = [
            {
                "step": 1,
                "survey": {
                    "screen_type": "map_or_level_select",
                    "summary": "Choose a level.",
                },
                "dedupe": {"screen_signature": "map"},
            },
            {
                "step": 2,
                "survey": {
                    "screen_type": "playing_board",
                    "summary": "Play the puzzle.",
                },
                "dedupe": {"screen_signature": "board"},
            },
            {
                "step": 3,
                "survey": {
                    "screen_type": "level_complete",
                    "summary": "Finish the level.",
                },
                "dedupe": {"screen_signature": "complete"},
            },
            {
                "step": 4,
                "survey": {
                    "screen_type": "settings_menu",
                    "summary": "Change settings.",
                },
                "dedupe": {"screen_signature": "settings"},
            },
        ]

        report = generate_survey_markdown(records)

        self.assertIn("## Game Structure Diagram", report)
        self.assertIn("```mermaid\nflowchart LR", report)
        self.assertIn('screen_map_or_level_select["Map / Level Select"]', report)
        self.assertIn('screen_playing_board["Playing Board"]', report)
        self.assertIn("현재 화면 간 전환 기록 없음", report)
        self.assertNotIn("-->", report)

    def test_empty_report_explains_that_no_diagram_is_available(self) -> None:
        """기록이 없을 때 다이어그램 부재 안내 문구가 나오는지 검증합니다."""
        report = generate_survey_markdown([])

        self.assertIn("## Game Structure Diagram", report)
        self.assertIn("No survey screens recorded yet.", report)
        self.assertNotIn("```mermaid", report)

    def test_evidence_index_is_capped_but_lists_stay_complete(self) -> None:
        """요소 목록은 전체 누적되고 Evidence Index만 최근 건수로 제한되는지 검증합니다."""
        records = [
            {
                "step": index,
                "survey": {"screen_type": f"screen_{index}"},
                "dedupe": {"screen_signature": str(index)},
                "elements": [
                    {
                        "key": f"element_{index}",
                        "category": "ui",
                        "name": f"element {index}",
                    }
                ],
            }
            for index in range(1, 6)
        ]

        report = generate_survey_markdown(records, evidence_limit=2)

        # 요소 목록은 오래된 기록까지 전부 누적됩니다.
        self.assertIn("element 1", report)
        self.assertIn("element 5", report)

        # 단계별 증거만 최근 것으로 줄어듭니다.
        evidence = report.split("## Evidence Index", 1)[1]
        self.assertNotIn("Step 1:", evidence)
        self.assertIn("Step 5:", evidence)
        self.assertIn("전체 5건 중 최근 2건만", report)

    def test_autodrive_graph_replaces_inferred_edges(self) -> None:
        """Autodrive 그래프가 있으면 추정 대신 실제 전환선을 사용하는지 검증합니다."""
        records = [
            {
                "step": 1,
                "survey": {"screen_type": "map_or_level_select"},
                "dedupe": {"screen_signature": "map"},
            },
            {
                "step": 2,
                "survey": {"screen_type": "settings_menu"},
                "dedupe": {"screen_signature": "settings"},
            },
        ]
        graph = NavigationGraph(
            nodes={
                "map": ScreenNode(
                    id="map",
                    screen_type="map_or_level_select",
                ),
                "settings": ScreenNode(
                    id="settings",
                    screen_type="settings_menu",
                ),
            },
            edges=[
                NavigationEdge(
                    source="map",
                    target="settings",
                    action="Settings",
                )
            ],
        )

        report = generate_survey_markdown(records, graph)

        self.assertIn("Autodrive가 관찰한 실제 화면 전환", report)
        self.assertIn("screen_map -->|Settings| screen_settings", report)
        self.assertNotIn("Start level", report)


if __name__ == "__main__":
    unittest.main()
