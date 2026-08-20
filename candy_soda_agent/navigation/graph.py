import json
from pathlib import Path

import cv2

from navigation.observer import screen_id
from navigation.schemas import NavigationGraph, ScreenNode, NavigationEdge

ROOT = Path("output/navigation")
GRAPH_PATH = ROOT / "graph.json"
JOURNEY_PATH = ROOT / "journeys.jsonl"
IMAGE_DIR = ROOT / "representatives"

class GraphStore:
    def __init__(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.graph = (
            NavigationGraph.model_validate_json(GRAPH_PATH.read_text())
            if GRAPH_PATH.exists()
            else NavigationGraph()
        )
    def observe(self, decision, image) -> ScreenNode:
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

        node.visits += 1
        self.save()
        return node

    def connect(self, source: str, target: str, action: str) -> None:
        edge = NavigationEdge(source=source, target=target, action=action)
        self.graph.edges.append(edge)
        self.save()

    def log(self, record: dict) -> None:
        with JOURNEY_PATH.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save(self) -> None:
        ROOT.mkdir(parents=True, exist_ok=True)
        GRAPH_PATH.write_text(
            self.graph.model_dump_json(indent=2),
            encoding="utf-8",
        )
