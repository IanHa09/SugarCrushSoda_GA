"""탐색 그래프(화면 노드/간선)를 저장하고 정규화하는 저장소."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable

import cv2

from config import (
    NAVIGATION_DIR,
    NAVIGATION_GRAPH_PATH,
    NAVIGATION_IMAGE_DIR,
    NAVIGATION_JOURNEY_PATH,
    SCREEN_MATCH_MAX_BUTTON_DIFF,
)
from navigation.observer import (
    SINGLETON_SCREEN_TYPES,
    canonical_screen_id,
    screen_buttons,
    screen_id,
)
from navigation.policy import navigation_action_key_from_label
from schemas import NavigationEdge, NavigationGraph, ScreenNode

# 1단계: 게임·run별로 분리된 output/<game_slug>/runs/<run_id>/navigation/ 경로를 씁니다.
ROOT = NAVIGATION_DIR
GRAPH_PATH = NAVIGATION_GRAPH_PATH
JOURNEY_PATH = NAVIGATION_JOURNEY_PATH
IMAGE_DIR = NAVIGATION_IMAGE_DIR


def _adjacency(graph: NavigationGraph) -> dict[str, list[NavigationEdge]]:
    # source 노드별 나가는 간선 목록입니다(BFS용).
    adjacency: dict[str, list[NavigationEdge]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge)
    return adjacency


def bfs_nearest_matching(
    graph: NavigationGraph,
    source: str,
    is_target: Callable[[str], bool],
) -> list[NavigationEdge] | None:
    """source에서 시작해 is_target을 만족하는 가장 가까운 노드까지의 간선 경로를
    간선 수 기준 최단으로 찾습니다. source 자신이 만족하면 빈 리스트를 반환합니다.
    도달 가능한 노드 중에 없으면 None입니다(2단계: 원거리 프론티어 탐색에 씁니다).

    NavigationGraph 데이터만으로 동작하는 자유 함수입니다 — GraphStore(파일 I/O가
    있는 실시간 저장소) 없이도 survey_report.py 같은 곳에서 그대로 재사용합니다."""

    if is_target(source):
        return []

    adjacency = _adjacency(graph)
    visited = {source}
    queue: deque[tuple[str, list[NavigationEdge]]] = deque([(source, [])])
    while queue:
        node_id, path = queue.popleft()
        for edge in adjacency.get(node_id, []):
            if edge.target in visited:
                continue
            visited.add(edge.target)
            new_path = path + [edge]
            if is_target(edge.target):
                return new_path
            queue.append((edge.target, new_path))
    return None


def bfs_path(graph: NavigationGraph, source: str, target: str) -> list[NavigationEdge] | None:
    """source에서 target까지의 최단 간선 경로. (2단계: 루트 리셋 후 재계획에 씁니다.)"""
    return bfs_nearest_matching(graph, source, lambda node_id: node_id == target)


def bfs_spanning_tree(
    graph: NavigationGraph,
    root: str,
) -> tuple[dict[str, list[NavigationEdge]], list[NavigationEdge]]:
    """root에서 BFS로 만든 스패닝 트리와, 트리에 들어가지 못한 교차 간선을 함께
    반환합니다(3단계: 그래프에는 사이클이 있어 트리가 아니므로 BFS로 투영합니다).
    반환값: ({parent_id: [자식으로 가는 간선, ...]}, 교차 간선 목록).
    BFS라서 트리 깊이가 곧 "루트에서 몇 번 눌러야 도달하는가"와 같습니다."""

    adjacency = _adjacency(graph)
    tree: dict[str, list[NavigationEdge]] = {}
    cross_links: list[NavigationEdge] = []
    visited = {root}
    queue: deque[str] = deque([root])
    while queue:
        node_id = queue.popleft()
        for edge in adjacency.get(node_id, []):
            if edge.target not in visited:
                visited.add(edge.target)
                tree.setdefault(node_id, []).append(edge)
                queue.append(edge.target)
            else:
                cross_links.append(edge)
    return tree, cross_links


def unreached_from(graph: NavigationGraph, root: str) -> list[str]:
    """root에서 BFS로 전혀 도달할 수 없는 노드 ID 목록(진단용)."""
    tree, _ = bfs_spanning_tree(graph, root)
    reached = {root}
    for edges in tree.values():
        reached.update(edge.target for edge in edges)
    return [node_id for node_id in graph.nodes if node_id not in reached]


def screen_type_node_counts(graph: NavigationGraph) -> dict[str, int]:
    """화면 타입별 노드 수(진단용)."""
    counts: dict[str, int] = {}
    for node in graph.nodes.values():
        counts[node.screen_type] = counts.get(node.screen_type, 0) + 1
    return counts


def fragmented_screen_types(
    graph: NavigationGraph | None,
    warn_count: int,
) -> dict[str, int]:
    """노드가 warn_count개 이상 쌓인 화면 타입만 골라 반환합니다(진단용).

    같은 화면이 여러 노드로 쪼개졌다는 신호일 수 있습니다. 다만 게임에 실제로
    그만큼 다른 화면이 있을 수도 있어 판단 재료일 뿐이며, 여기서 노드를 합치지는
    않습니다. warn_count가 0이면 진단을 끕니다."""

    if warn_count <= 0 or graph is None or not graph.nodes:
        return {}
    return {
        screen_type: count
        for screen_type, count in screen_type_node_counts(graph).items()
        if count >= warn_count
    }


def buttons_within_tolerance(a: list[str], b: list[str], max_diff: int) -> bool:
    """두 버튼 목록이 같은 화면으로 볼 만큼 가까운지 판단합니다."""
    set_a, set_b = set(a), set(b)
    # 공통 버튼이 하나도 없으면(예: 버튼 없는 화면 vs 닫기만 있는 화면) 같은 화면이라고
    # 볼 근거가 없어 제외합니다.
    return bool(set_a & set_b) and len(set_a ^ set_b) <= max_diff


def find_tolerant_match(
    graph: NavigationGraph,
    screen_type: str,
    buttons: list[str],
    max_diff: int,
) -> str | None:
    """정확한 ID가 없을 때, 같은 화면 타입 노드 중 버튼 목록이 허용 범위 안인 노드를
    찾습니다. 여럿이면 차이가 가장 적은 것 -> 방문이 많은 것 -> 먼저 생긴 것 순입니다.
    비교 기준은 각 노드가 처음 관찰될 때 저장한 버튼 목록(고정)이라, 조금씩 다른
    관찰이 이어져도 기준이 떠내려가지 않습니다."""

    if max_diff <= 0:
        return None
    best: tuple[tuple[int, int, int], str] | None = None
    for index, node in enumerate(graph.nodes.values()):
        if node.screen_type != screen_type or not node.buttons:
            continue
        if not buttons_within_tolerance(node.buttons, buttons, max_diff):
            continue
        score = (len(set(node.buttons) ^ set(buttons)), -node.visits, index)
        if best is None or score < best[0]:
            best = (score, node.id)
    return best[1] if best else None


def resolve_root_screen_id(graph: NavigationGraph, configured_root: str | None) -> str | None:
    """트리 루트를 정합니다: 설정된 ROOT_SCREEN_ID가 그래프에 있으면 그걸 쓰고,
    아니면 이번 그래프에서 가장 먼저 관찰된(=nodes 딕셔너리에 가장 먼저 들어간)
    화면으로 폴백합니다. GraphStore.observe()는 새 노드일 때만 nodes에 넣으므로
    딕셔너리 순서가 곧 최초 관찰 순서입니다."""

    if not graph or not graph.nodes:
        return None
    if configured_root and configured_root in graph.nodes:
        return configured_root
    return next(iter(graph.nodes))


class GraphStore:
    def __init__(self):
        # 그래프 파일을 불러오고, 없으면 새로 만듭니다.
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.graph = (
            NavigationGraph.model_validate_json(
                GRAPH_PATH.read_text(encoding="utf-8")
            )
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
        # 주의: 이 집합은 "간선이 생겼는지"만 보므로, 눌러도 화면이 안 바뀐 버튼은
        # 빠집니다. 2단계부터 프론티어/재생 판단은 이 메서드 대신
        # navigation.ledger.LedgerStore(시도 자체를 기록)를 기준으로 합니다.
        return {
            f"{edge.source}:{edge.action_key}"
            for edge in self.graph.edges
            if edge.action_key
        }

    # 아래 BFS 메서드들은 모듈 수준 자유 함수(self.graph만 필요)에 위임합니다.
    # survey_report.py 등은 GraphStore(파일 I/O 있는 실시간 저장소) 없이 같은
    # 로직을 NavigationGraph 데이터에 바로 쓸 수 있습니다.

    def bfs_nearest_matching(
        self, source: str, is_target: Callable[[str], bool]
    ) -> list[NavigationEdge] | None:
        return bfs_nearest_matching(self.graph, source, is_target)

    def bfs_path(self, source: str, target: str) -> list[NavigationEdge] | None:
        return bfs_path(self.graph, source, target)

    def bfs_spanning_tree(
        self, root: str
    ) -> tuple[dict[str, list[NavigationEdge]], list[NavigationEdge]]:
        return bfs_spanning_tree(self.graph, root)

    def unreached_from(self, root: str) -> list[str]:
        return unreached_from(self.graph, root)

    def observe(self, decision, image) -> ScreenNode:
        # 화면을 노드로 기록하고 대표 이미지를 저장합니다.
        node_id = screen_id(decision)
        buttons = screen_buttons(decision)
        if (
            node_id not in self.graph.nodes
            and decision.screen_type not in SINGLETON_SCREEN_TYPES
        ):
            # 정확히 같은 ID가 없으면 버튼 1개 차이까지 허용해 기존 화면을 찾습니다.
            matched = find_tolerant_match(
                self.graph,
                decision.screen_type,
                buttons,
                SCREEN_MATCH_MAX_BUTTON_DIFF,
            )
            if matched is not None:
                node_id = matched
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
                buttons=buttons,
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
