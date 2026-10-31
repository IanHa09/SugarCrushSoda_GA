"""탐색 그래프(화면 노드/간선)를 저장하고 정규화하는 저장소."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable

import cv2

from config import (
    NAVIGATION_DIR,
    NAVIGATION_GRAPH_PATH,
    NAVIGATION_IMAGE_DIR,
    NAVIGATION_JOURNEY_PATH,
    SCREEN_FINGERPRINT_MAX_DISTANCE,
    SCREEN_HOG_MAX_DISTANCE,
    SCREEN_MATCH_MAX_BUTTON_DIFF,
    SCREEN_REUSE_MAX_DISTANCE,
)
from game_knowledge import FALLBACK_ROLE
from image_utils import board_fingerprint, fingerprint_distance
from navigation.observer import screen_buttons, screen_id
from navigation.policy import navigation_action_key_from_label
from navigation.screen_index import ScreenIndex
from schemas import NavigationEdge, NavigationGraph, ScreenNode, SurveyDecision

# 1단계: 게임·run별로 분리된 output/<game_slug>/runs/<run_id>/navigation/ 경로를 씁니다.
ROOT = NAVIGATION_DIR
GRAPH_PATH = NAVIGATION_GRAPH_PATH
JOURNEY_PATH = NAVIGATION_JOURNEY_PATH
IMAGE_DIR = NAVIGATION_IMAGE_DIR


def _adjacency(
    graph: NavigationGraph,
    extra_edges: Iterable[NavigationEdge] = (),
) -> dict[str, list[NavigationEdge]]:
    # source 노드별 나가는 간선 목록입니다(BFS용). extra_edges는 그래프 파일에 없는
    # 지름길(공통 버튼 간선, ui_chrome.teleport_edges)이며 실제 간선 뒤에 붙습니다.
    adjacency: dict[str, list[NavigationEdge]] = {}
    for edge in list(graph.edges) + list(extra_edges):
        adjacency.setdefault(edge.source, []).append(edge)
    return adjacency


# 경로 재생에서 연달아 이 횟수만큼 예상한 화면에 못 간 간선은 길 찾기에서 뺍니다(다시 성공하면
# 되살아남). 실측(run_2026-10-27_05-44-27): 뒤로 키가 안 먹는 탭 화면에서 예전 "뒤로" 간선을
# 세션마다 다시 골라 같은 화면을 맴돌았습니다.
EDGE_BROKEN_AFTER = 2


def bfs_nearest_matching(
    graph: NavigationGraph,
    source: str,
    is_target: Callable[[str], bool],
    extra_edges: Iterable[NavigationEdge] = (),
    avoid: Callable[[str], bool] | None = None,
    skip_edge: Callable[[NavigationEdge], bool] | None = None,
) -> list[NavigationEdge] | None:
    """source에서 시작해 is_target을 만족하는 가장 가까운 노드까지의 간선 경로를
    간선 수 기준 최단으로 찾습니다. source 자신이 만족하면 빈 리스트를 반환합니다.
    도달 가능한 노드 중에 없으면 None입니다(2단계: 원거리 프론티어 탐색에 씁니다).

    avoid: True를 돌려주는 노드는 지나가지도, 도착하지도 않습니다(플레이 화면 등).
    skip_edge: True를 돌려주는 간선은 쓰지 않습니다(고장 난 간선).

    NavigationGraph 데이터만으로 동작하는 자유 함수입니다 — GraphStore(파일 I/O가
    있는 실시간 저장소) 없이도 survey_report.py 같은 곳에서 그대로 재사용합니다."""

    if is_target(source):
        return []

    adjacency = _adjacency(graph, extra_edges)
    visited = {source}
    queue: deque[tuple[str, list[NavigationEdge]]] = deque([(source, [])])
    while queue:
        node_id, path = queue.popleft()
        for edge in adjacency.get(node_id, []):
            if edge.target in visited:
                continue
            if skip_edge is not None and skip_edge(edge):
                continue
            if avoid is not None and avoid(edge.target):
                continue
            visited.add(edge.target)
            new_path = path + [edge]
            if is_target(edge.target):
                return new_path
            queue.append((edge.target, new_path))
    return None


def bfs_path(
    graph: NavigationGraph,
    source: str,
    target: str,
    extra_edges: Iterable[NavigationEdge] = (),
    avoid: Callable[[str], bool] | None = None,
    skip_edge: Callable[[NavigationEdge], bool] | None = None,
) -> list[NavigationEdge] | None:
    """source에서 target까지의 최단 간선 경로. (2단계: 루트 리셋 후 재계획에 씁니다.)"""
    return bfs_nearest_matching(
        graph, source, lambda node_id: node_id == target, extra_edges, avoid, skip_edge
    )


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


def find_fingerprint_match(
    graph: NavigationGraph,
    screen_type: str,
    fingerprint: str,
    max_distance: float,
    fingerprint_of: Callable[[ScreenNode], str] | None = None,
) -> str | None:
    """같은 화면 타입 노드 중 이미지 지문이 가장 가까운 노드를 찾습니다.

    화면 식별의 1차 기준입니다. LLM이 보고하는 버튼 목록은 같은 화면을 봐도 관찰마다
    달라서 버튼만으로는 같은 화면을 못 알아봅니다(실측: 같은 플레이 화면의 버튼 목록이
    8~10개 차이). 이미지는 같은 화면이면 거의 같게 나옵니다.

    비교 기준은 각 노드가 처음 관찰될 때 저장한 지문(고정)이라, 조금씩 다른 관찰이
    이어져도 기준이 떠내려가지 않습니다(find_tolerant_match와 같은 방식).

    화면 타입이 같은 노드만 봅니다. 팝업이 뜬 화면과 그 배경 화면처럼 이미지가 거의
    같으면서 실제로는 다른 화면인 경우를 타입이 걸러 줍니다."""

    if max_distance <= 0 or not fingerprint:
        return None
    best: tuple[tuple[float, int, int], str] | None = None
    for index, node in enumerate(graph.nodes.values()):
        node_fingerprint = fingerprint_of(node) if fingerprint_of else node.fingerprint
        if node.screen_type != screen_type or not node_fingerprint:
            continue
        distance = fingerprint_distance(fingerprint, node_fingerprint)
        if distance > max_distance:
            continue
        score = (distance, -node.visits, index)
        if best is None or score < best[0]:
            best = (score, node.id)
    return best[1] if best else None


def fingerprint_conflicts(
    node: ScreenNode,
    fingerprint: str,
    max_distance: float,
    fingerprint_of: Callable[[ScreenNode], str] | None = None,
) -> bool:
    """지문이 양쪽에 다 있는데 멀면 다른 화면이라고 봅니다.

    버튼 기준 매칭이 붙이려는 노드를 거부하는 데 씁니다. 실측에서 상점 화면 4건이
    버튼을 'close' 하나만 보고해 버튼 기준으로는 한 화면이 됐는데, 지문 거리는
    0.46이었습니다(실제로 서로 다른 상점 화면). 지문은 합치는 데만 쓰는 게 아니라
    이렇게 나누는 데도 씁니다."""

    node_fingerprint = fingerprint_of(node) if fingerprint_of else node.fingerprint
    if max_distance <= 0 or not fingerprint or not node_fingerprint:
        return False
    return fingerprint_distance(fingerprint, node_fingerprint) > max_distance


def unique_node_id(graph: NavigationGraph, node_id: str, fingerprint: str) -> str:
    """이미 쓰이는 ID면 지문 앞자리를 붙여 새 ID를 만듭니다.

    화면 타입과 버튼 목록이 같은데 화면은 다른 경우(위 fingerprint_conflicts 참고)
    자연스러운 ID가 이미 다른 노드 차지라 그대로 쓰면 덮어쓰게 됩니다."""

    if node_id not in graph.nodes:
        return node_id
    suffix = (fingerprint or "x")[:6]
    candidate = f"{node_id}_{suffix}"
    serial = 2
    while candidate in graph.nodes:
        candidate = f"{node_id}_{suffix}{serial}"
        serial += 1
    return candidate


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
        # 공통 띠(네비게이션 바 등)를 지문 비교에서 뺄 때 씁니다(set_chrome_bands).
        self.chrome_bands = None
        self._content_fingerprints: dict[str, str] = {}
        # 경로 계획(BFS)에서 지나가지 않을 노드 판정. navigator가 플레이 화면을 넣습니다.
        self.avoid_node: Callable[[str], bool] | None = None
        # 대표 화면 전체의 HOG 특징 색인(처음 쓸 때 대표 이미지로 만듭니다)과 마지막 비교 결과.
        self._hog_index: ScreenIndex | None = None
        self.last_hog_match: tuple[str | None, float] = (None, 1.0)
        # 그래프에 없는 지름길 간선(공통 버튼)의 재생 실패 횟수. 그래프 간선은 edge.failures에 저장.
        self._extra_failures: dict[tuple[str, str, str], int] = {}

    # -- 공통 띠를 뺀 지문 ------------------------------------------------------

    def image_path(self, node_id: str):
        return IMAGE_DIR / f"{node_id}.png"

    def load_image(self, node_id: str):
        path = self.image_path(node_id)
        return cv2.imread(str(path)) if path.exists() else None

    def set_chrome_bands(self, bands) -> None:
        """공통 띠를 정합니다. None이거나 띠가 없으면 예전처럼 화면 전체 지문을 씁니다."""
        self.chrome_bands = bands if bands is not None and bands.active else None
        self._content_fingerprints.clear()
        self._hog_index = None  # 비교 영역이 바뀌었으니 색인을 다시 만듭니다.

    def _content(self, image):
        """같은 화면 비교에 쓰는 영역: 공통 띠(네비게이션 바 등)를 뺀 본문."""
        if image is None or self.chrome_bands is None:
            return image
        return self.chrome_bands.content(image)

    def _fingerprint_for_image(self, image) -> str:
        return board_fingerprint(self._content(image))

    # -- 대표 화면 전체와 HOG 비교 ------------------------------------------------

    def _screen_index(self) -> ScreenIndex:
        if self._hog_index is None:
            self._hog_index = ScreenIndex.build(
                self.graph.nodes, lambda node_id: self._content(self.load_image(node_id))
            )
        return self._hog_index

    def _hog_match(self, image) -> str | None:
        """화면 타입과 상관없이 대표 화면 전체 중 가장 가까운 노드(거리가 임계값 이하일 때)."""
        if SCREEN_HOG_MAX_DISTANCE <= 0 or image is None:
            return None
        self.last_hog_match = self._screen_index().nearest(self._content(image))
        node_id, distance = self.last_hog_match
        return node_id if node_id is not None and distance <= SCREEN_HOG_MAX_DISTANCE else None

    # -- 아는 화면의 분석 재사용 ------------------------------------------------

    def _decision_path(self, node_id: str):
        return ROOT / "decisions" / f"{node_id}.json"

    def load_decision(self, node_id: str) -> SurveyDecision | None:
        """노드를 처음 분석한 결과. 없거나 읽을 수 없으면(형식이 바뀐 옛 파일 등) None."""
        path = self._decision_path(node_id)
        if not path.exists():
            return None
        try:
            return SurveyDecision.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save_decision(
        self, node_id: str, decision: SurveyDecision, *, overwrite: bool = False
    ) -> None:
        """분석 결과를 노드 기준으로 저장합니다. 이미 있으면 그대로 둡니다(기준 고정:
        다시 분석할 때마다 버튼 이름이 조금씩 달라져 같은 버튼이 새 버튼으로 잡히던 것을 막음).
        overwrite: 막혀서 다시 분석한 결과(기존 후보에 새 후보를 더한 것)로 바꿀 때만 씁니다."""
        if not overwrite and self.load_decision(node_id) is not None:
            return
        path = self._decision_path(node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")

    def known_decision(
        self, image, same_node: str | None = None
    ) -> tuple[str, SurveyDecision] | None:
        """확실히 아는 화면이면 (노드 ID, 그 화면을 처음 분석한 결과)를 돌려줍니다.

        same_node: 직전 캡처와 픽셀이 같아 이미 같은 화면으로 확인된 노드.
        없으면 HOG 거리가 SCREEN_REUSE_MAX_DISTANCE 이하인 대표 화면을 찾습니다
        (같은 노드 판정 SCREEN_HOG_MAX_DISTANCE보다 엄격). 저장된 분석이 없으면 None."""
        if SCREEN_REUSE_MAX_DISTANCE <= 0:
            return None
        node_id = same_node
        if node_id is None and image is not None:
            self.last_hog_match = self._screen_index().nearest(self._content(image))
            nearest, distance = self.last_hog_match
            node_id = nearest if distance <= SCREEN_REUSE_MAX_DISTANCE else None
        decision = self.load_decision(node_id) if node_id is not None else None
        return (node_id, decision) if decision is not None else None

    def _node_fingerprint(self, node: ScreenNode) -> str:
        """노드의 비교용 지문. 공통 띠가 있으면 대표 이미지에서 띠를 빼고 다시 계산합니다
        (대표 이미지가 없으면 저장된 전체 지문으로 대신합니다)."""
        if self.chrome_bands is None:
            return node.fingerprint
        cached = self._content_fingerprints.get(node.id)
        if cached is not None:
            return cached
        image = self.load_image(node.id)
        value = self._fingerprint_for_image(image) if image is not None else node.fingerprint
        self._content_fingerprints[node.id] = value
        return value

    def _normalize_graph(self) -> bool:
        """간선 중복을 정리합니다.

        전에는 여기서 상점·설정 같은 화면 타입을 타입 이름 하나로 묶는 재매핑도
        했습니다(SINGLETON_SCREEN_TYPES). 실측해 보니 상점은 서로 다른 화면이 여러
        개라 과병합이었고, 지금은 화면 이미지 지문이 그 판단을 대신하므로 뺐습니다."""

        before = self.graph.model_dump()
        nodes = self.graph.nodes

        edges: list[NavigationEdge] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.graph.edges:
            source = edge.source
            target = edge.target
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
                failures=edge.failures,
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
        self,
        source: str,
        is_target: Callable[[str], bool],
        extra_edges: Iterable[NavigationEdge] = (),
    ) -> list[NavigationEdge] | None:
        return bfs_nearest_matching(
            self.graph, source, is_target, extra_edges, self.avoid_node, self.is_broken
        )

    def bfs_path(
        self,
        source: str,
        target: str,
        extra_edges: Iterable[NavigationEdge] = (),
    ) -> list[NavigationEdge] | None:
        return bfs_path(self.graph, source, target, extra_edges, self.avoid_node, self.is_broken)

    # -- 고장 난 간선 -----------------------------------------------------------

    def _stored_edge(self, edge: NavigationEdge) -> NavigationEdge | None:
        return next(
            (
                stored for stored in self.graph.edges
                if stored.source == edge.source
                and stored.target == edge.target
                and stored.action_key == edge.action_key
            ),
            None,
        )

    def is_broken(self, edge: NavigationEdge) -> bool:
        key = (edge.source, edge.action_key, edge.target)
        failures = max(edge.failures, self._extra_failures.get(key, 0))
        return failures >= EDGE_BROKEN_AFTER

    def note_edge_result(self, edge: NavigationEdge, ok: bool) -> None:
        """경로 재생(또는 루트로 가는 길)에서 이 간선을 눌러 본 결과를 남깁니다.
        실패가 EDGE_BROKEN_AFTER번 이어지면 길 찾기에서 빠지고, 성공하면 0으로 돌아갑니다."""
        stored = self._stored_edge(edge)
        if stored is None:
            key = (edge.source, edge.action_key, edge.target)
            self._extra_failures[key] = 0 if ok else self._extra_failures.get(key, 0) + 1
            return
        failures = 0 if ok else stored.failures + 1
        if failures != stored.failures:
            stored.failures = failures
            if failures == EDGE_BROKEN_AFTER:
                print(
                    f"[PATH] '{stored.action}' 간선({stored.source} -> {stored.target})이 "
                    f"{failures}번 연달아 안 맞아 길 찾기에서 뺍니다."
                )
            self.save()

    def bfs_spanning_tree(
        self, root: str
    ) -> tuple[dict[str, list[NavigationEdge]], list[NavigationEdge]]:
        return bfs_spanning_tree(self.graph, root)

    def unreached_from(self, root: str) -> list[str]:
        return unreached_from(self.graph, root)

    def observe(self, decision, image) -> ScreenNode:
        """화면을 노드로 기록하고 대표 이미지를 저장합니다.

        같은 화면을 알아보는 순서:
        1. HOG 거리 — 대표 화면 전체와 비교하며, 화면 타입은 보지 않습니다(screen_index.py).
        2. 이미지 지문(dHash) — 같은 화면 타입 노드끼리만 비교합니다.
        3. 정확히 같은 ID(화면 타입 + 버튼 목록) — 단, 지문이 충돌하면 쓰지 않습니다.
        4. 버튼 1개 차이 허용 매칭 — 여기도 지문 충돌은 거부합니다.
        어디에도 못 붙으면 새 노드입니다.
        """

        natural_id = screen_id(decision)
        buttons = screen_buttons(decision)
        # 노드에 저장하는 지문은 항상 화면 전체 지문이고, 비교에는 공통 띠를 뺀 지문을
        # 씁니다(띠가 아직 없으면 둘은 같습니다).
        fingerprint = board_fingerprint(image) if image is not None else ""
        compare_fingerprint = self._fingerprint_for_image(image) if image is not None else ""

        def _usable(candidate_id: str | None) -> str | None:
            if candidate_id is None:
                return None
            node = self.graph.nodes.get(candidate_id)
            if node is None:
                return None
            if fingerprint_conflicts(
                node,
                compare_fingerprint,
                SCREEN_FINGERPRINT_MAX_DISTANCE,
                self._node_fingerprint,
            ):
                return None
            return candidate_id

        node_id = self._hog_match(image)
        if node_id is None:
            node_id = find_fingerprint_match(
                self.graph,
                decision.screen_type,
                compare_fingerprint,
                SCREEN_FINGERPRINT_MAX_DISTANCE,
                self._node_fingerprint,
            )
        if node_id is None:
            node_id = _usable(natural_id)
        if node_id is None:
            node_id = _usable(find_tolerant_match(
                self.graph,
                decision.screen_type,
                buttons,
                SCREEN_MATCH_MAX_BUTTON_DIFF,
            ))
        if node_id is None:
            node_id = unique_node_id(self.graph, natural_id, fingerprint)

        image_path = IMAGE_DIR / f"{node_id}.png"

        if not image_path.exists():
            if not cv2.imwrite(str(image_path), image):
                raise OSError(f"대표 이미지 저장 실패: {image_path}")
            # 새 대표 화면은 HOG 색인에도 넣습니다(색인이 아직 없으면 처음 쓸 때 파일에서 만듭니다).
            if self._hog_index is not None:
                self._hog_index.add(node_id, self._content(image))

        node = self.graph.nodes.get(node_id)
        if node is None:
            node = ScreenNode(
                id=node_id,
                screen_type=decision.screen_type,
                summary=decision.summary,
                representative=str(image_path),
                buttons=buttons,
                fingerprint=fingerprint,
                role=decision.screen_role,
            )
            self.graph.nodes[node_id] = node
        else:
            node.representative = str(image_path)
            # 지문이 생기기 전에 만들어진 노드는 이번 관찰로 기준을 채워 둡니다.
            # 그래야 다음 관찰부터 이 노드도 지문으로 찾을 수 있습니다.
            if not node.fingerprint and fingerprint:
                node.fingerprint = fingerprint
            # 역할은 처음 정한 값을 지키고, 비었거나 모를 때만 이번 분석으로 채웁니다.
            if node.role in ("", FALLBACK_ROLE):
                node.role = decision.screen_role

        node.visits += 1
        self.save()
        return node

    def revisit(self, node_id: str) -> ScreenNode:
        """픽셀로 직전 화면과 같다고 확인된 관찰을 기존 노드의 방문으로만 셉니다.

        같은 화면인데 LLM이 화면 타입을 바꿔 부르면(실측: 프로필·친구·고객센터를
        모두 settings_menu로, 또는 그 반대) observe()는 타입이 달라 지문 매칭을 못 하고
        새 노드를 만듭니다. 화면 픽셀이 그대로면 LLM 분류와 무관하게 같은 노드입니다."""

        node = self.graph.nodes[node_id]
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
        # 동일 간선이 없으면 새 간선을 추가합니다. 있으면 실제로 다시 이어졌으니 고장 표시를 지웁니다.
        existing = self._stored_edge(
            NavigationEdge(source=source, target=target, action=action, action_key=action_key)
        )
        if existing is not None:
            if existing.failures:
                existing.failures = 0
                self.save()
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
