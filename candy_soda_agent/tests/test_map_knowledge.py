"""장르 화면 지도(knowledge/map_knowledge.json) 읽기·대조, 화면 역할 기록, 보고서 섹션 테스트."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import (
    BUTTON_REGIONS,
    ELEMENT_CATEGORIES,
    FALLBACK_ROLE,
    KnowledgeError,
    load_game_profile,
    load_screen_map,
    map_role_ids,
    screen_type_ids,
    survey_prompt_block,
)
from navigation.graph import GraphStore
from schemas import (
    NavigationEdge,
    NavigationGraph,
    ScreenNode,
    SurveyButtonCandidate,
    SurveyDecision,
    SurveyElement,
)
from survey_report import _append_genre_map_section


def _node(role_id: str, **extra) -> dict:
    return {
        "id": role_id, "label_ko": role_id, "screen_types": ["popup"], "prevalence": "common",
        "leave_by": ["close"], "element_categories": ["ui"], "description": "", **extra,
    }


def _edge(source: str, target: str, **extra) -> dict:
    return {"from": source, "to": target, "trigger": "tap", "prevalence": "common", **extra}


SMALL_MAP = {
    "genres": {
        "_default": {
            "nodes": [
                _node("home", screen_types=["home"], tab=True, leave_by=[]),
                _node("shop", screen_types=["shop"], tab=True),
                _node("info"),
                _node("settings"),
                _node("outside_app", screen_types=["unknown"]),
            ],
            "edges": [
                _edge("home", "settings", action="settings"),
                _edge("*", "info"),
                _edge("shop", "outside_app", risk="purchase"),
            ],
        },
        "match3": {
            "nodes": [_node("settings", prevalence="some"), _node("level_start", screen_types=["level"])],
            "edges": [_edge("home", "level_start")],
        },
    },
    "games": {"Some Game": {"nodes": [], "edges": [_edge("level_start", "shop")]}},
}


class _MapFile:
    def __init__(self, test: unittest.TestCase) -> None:
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "map_knowledge.json"

    def load(self, data: dict, genre: str = "match3", title: str = ""):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return load_screen_map(genre, title, path=self.path)


class ShippedMapTests(unittest.TestCase):
    """저장소의 map_knowledge.json이 그대로 읽히고 다른 지식·스키마와 어휘가 맞는지."""

    def test_every_genre_loads_with_known_screen_types(self) -> None:
        default = load_screen_map("_default")
        match3 = load_game_profile("Candy Crush Soda Saga").screen_map
        self.assertIsNone(default.role("level_start"))
        self.assertIsNotNone(match3.role("level_start"))
        self.assertGreater(len(match3.roles), len(default.roles))
        for role in match3.roles:
            self.assertTrue(set(role.screen_types) <= set(screen_type_ids()), role.id)
        # 매치3 층은 기본 층 노드의 일부 필드만 덮어씁니다.
        self.assertEqual(default.role("team").prevalence, "some")
        self.assertEqual(match3.role("team").prevalence, "common")
        self.assertEqual(match3.role("team").label_ko, default.role("team").label_ko)

    def test_schema_role_enum_matches_the_map(self) -> None:
        enum = SurveyDecision.model_json_schema()["properties"]["screen_role"]["enum"]
        self.assertEqual(tuple(enum), map_role_ids())
        self.assertEqual(enum[-1], FALLBACK_ROLE)
        self.assertEqual(SurveyDecision().screen_role, FALLBACK_ROLE)
        # 예전 분석 파일(screen_role 없음)도 읽힙니다.
        self.assertEqual(SurveyDecision.model_validate({"screen_type": "home"}).screen_role, FALLBACK_ROLE)

    def test_vocabularies_match_the_schemas(self) -> None:
        regions = set(get_args(SurveyButtonCandidate.model_fields["region"].annotation)) - {"unknown"}
        self.assertEqual(regions, set(BUTTON_REGIONS))
        self.assertEqual(set(get_args(SurveyElement.model_fields["category"].annotation)), set(ELEMENT_CATEGORIES))

    def test_prompt_lists_the_genre_roles(self) -> None:
        block = survey_prompt_block(load_game_profile("Candy Crush Soda Saga"))
        self.assertIn("화면 역할(screen_role)", block)
        self.assertIn("- level_start: 레벨 시작", block)
        default_block = survey_prompt_block(load_game_profile("Not In The List"))
        self.assertNotIn("- level_start:", default_block)


class MapLoaderTests(unittest.TestCase):
    def test_default_then_genre_then_game(self) -> None:
        files = _MapFile(self)
        merged = files.load(SMALL_MAP, "match3", "some game")
        self.assertEqual(merged.role("settings").prevalence, "some")
        self.assertEqual(merged.role("settings").screen_types, ("popup",))
        self.assertTrue(any(e.source == "level_start" and e.target == "shop" for e in merged.edges))
        self.assertIsNone(files.load(SMALL_MAP, "_default").role("level_start"))
        self.assertEqual(map_role_ids(files.path)[-1], FALLBACK_ROLE)
        self.assertIn("level_start", map_role_ids(files.path))

    def test_invalid_maps_fail_with_a_clear_message(self) -> None:
        files = _MapFile(self)

        def broken(change) -> dict:
            data = json.loads(json.dumps(SMALL_MAP))
            change(data["genres"]["_default"])
            return data

        cases = {
            "screen_types": lambda layer: layer["nodes"][0].update(screen_types=["saga"]),
            "끝이 노드에 없습니다": lambda layer: layer["edges"].append(_edge("home", "nowhere")),
            "action": lambda layer: layer["edges"].append(_edge("home", "shop", action="buy")),
            "'unknown'는 쓸 수 없습니다": lambda layer: layer["nodes"].append(_node("unknown")),
            "필드가 빠졌습니다": lambda layer: layer["nodes"].append({"id": "half"}),
            "모르는 필드": lambda layer: layer["nodes"][1].update(leaveby=["close"]),
            "trigger": lambda layer: layer["edges"][0].pop("trigger"),
        }
        for message, change in cases.items():
            with self.subTest(message), self.assertRaises(KnowledgeError) as caught:
                files.load(broken(change))
            self.assertIn(message, str(caught.exception))


class ScreenMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = _MapFile(self).load(SMALL_MAP)

    def test_expected_transitions(self) -> None:
        expects = self.map.expects
        self.assertTrue(expects("home", "settings"))      # 적은 간선
        self.assertTrue(expects("settings", "info"))      # "*" 간선
        self.assertTrue(expects("shop", "home"))          # 탭끼리
        self.assertTrue(expects("settings", "home"))      # 닫으면 연 화면으로
        self.assertTrue(expects("info", "shop"))          # 아무 화면에서 연 창을 닫음
        self.assertFalse(expects("settings", "shop"))
        self.assertFalse(expects("home", "nowhere"))

    def test_safe_reachable_skips_risky_edges(self) -> None:
        reachable = self.map.safe_reachable()
        self.assertTrue({"home", "shop", "settings", "info", "level_start"} <= reachable)
        self.assertNotIn("outside_app", reachable)


class NodeRoleTests(unittest.TestCase):
    def test_role_is_set_once_and_filled_when_unknown(self) -> None:
        image = np.random.default_rng(7).integers(0, 255, (64, 64, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "navigation"
            with (
                patch("navigation.graph.ROOT", root),
                patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
                patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
                patch("navigation.graph.IMAGE_DIR", root / "representatives"),
                patch("navigation.graph.SCREEN_HOG_MAX_DISTANCE", 0.0),
            ):
                store = GraphStore()
                first = store.observe(SurveyDecision(screen_type="menu"), image)
                self.assertEqual(first.role, FALLBACK_ROLE)
                store.observe(SurveyDecision(screen_type="menu", screen_role="team"), image)
                store.observe(SurveyDecision(screen_type="menu", screen_role="friends"), image)
                self.assertEqual(store.graph.nodes[first.id].role, "team")


class GenreMapReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = _MapFile(self).load(SMALL_MAP)

    def _report(self, graph: NavigationGraph) -> str:
        lines: list[str] = []
        _append_genre_map_section(lines, graph, self.map)
        return "\n".join(lines)

    def test_found_missing_and_unexpected_transitions(self) -> None:
        graph = NavigationGraph(
            nodes={
                "a": ScreenNode(id="a", screen_type="home", role="home"),
                "b": ScreenNode(id="b", screen_type="menu", role="settings"),
                "c": ScreenNode(id="c", screen_type="shop", role="shop"),
                "d": ScreenNode(id="d", screen_type="popup"),
            },
            edges=[
                NavigationEdge(source="a", target="b", action="톱니"),
                NavigationEdge(source="b", target="c", action="저장"),
            ],
        )
        report = self._report(graph)
        self.assertIn("## Genre Map", report)
        self.assertIn("역할을 붙인 화면 3/4개, 찾은 역할 3개", report)
        self.assertIn("settings(`settings`), some: 화면 1개", report)
        self.assertIn("화면 타입 menu는 지도 후보(popup)에 없음", report)
        self.assertIn("- common: info(`info`), level_start(`level_start`)", report)
        self.assertIn("outside_app(`outside_app`)", report.split("### 못 찾은 역할")[1])
        unexpected = report.split("### 지도에 없는 전환")[1]
        self.assertIn("--(저장)-->", unexpected)
        self.assertNotIn("톱니", unexpected)

    def test_run_without_roles_says_so(self) -> None:
        graph = NavigationGraph(nodes={"a": ScreenNode(id="a", screen_type="home")})
        self.assertIn("역할이 기록된 화면이 없습니다", self._report(graph))


if __name__ == "__main__":
    unittest.main()
