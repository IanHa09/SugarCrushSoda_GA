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

        # 두 화면 모두 언급돼야 합니다(동시출현).
        star_section = report.split("### reward", 1)[1]
        self.assertIn("2개 화면", star_section)
        self.assertIn("level_complete", star_section)
        self.assertIn("reward_popup", star_section)

    def test_one_screen_element_omits_the_screen_suffix(self) -> None:
        """한 화면에서만 본 요소는 등장 화면을 적어도 정보가 늘지 않습니다."""

        records = [
            {
                "step": 1,
                "screen_id": "screen_a",
                "survey": {"screen_type": "level_complete"},
                "elements": [{"category": "reward", "name": "Star"}],
            }
        ]

        report = generate_survey_markdown(records)

        star_section = report.split("### reward", 1)[1]
        self.assertIn("- Star", star_section)
        self.assertNotIn("개 화면", star_section)

    def test_same_element_under_different_names_becomes_one_entry(self) -> None:
        """"부스터 / 부스터 아이콘 / 부스터 선택 버튼"은 한 요소입니다."""

        records = [
            {
                "step": index,
                "screen_id": f"screen_{index}",
                "survey": {"screen_type": "playing_board"},
                "elements": [{"category": "booster", "name": name}],
            }
            for index, name in enumerate(
                ["부스터", "부스터 아이콘", "부스터 선택 버튼", "부스터"], start=1
            )
        ]

        report = generate_survey_markdown(records)

        booster_section = report.split("### booster", 1)[1].split("\n## ", 1)[0]
        bullets = [line for line in booster_section.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 1)
        # 대표 이름은 가장 자주 나온 표기입니다.
        self.assertIn("부스터", bullets[0])
        self.assertNotIn("아이콘", bullets[0])

    def test_element_evidence_lines_are_dropped(self) -> None:
        """요소마다 붙던 Evidence 줄은 이름을 다시 말하는 수준이라 뺐습니다."""

        records = [
            {
                "step": 1,
                "screen_id": "screen_a",
                "survey": {"screen_type": "level_complete"},
                "elements": [
                    {"category": "reward", "name": "Star", "evidence": "세 개의 별"}
                ],
            }
        ]

        report = generate_survey_markdown(records)

        self.assertNotIn("세 개의 별", report)


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


class SelfLoopTests(unittest.TestCase):
    """같은 화면으로 돌아오는 간선은 "눌러도 안 바뀌었다"는 뜻 하나뿐입니다."""

    @staticmethod
    def _graph_with_self_loops() -> NavigationGraph:
        return NavigationGraph(
            nodes={
                "a": ScreenNode(id="a", screen_type="map_or_level_select"),
                "b": ScreenNode(id="b", screen_type="shop_or_currency"),
            },
            edges=[
                NavigationEdge(source="a", target="b", action="Shop", action_key="shop"),
                NavigationEdge(source="b", target="b", action="탭1", action_key="t1"),
                NavigationEdge(source="b", target="b", action="탭2", action_key="t2"),
            ],
        )

    def test_self_loops_are_summed_into_one_line(self) -> None:
        report = generate_survey_markdown([], self._graph_with_self_loops())

        cross_section = report.split("### Cross Links", 1)[1]
        no_change = [
            line for line in cross_section.splitlines()
            if "눌러도 화면이 그대로인 버튼" in line
        ]
        self.assertEqual(len(no_change), 1)
        self.assertIn("2개", no_change[0])
        self.assertIn("탭1", no_change[0])
        self.assertIn("탭2", no_change[0])

    def test_self_loops_are_not_drawn_in_the_diagram(self) -> None:
        report = generate_survey_markdown([], self._graph_with_self_loops())

        diagram = report.split("```mermaid", 1)[1].split("```", 1)[0]
        self.assertNotIn("탭1", diagram)
        self.assertIn("Shop", diagram)
        self.assertIn("간선 2개는 그리지 않았습니다", report)


class OrphanScreenTests(unittest.TestCase):
    def test_nodes_unreachable_from_root_are_listed(self) -> None:
        """트리에서 그냥 빠지면 관찰했는지조차 알 수 없었습니다."""
        graph = _graph_a_b_c_with_cycle()
        graph.nodes["lost"] = ScreenNode(id="lost", screen_type="booster_panel")

        report = generate_survey_markdown([], graph)

        hierarchy = report.split("## Screen Hierarchy", 1)[1]
        self.assertIn("트리에 없는 화면", hierarchy)
        orphan_section = hierarchy.split("트리에 없는 화면", 1)[1]
        self.assertIn("Booster Panel", orphan_section)

    def test_section_is_absent_when_every_node_is_reachable(self) -> None:
        report = generate_survey_markdown([], _graph_a_b_c_with_cycle())

        self.assertNotIn("트리에 없는 화면", report)


class EvidenceIndexTests(unittest.TestCase):
    def test_repeated_screen_type_collapses_into_one_line(self) -> None:
        """같은 화면을 연달아 보면 한 줄로 묶고 건수만 적습니다."""
        records = [
            {
                "step": index,
                "session_id": "s1",
                "survey": {"screen_type": "shop_or_currency", "summary": "상점"},
            }
            for index in range(1, 6)
        ] + [
            {
                "step": 6,
                "session_id": "s1",
                "survey": {"screen_type": "playing_board", "summary": "보드"},
            }
        ]

        report = generate_survey_markdown(records)

        evidence = report.split("## Evidence Index", 1)[1]
        bullets = [line for line in evidence.splitlines() if line.startswith("- Step")]
        self.assertEqual(len(bullets), 2)
        self.assertIn("Step 1–5 (5건)", bullets[0])
        self.assertIn("Step 6", bullets[1])

    def test_new_session_breaks_the_group(self) -> None:
        """세션이 바뀌면 step이 1부터 다시 시작하므로 묶으면 안 됩니다."""
        records = [
            {"step": 1, "session_id": "s1", "survey": {"screen_type": "tutorial"}},
            {"step": 1, "session_id": "s2", "survey": {"screen_type": "tutorial"}},
        ]

        report = generate_survey_markdown(records)

        evidence = report.split("## Evidence Index", 1)[1]
        bullets = [line for line in evidence.splitlines() if line.startswith("- Step")]
        self.assertEqual(len(bullets), 2)


class EvidenceLinkTests(unittest.TestCase):
    def test_capture_outside_the_report_folder_becomes_a_relative_link(self) -> None:
        """캡처는 보고서 폴더 밖(output/captures)에 있어 ..로 올라가야 합니다."""
        from survey_report import SURVEY_REPORT_PATH, _relative_link

        capture = SURVEY_REPORT_PATH.parent.parents[2] / "captures" / "shot.png"

        link = _relative_link(str(capture), SURVEY_REPORT_PATH.parent)

        self.assertTrue(link.startswith("../"), link)
        self.assertTrue(link.endswith("captures/shot.png"), link)
        self.assertNotIn("\\", link)


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
