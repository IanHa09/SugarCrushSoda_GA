from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from survey_report import generate_survey_markdown
from navigation.schemas import NavigationEdge, NavigationGraph, ScreenNode


class SurveyReportTests(unittest.TestCase):
    def test_report_diagram_lists_observed_screens_without_guessing_edges(self) -> None:
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
        report = generate_survey_markdown([])

        self.assertIn("## Game Structure Diagram", report)
        self.assertIn("No survey screens recorded yet.", report)
        self.assertNotIn("```mermaid", report)

    def test_autodrive_graph_replaces_inferred_edges(self) -> None:
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
