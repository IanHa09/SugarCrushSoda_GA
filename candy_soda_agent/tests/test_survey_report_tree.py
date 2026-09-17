"""3단계(트리 투영·동시출현·프론티어 섹션) 보고서 동작을 검증합니다.

기존 tests/test_survey_report.py는 옛 동작(다이어그램 기본 골격)을 그대로
지키는지 봅니다. 이 파일은 이번에 새로 생긴 부분만 다룹니다.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.ledger import LedgerStore
from schemas import NavigationEdge, NavigationGraph, ScreenNode
from survey_report import generate_survey_markdown


def _graph_a_b_c_with_cycle() -> NavigationGraph:
    """A -> B -> C, 그리고 C -> A(사이클, 교차 링크가 돼야 함)."""
    return NavigationGraph(
        nodes={
            "a": ScreenNode(id="a", screen_type="map_or_level_select"),
            "b": ScreenNode(id="b", screen_type="shop_or_currency"),
            "c": ScreenNode(id="c", screen_type="settings_menu"),
        },
        edges=[
            NavigationEdge(source="a", target="b", action="Shop", action_key="shop"),
            NavigationEdge(source="b", target="c", action="Settings", action_key="settings"),
            NavigationEdge(source="c", target="a", action="Home", action_key="home"),
        ],
    )


class ElementCoOccurrenceTests(unittest.TestCase):
    def test_element_seen_on_multiple_screens_lists_every_screen(self) -> None:
        """옛 버전은 setdefault 때문에 첫 등장만 남기고 나머지 화면을 버렸습니다.
        이제는 같은 요소가 나온 모든 화면을 모아야 합니다."""

        records = [
            {
                "step": 1,
                "screen_id": "screen_a",
                "survey": {"screen_type": "level_complete"},
                "elements": [
                    {"key": "star", "category": "reward", "name": "Star", "evidence": "3 stars"},
                ],
            },
            {
                "step": 2,
                "screen_id": "screen_b",
                "survey": {"screen_type": "reward_popup"},
                "elements": [
                    {"key": "star", "category": "reward", "name": "Star", "evidence": "bonus stars"},
                ],
            },
        ]

        report = generate_survey_markdown(records)

        # 두 화면 모두 언급돼야 하고(동시출현), 두 번째 evidence가 첫 번째를 지우면 안 됩니다.
        star_section = report.split("### reward", 1)[1]
        self.assertIn("등장 화면(2)", star_section)
        self.assertIn("level_complete", star_section.lower().replace(" ", "_") + star_section)


class ScreenHierarchyTests(unittest.TestCase):
    def test_tree_lists_hierarchy_and_moves_cycle_edge_to_cross_links(self) -> None:
        """A/B/C가 트리에 들어가고, C->A(사이클)는 Cross Links로 빠져야 합니다."""
        graph = _graph_a_b_c_with_cycle()

        report = generate_survey_markdown([], graph)

        self.assertIn("## Screen Hierarchy", report)
        hierarchy = report.split("## Screen Hierarchy", 1)[1]
        # 루트(a, 딕셔너리에 가장 먼저 들어간 노드) 아래 b, 그 아래 c가 들여쓰기로 나와야 합니다.
        self.assertIn("Map / Level Select", hierarchy)
        self.assertIn("Shop / Currency", hierarchy)
        self.assertIn("Settings", hierarchy)
        self.assertIn("### Cross Links", hierarchy)
        cross_section = hierarchy.split("### Cross Links", 1)[1]
        self.assertIn("Home", cross_section)  # c -> a 간선이 교차 링크로 남음

        # 다이어그램에서도 같은 간선이 점선(-.->)으로 그려져야 합니다.
        self.assertIn("-.->", report)

    def test_configured_root_overrides_first_seen_node(self) -> None:
        """ROOT_SCREEN_ID가 그래프에 있으면 최초 관찰 노드 대신 그걸 루트로 씁니다."""
        graph = _graph_a_b_c_with_cycle()

        with tempfile.TemporaryDirectory():
            from unittest.mock import patch

            with patch("survey_report.ROOT_SCREEN_ID", "c"):
                report = generate_survey_markdown([], graph)

        hierarchy = report.split("## Screen Hierarchy", 1)[1]
        # 루트가 c이므로 c가 최상위(들여쓰기 없는 첫 항목)여야 합니다.
        first_bullet_line = next(line for line in hierarchy.splitlines() if line.startswith("- "))
        self.assertIn("Settings", first_bullet_line)


class MermaidCutoffTests(unittest.TestCase):
    def test_diagram_is_skipped_when_node_count_exceeds_cap(self) -> None:
        """노드가 너무 많으면(설정된 상한 초과) mermaid 블록을 생략합니다."""
        nodes = {
            f"n{i}": ScreenNode(id=f"n{i}", screen_type="unknown_but_recordable")
            for i in range(5)
        }
        graph = NavigationGraph(
            nodes=nodes,
            edges=[NavigationEdge(source="n0", target="n1", action="Go", action_key="go")],
        )

        from unittest.mock import patch

        with patch("survey_report.MERMAID_MAX_NODES", 2):
            report = generate_survey_markdown([], graph)

        self.assertNotIn("```mermaid", report)
        self.assertIn("mermaid 다이어그램은 생략합니다", report)


class FrontierSectionTests(unittest.TestCase):
    def test_frontier_and_blocked_entries_are_listed_when_ledger_given(self) -> None:
        graph = _graph_a_b_c_with_cycle()

        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.seen("b", "action_event", "Event")  # 미시도(프론티어)
            ledger.seen("c", "action_ad", "Watch Ad")
            ledger.mark_attempted("c", "action_ad", "Watch Ad")
            ledger.block("c", "action_ad")  # 포기

            report = generate_survey_markdown([], graph, ledger=ledger)

        frontier_section = report.split("## Exploration Frontier", 1)[1]
        self.assertIn("탐색 커버리지: 1/2", frontier_section)
        self.assertIn("Event", frontier_section)
        self.assertIn("Watch Ad", frontier_section)

    def test_not_visible_entries_are_listed_and_counted(self) -> None:
        graph = _graph_a_b_c_with_cycle()

        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.seen("a", "action_gift", "Gift")
            ledger.note_visit("a", [], max_misses=1)  # 후보에서 사라져 정리

            report = generate_survey_markdown([], graph, ledger=ledger)

        frontier_section = report.split("## Exploration Frontier", 1)[1]
        self.assertIn("누르지 않고 정리된 것 1개", frontier_section)
        hidden = frontier_section.split("(not_visible)", 1)[1]
        self.assertIn("Gift", hidden)

    def test_overview_shows_node_count_per_screen_type(self) -> None:
        """쪼개짐 진단용: 화면 타입별 노드 수가 Overview에 나옵니다."""
        graph = _graph_a_b_c_with_cycle()
        graph.nodes["d"] = ScreenNode(id="d", screen_type="settings_menu")

        report = generate_survey_markdown([], graph)

        overview = report.split("## Overview", 1)[1].split("##", 1)[0]
        self.assertIn("Navigation nodes: 4", overview)
        self.assertIn("settings_menu 2", overview)

    def test_missing_ledger_notes_no_data_instead_of_failing(self) -> None:
        report = generate_survey_markdown([], None, ledger=None)
        self.assertIn("원장 정보 없음", report)


if __name__ == "__main__":
    unittest.main()
