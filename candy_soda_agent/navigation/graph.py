"""탐색 그래프(화면 노드/간선)를 저장하고 정규화하는 저장소."""

import json
from pathlib import Path

import cv2

from navigation.observer import (
    SINGLETON_SCREEN_TYPES,
    canonical_screen_id,
    screen_id,
)
from navigation.policy import navigation_action_key_from_label
from schemas import NavigationEdge, NavigationGraph, ScreenNode

ROOT = Path("output/navigation")
GRAPH_PATH = ROOT / "graph.json"
JOURNEY_PATH = ROOT / "journeys.jsonl"
IMAGE_DIR = ROOT / "representatives"


class GraphStore:
    def __init__(self):
        # 그래프 파일을 불러오고, 없으면 새로 만듭니다.
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.graph = (
            NavigationGraph.model_validate_json(GRAPH_PATH.read_text())
            if GRAPH_PATH.exists()
            else NavigationGraph()
        )
        if self._normalize_graph():
            self.save()

    def _normalize_graph(self) -> bool:
        """기존 화면과 간선 중복 정리."""

        before = self.graph.model_dump()
        remapped: dict[str, str] = {}
        nodes: dict[str, ScreenNode] = {}

        for old_id, node in self.graph.nodes.items():
            node_id = (
                canonical_screen_id(node.screen_type)
                if node.screen_type in SINGLETON_SCREEN_TYPES
                else old_id
            )
            remapped[old_id] = node_id
            existing = nodes.get(node_id)
            if existing is None:
                nodes[node_id] = node.model_copy(update={"id": node_id})
                continue

            existing.visits += node.visits
            if len(node.summary) > len(existing.summary):
                existing.summary = node.summary
            if not existing.representative and node.representative:
                existing.representative = node.representative

        edges: list[NavigationEdge] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.graph.edges:
            source = remapped.get(edge.source, edge.source)
            target = remapped.get(edge.target, edge.target)
            action_key = (
                edge.action_key
                or navigation_action_key_from_label(edge.action)
            )
            identity = (source, target, action_key)
            if identity in seen_edges:
                continue
            seen_edges.add(identity)
            edges.append(NavigationEdge(
                source=source,
                target=target,
                action=edge.action,
                action_key=action_key,
            ))

        self.graph.nodes = nodes
        self.graph.edges = edges
        return before != self.graph.model_dump()

    def explored_actions(self) -> set[str]:
        # 이미 탐색한 (노드, 행동) 조합 집합을 반환합니다.
        return {
            f"{edge.source}:{edge.action_key}"
            for edge in self.graph.edges
            if edge.action_key
        }

    def observe(self, decision, image) -> ScreenNode:
        # 화면을 노드로 기록하고 대표 이미지를 저장합니다.
        node_id = screen_id(decision)
        image_path = IMAGE_DIR / f"{node_id}.png"

        if not image_path.exists() and not cv2.imwrite(str(image_path), image):
            raise OSError(f"대표 이미지 저장 실패: {image_path}")

        node = self.graph.nodes.get(node_id)
        if node is None:
            node = ScreenNode(
                id=node_id,
                screen_type=decision.screen_type,
                summary=decision.summary,
                representative=str(image_path),
            )
            self.graph.nodes[node_id] = node
        else:
            node.representative = str(image_path)

        node.visits += 1
        self.save()
        return node

    def connect(
        self,
        source: str,
        target: str,
        action: str,
        action_key: str,
    ) -> None:
        # 동일 간선이 없으면 새 간선을 추가합니다.
        if any(
            edge.source == source
            and edge.target == target
            and edge.action_key == action_key
            for edge in self.graph.edges
        ):
            return
        edge = NavigationEdge(
            source=source,
            target=target,
            action=action,
            action_key=action_key,
        )
        self.graph.edges.append(edge)
        self.save()

    def log(self, record: dict) -> None:
        # 탐색 기록 한 줄을 journeys.jsonl에 추가합니다.
        with JOURNEY_PATH.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save(self) -> None:
        ROOT.mkdir(parents=True, exist_ok=True)
        GRAPH_PATH.write_text(
            self.graph.model_dump_json(indent=2),
            encoding="utf-8",
        )
