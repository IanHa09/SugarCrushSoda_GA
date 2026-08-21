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
from navigation.schemas import NavigationEdge, NavigationGraph, ScreenNode
from navigation.navigator import run_autodrive
from schemas import SurveyButtonCandidate, SurveyDecision


def _button(label: str, role: str = "structure") -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label,
        role=role,
        center={"x": 0.5, "y": 0.5},
        confidence=0.9,
    )


class ScreenIdentityTests(unittest.TestCase):
    def test_parallel_section_id_ignores_llm_wording_changes(self) -> None:
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
        self.assertEqual(
            navigation_action_key_from_label("Settings (gear)"),
            navigation_action_key_from_label("Settings (cog icon)"),
        )


class GraphNormalizationTests(unittest.TestCase):
    def test_existing_parallel_screen_duplicates_are_merged(self) -> None:
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
            patch("navigation.navigator.record_survey_screen") as record_mock,
            patch(
                "navigation.navigator.write_survey_report",
                return_value=Path("output/game_survey_report.md"),
            ),
        ):
            mss_mock.return_value.__enter__.return_value = object()
            run_autodrive(object(), max_steps=1, allow_taps=False)

        record_mock.assert_called_once()
        self.assertIs(record_mock.call_args.kwargs["graph"], graph)
        self.assertEqual(
            record_mock.call_args.kwargs["action"]["outcome"],
            "no_safe_action",
        )


if __name__ == "__main__":
    unittest.main()
