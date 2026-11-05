"""조사 로그를 읽기 쉬운 Markdown으로 변환.

3단계: 게임 UI에는 사이클이 있어 그래프 자체는 트리가 아니므로, 그래프를 진실의
원천으로 두고 루트에서 BFS로 만든 스패닝 트리를 "화면 계층"으로 보여줍니다(BFS라서
트리 깊이 = 루트에서 몇 번 눌러야 도달하는지와 같습니다). 트리에 못 들어간 간선은
"교차 링크"로 따로 보여주고(머메이드에서는 점선), 원장이 있으면 "아직 못 눌러본
프론티어"와 "도달 실패로 포기한 항목"도 커버리지 근거로 보여줍니다.

옛 generate_survey_markdown은 같은 요소가 여러 화면에 나와도 setdefault로 첫 등장만
남기고 나머지를 버렸습니다. 이번에 "요소가 어느 화면들에 나왔는지"(동시출현)를 전부
모으도록 고쳤습니다 — 자산 자체가 아니라 자산 간 상호작용을 보려는 목적에 맞습니다.

분량 정리: 보고서가 길어도 판단 재료가 늘지 않는 부분을 걷어냈습니다. 요소는 표기만
다른 이름을 한 항목으로 묶고 이름을 되풀이하던 Evidence 줄을 뺐으며, 캡처 목록은
화면 타입이 바뀌는 지점만 남깁니다. 같은 화면으로 되돌아오는 간선은 "눌러도 안
바뀌었다"는 뜻뿐이라 화면별 한 줄로 모읍니다. 반대로 트리에 못 들어간 화면은 그냥
빠지면 관찰 여부조차 알 수 없어 따로 밝혀 둡니다.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from config import (
    MERMAID_MAX_NODES,
    ROOT_SCREEN_ID,
    SCREEN_FRAGMENTATION_WARN_COUNT,
    SURVEY_REPORT_EVIDENCE_LIMIT,
    SURVEY_REPORT_PATH,
)
from config import NAVIGATION_GRAPH_PATH as CURRENT_RUN_GRAPH_PATH
from config import NAVIGATION_LEDGER_PATH as CURRENT_RUN_LEDGER_PATH
from config import NAVIGATION_UI_CHROME_PATH as CURRENT_RUN_UI_CHROME_PATH
from game_knowledge import PREVALENCE_ORDER, ScreenMap, active_profile, screen_type_labels
from navigation.graph import (
    bfs_spanning_tree,
    fragmented_screen_types,
    resolve_root_screen_id,
    unreached_from,
)
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore, is_open_entry
from navigation.policy import nav_tab_index, nav_tab_ordinal, navigation_action_key_from_label
from schemas import NavigationEdge, NavigationGraph
from survey_utils import canonical_name_tokens


# 화면 타입 라벨과 순서는 knowledge/screen_types.json에서 읽습니다(꺼진 후보 타입 포함).
SCREEN_TYPE_LABELS = screen_type_labels()


def _display(value: str | None, fallback: str = "unknown") -> str:
    """값이 비어 있으면 대체 문자열을 반환합니다."""
    if value is None:
        return fallback
    stripped = str(value).strip()
    return stripped if stripped else fallback


def _game_line() -> str:
    """보고서 Overview의 게임·장르 한 줄(GAME_TITLE과 knowledge/games.txt 기준)."""
    try:
        profile = active_profile()
    except Exception as error:  # noqa: BLE001 - 지식 파일 문제로 보고서를 못 쓰면 안 됩니다.
        return f"(게임 지식 읽기 실패: {error})"
    genre = profile.genre_label or profile.genre
    note = "" if profile.known else " — games.txt에 없는 제목"
    return f"{profile.title} (장르: {genre}{note})"


def _screen_label(screen_type: str) -> str:
    return SCREEN_TYPE_LABELS.get(screen_type, screen_type.replace("_", " ").title())


def _relative_link(path: str | None, base: Path) -> str:
    """보고서 파일에서 그 파일로 가는 상대 경로를 만듭니다.

    Path.relative_to는 base 아래에 있는 경로만 처리해서, 캡처 이미지처럼 보고서
    폴더 밖(output/captures/)에 있는 파일이면 ValueError로 빠지고 원본 경로가 그대로
    나왔습니다. 그 경로는 링크로 열리지 않아 증거 구실을 못 했습니다. os.path.relpath는
    `..`를 써서 위로 올라가는 경로도 만들어 줍니다."""

    if not path:
        return ""
    # 윈도우에서 적힌 기록(output\captures\...)을 다른 OS에서 읽으면 역슬래시가
    # 폴더 구분자로 안 보여 경로가 통째로 파일 이름이 됩니다.
    target = Path(str(path).replace("\\", "/"))
    try:
        return Path(os.path.relpath(target.resolve(), base.resolve())).as_posix()
    except ValueError:
        # 윈도우에서 드라이브가 다르면 상대 경로가 없습니다. 그때만 원본을 둡니다.
        return path


def _mermaid_node_id(raw_id: str) -> str:
    """임의 문자열을 mermaid 노드 ID로 변환합니다."""
    safe_name = "".join(
        character if character.isalnum() else "_" for character in raw_id
    ).strip("_")
    return f"screen_{safe_name or 'unknown'}"


def _mermaid_text(value: str) -> str:
    """따옴표로 감싼 mermaid 라벨 안에 넣을 수 있게 문자를 바꿉니다.

    큰따옴표는 라벨을 끝내고, |는 간선 라벨 구분자이고, 백틱으로 시작하면 markdown
    문자열로 해석되므로 각각 바꿉니다. 괄호·!·? 같은 나머지 문자는 따옴표 안이면
    그대로 써도 됩니다."""
    return value.replace('"', "'").replace("|", "/").replace("`", "'")


def _screen_key_for_record(record: dict[str, Any]) -> str:
    """이 기록이 어느 화면에 속하는지 판단할 키를 고릅니다.

    autodrive로 만든 기록은 navigation 그래프 노드 ID(screen_id)를 그대로 갖고
    있어 그래프와 정확히 대응됩니다. 그 필드가 없는(수동 조사 모드 등, 그래프가
    없는) 기록은 survey_utils의 screen_signature로, 그것도 없으면 화면 타입
    이름으로 대략 묶습니다."""

    screen_id = record.get("screen_id")
    if screen_id:
        return screen_id
    signature = record.get("dedupe", {}).get("screen_signature")
    if signature:
        return f"sig:{signature}"
    return f"type:{_display(record.get('survey', {}).get('screen_type'))}"


def _append_fragmentation_warning(
    lines: list[str],
    graph: NavigationGraph,
) -> None:
    """화면 타입별 노드 수가 기준을 넘으면 Overview에 진단 줄을 덧붙입니다.

    지금까지는 "화면 타입별" 숫자만 찍고 사람이 보고 판단해야 했습니다. 기준
    (SCREEN_FRAGMENTATION_WARN_COUNT)을 넘는 타입을 따로 뽑아 주므로, 같은 화면이
    여러 노드로 쪼개지고 있는지 눈으로 훑지 않아도 됩니다. 판단 재료일 뿐이라
    노드를 자동으로 합치지는 않습니다."""

    fragmented = fragmented_screen_types(graph, SCREEN_FRAGMENTATION_WARN_COUNT)
    if not fragmented:
        return
    detail = ", ".join(
        f"{_screen_label(screen_type)} {count}개"
        for screen_type, count in sorted(fragmented.items())
    )
    lines.append(
        f"- ⚠️ 화면 파편화 의심: {detail} "
        f"(기준 {SCREEN_FRAGMENTATION_WARN_COUNT}개 이상). 실제로 서로 다른 화면이면 "
        "무시해도 되고, 같은 화면이 쪼개진 것이면 아래 Screen Hierarchy에서 "
        "대표 이미지를 비교해 확인하세요."
    )


# -- 구조도용 보기: 탭 화면 묶기와 화면 이름 -----------------------------------------
#
# 탭바는 탭 화면마다 보여서 탭 화면끼리는 서로 다 이어집니다(실측: 상점→친구, 팀→상점…). 관찰한
# 대로 다 그리면 그 간선이 구조를 가리므로 구조도·계층에서는 탭 화면을 루트 아래 탭 순서대로
# 나란히 두고 탭 화면끼리 오간 간선은 뺍니다. graph.json은 그대로 둡니다(길 찾기에 필요).


def _tab_screens(graph: NavigationGraph, root_id: str) -> dict[str, int]:
    """탭 화면 ID -> 탭 순서. 선택된 탭이 보인 화면 중 루트이거나 그 탭을 눌러 도착한 화면만
    셉니다(팝업 뒤로 비친 탭바를 읽은 화면은 그 탭을 눌러 온 게 아니라 빠집니다)."""

    def reached_by_own_tab(node) -> bool:
        return any(
            edge.target == node.id
            and (
                nav_tab_index(edge.action) == node.tab_index
                or (node.tab_name and edge.action == node.tab_name)
            )
            for edge in graph.edges
        )

    return {
        node.id: node.tab_index
        for node in graph.nodes.values()
        if node.tab_index >= 0 and (node.id == root_id or reached_by_own_tab(node))
    }


def _tab_labels(graph: NavigationGraph, tabs: dict[str, int]) -> dict[int, str]:
    """탭 순서 -> 보고서에 쓸 탭 이름 하나. 들어가 본 탭은 "둘째 탭 (친구)"(그 화면에서 선택된
    탭 아래 뜬 이름), 아니면 처음 기록된 라벨로 고정합니다(아이콘 묘사가 화면마다 달라서)."""

    labels: dict[int, str] = {}
    for edge in graph.edges:
        index = nav_tab_index(edge.action)
        if index is not None:
            labels.setdefault(index, edge.action)
    for node_id, index in tabs.items():
        name = graph.nodes[node_id].tab_name
        if name:
            labels[index] = f"{nav_tab_ordinal(index)} 탭 ({name})"
    return labels


def _tab_text(action: str, tab_labels: dict[int, str]) -> str:
    index = nav_tab_index(action)
    return tab_labels.get(index, action) if index is not None else action


def _node_names(graph: NavigationGraph, tabs: dict[str, int]) -> dict[str, str]:
    """노드 표시 이름: 탭 화면은 탭 이름, 나머지는 화면 타입 이름이고 같은 이름이 여럿이면
    발견 순서대로 "Popup(1)", "Popup(2)"처럼 번호를 붙입니다."""

    names = {
        node.id: (
            (node.tab_name or f"{nav_tab_ordinal(node.tab_index)} 탭")
            if node.id in tabs
            else _screen_label(node.screen_type)
        )
        for node in graph.nodes.values()
    }
    totals = Counter(names.values())
    seen: Counter[str] = Counter()
    for node_id, name in names.items():
        if totals[name] > 1:
            seen[name] += 1
            names[node_id] = f"{name}({seen[name]})"
    return names


def _structure_view(
    graph: NavigationGraph, root_id: str | None
) -> tuple[NavigationGraph, dict[str, str], dict[int, str], int]:
    """(구조도용 그래프, 노드 이름, 탭 이름, 뺀 탭 간선 수). 구조도용 그래프는 루트→탭 화면 간선을
    탭 순서대로 앞에 두고(홈에서 직접 누른 적이 없어도), 탭 화면끼리 오간 간선은 뺍니다."""

    tabs = _tab_screens(graph, root_id) if root_id else {}
    tab_labels = _tab_labels(graph, tabs)
    edges = [
        NavigationEdge(
            source=root_id, target=node_id,
            action=tab_labels.get(index) or f"{nav_tab_ordinal(index)} 탭",
        )
        for node_id, index in sorted(tabs.items(), key=lambda item: item[1])
        if node_id != root_id
    ]
    hidden = 0
    for edge in graph.edges:
        if edge.source in tabs and edge.target in tabs and edge.source != edge.target:
            hidden += edge.source != root_id  # 루트→탭은 위에서 다시 그리므로 셈에서 뺍니다.
            continue
        edges.append(edge.model_copy(update={"action": _tab_text(edge.action, tab_labels)}))
    view = graph.model_copy(update={"edges": edges})
    return view, _node_names(graph, tabs), tab_labels, hidden


# 뒤로 가기는 지나온 길에 따라 도착이 달라 합쳐짐 의심에서 뺍니다(화면의 "뒤로"와 키가 같음).
_BACK_ACTION_KEY = navigation_action_key_from_label("뒤로(키)")


def _append_merge_warning(lines: list[str], graph: NavigationGraph, names: dict[str, str]) -> None:
    """같은 화면의 같은 버튼이 서로 다른 화면으로 갔으면, 다른 화면이 한 노드로 합쳐졌을 수
    있다고 Overview에 적습니다(실측: 활동·친구 탭의 안내 팝업이 HOG 0.146으로 한 노드가 되어
    그 "확인"이 활동·친구로 갈림). 닫기류는 연 화면으로 돌아가는 정상일 수도 있어 판단은 사람이."""

    targets: dict[tuple[str, str], list[str]] = defaultdict(list)
    actions: dict[tuple[str, str], str] = {}
    for edge in graph.edges:
        key = edge.action_key or edge.action
        if edge.source == edge.target or key == _BACK_ACTION_KEY:
            continue
        if edge.target not in targets[(edge.source, key)]:
            targets[(edge.source, key)].append(edge.target)
        actions.setdefault((edge.source, key), edge.action)
    for (source, key), arrived in targets.items():
        if len(arrived) < 2:
            continue
        lines.append(
            f"- ⚠️ 합쳐짐 의심: **{names.get(source, source)}**의 '{actions[(source, key)]}' → "
            f"{', '.join(names.get(target, target) for target in arrived)}. 같은 버튼이 여러 화면으로 "
            "갔습니다. 서로 다른 화면이 한 노드로 합쳐졌을 수 있으니 이 화면에 붙은 캡처들을 "
            "비교하세요(닫기류는 연 화면으로 돌아가는 정상일 수도 있음). 합쳐진 것이면 "
            "`SCREEN_HOG_MAX_DISTANCE`를 내립니다."
        )


def _append_game_structure_diagram(
    lines: list[str],
    screen_types: set[str],
    graph: NavigationGraph | None,
    root_id: str | None,
    names: dict[str, str] | None = None,
    hidden_tab_edges: int = 0,
) -> None:
    """실제 그래프(BFS 트리 + 교차 링크) 또는 발견 화면 기반 구조도 추가.
    graph는 _structure_view의 구조도용 그래프, names는 노드 이름입니다."""

    names = names or {}
    actual_graph = graph if graph and graph.edges else None
    lines.extend([
        "## Game Structure Diagram",
        "",
        (
            "Autodrive가 관찰한 실제 화면 전환입니다. 실선은 루트에서 BFS로 만든 "
            "계층(트리), 점선은 트리에 들어가지 않은 교차 링크입니다."
            if actual_graph
            else "조사에서 확인된 화면입니다. 전환선은 Autodrive 실행 후 추가됩니다."
        ),
        "",
    ])
    if not screen_types and not actual_graph:
        lines.extend(["- No survey screens recorded yet.", ""])
        return

    ordered_screen_types = [
        screen_type for screen_type in SCREEN_TYPE_LABELS if screen_type in screen_types
    ]
    ordered_screen_types.extend(sorted(screen_types - set(ordered_screen_types)))

    node_count = len(graph.nodes) if actual_graph else 0
    if actual_graph and node_count > MERMAID_MAX_NODES:
        lines.extend([
            f"- 화면이 {node_count}개로 많아(기준 {MERMAID_MAX_NODES}개) mermaid "
            "다이어그램은 생략합니다. 아래 Screen Hierarchy 섹션을 참고하세요.",
            "",
        ])
        return

    lines.extend(["```mermaid", "flowchart LR"])
    graph_types = set()
    if actual_graph:
        for node in actual_graph.nodes.values():
            graph_types.add(node.screen_type)
            name = names.get(node.id) or _screen_label(node.screen_type)
            lines.append(f'    {_mermaid_node_id(node.id)}["{_mermaid_text(name)}"]')
    for screen_type in ordered_screen_types:
        if screen_type in graph_types:
            continue
        suffix = " (survey only)" if actual_graph else ""
        lines.append(
            f'    {_mermaid_node_id(screen_type)}["{_screen_label(screen_type)}{suffix}"]'
        )

    # 같은 화면으로 되돌아오는 간선(self-loop)은 다이어그램에서 노드마다 고리만
    # 늘려 구조를 가리므로 그리지 않고, Cross Links에 한 줄로 모아 둡니다.
    self_loop_count = 0
    drawn: set[str] = set()

    def _add_edge(edge: Any, arrow: str) -> None:
        nonlocal self_loop_count
        if edge.source == edge.target:
            self_loop_count += 1
            return
        # 간선 라벨은 항상 따옴표로 감쌉니다. "뒤로(키)"처럼 괄호가 들어간 라벨을
        # 따옴표 없이 쓰면 mermaid가 노드 모양 기호로 읽어 다이어그램 전체가 깨집니다.
        line = (
            f"    {_mermaid_node_id(edge.source)} "
            f'{arrow}|"{_mermaid_text(edge.action)}"| '
            f"{_mermaid_node_id(edge.target)}"
        )
        if line in drawn:
            return
        drawn.add(line)
        lines.append(line)

    if actual_graph and root_id:
        tree, cross_links = bfs_spanning_tree(graph, root_id)
        for edges in tree.values():
            for edge in edges:
                _add_edge(edge, "-->")
        for edge in cross_links:
            _add_edge(edge, "-.->")
    elif actual_graph:
        for edge in actual_graph.edges:
            _add_edge(edge, "-->")

    lines.append("```")
    lines.append("")
    if actual_graph:
        lines.append(
            "연결선은 실제 전환, `survey only`는 조사 기록에만 있는 화면입니다."
        )
        if self_loop_count:
            lines.append(
                f"눌러도 같은 화면으로 돌아온 간선 {self_loop_count}개는 그리지 "
                "않았습니다(아래 Cross Links 참고)."
            )
        if hidden_tab_edges:
            lines.append(
                f"탭 화면은 루트 아래 탭 순서대로 두고, 탭 화면끼리 오간 간선 {hidden_tab_edges}개는 "
                "그리지 않았습니다(탭바로 어느 탭에서나 서로 갈 수 있음)."
            )
    else:
        lines.append("현재 화면 간 전환 기록 없음.")
    lines.append("")


def _append_screen_hierarchy(
    lines: list[str],
    graph: NavigationGraph,
    root_id: str,
    base_path: Path,
    names: dict[str, str] | None = None,
) -> None:
    """루트에서 BFS로 만든 스패닝 트리를 들여쓰기 목록으로 펼칩니다(트리 본문).
    graph는 _structure_view의 구조도용 그래프, names는 노드 이름입니다."""

    names = names or {}
    tree, cross_links = bfs_spanning_tree(graph, root_id)

    lines.extend(["## Screen Hierarchy", "", "루트에서 BFS로 투영한 화면 계층입니다.", ""])

    def _describe(node_id: str) -> str:
        node = graph.nodes.get(node_id)
        if node is None:
            return node_id
        label = names.get(node_id) or _screen_label(node.screen_type)
        return f"**{label}** (`{node.screen_type}`, visits={node.visits})"

    def _walk(node_id: str, depth: int, via_action: str | None) -> None:
        indent = "  " * depth
        prefix = f"{indent}- "
        via = f" — via {via_action!r}" if via_action else ""
        lines.append(f"{prefix}{_describe(node_id)}{via}")
        node = graph.nodes.get(node_id)
        image = _relative_link(node.representative if node else None, base_path)
        if image:
            lines.append(f"{indent}  - Evidence: {image}")
        for edge in tree.get(node_id, []):
            _walk(edge.target, depth + 1, edge.action)

    _walk(root_id, 0, None)
    lines.append("")

    _append_orphan_screens(lines, graph, root_id, _describe, base_path)

    lines.extend(["### Cross Links (트리 밖 간선)", ""])
    # 같은 화면으로 돌아오는 간선은 "이 버튼을 눌렀지만 화면이 그대로였다"는 뜻
    # 하나뿐입니다. 간선마다 같은 화면 이름을 양쪽에 반복해 찍는 대신 화면별로
    # 버튼 이름만 모아 한 줄로 보여 줍니다.
    no_change_actions: dict[str, list[str]] = defaultdict(list)
    real_links = []
    for edge in cross_links:
        if edge.source == edge.target:
            action = edge.action or "unknown"
            if action not in no_change_actions[edge.source]:
                no_change_actions[edge.source].append(action)
        else:
            real_links.append(edge)

    if not cross_links:
        lines.append("- 없음 — 관찰된 모든 전환이 계층 안에 들어갑니다.")
    for screen_id_key, actions in sorted(no_change_actions.items()):
        lines.append(
            f"- {_describe(screen_id_key)}: 눌러도 화면이 그대로인 버튼 "
            f"{len(actions)}개 — {', '.join(actions)}"
        )
    for edge in real_links:
        lines.append(
            f"- {_describe(edge.source)} --({edge.action or 'unknown'})--> "
            f"{_describe(edge.target)}"
        )
    lines.append("")


def _append_orphan_screens(
    lines: list[str],
    graph: NavigationGraph,
    root_id: str,
    describe: Any,
    base_path: Path,
) -> None:
    """루트에서 오는 전환이 기록되지 않아 트리에 못 들어간 화면을 밝혀 둡니다.

    지금까지는 이 화면들이 계층에서 그냥 빠지기만 해서, Overview가 "파편화된
    화면을 Screen Hierarchy에서 비교하라"고 안내해도 정작 거기에 없었습니다.
    대표 이미지를 같이 걸어 두어야 그 비교를 실제로 할 수 있습니다."""

    orphans = unreached_from(graph, root_id)
    if not orphans:
        return
    lines.extend([
        "### 트리에 없는 화면 (루트에서 가는 길을 모름)",
        "",
        "관찰은 했지만 루트에서 오는 전환이 기록되지 않아 계층에 넣지 못했습니다. "
        "탐색이 중간에 끊겼거나 화면이 쪼개졌다는 신호입니다.",
        "",
    ])
    for node_id in orphans:
        lines.append(f"- {describe(node_id)}")
        node = graph.nodes.get(node_id)
        image = _relative_link(node.representative if node else None, base_path)
        if image:
            lines.append(f"  - Evidence: {image}")
    lines.append("")


def _append_frontier_section(
    lines: list[str],
    graph: NavigationGraph | None,
    ledger: LedgerStore | None,
    names: dict[str, str] | None = None,
    tab_labels: dict[int, str] | None = None,
) -> None:
    """원장이 있으면 커버리지, 미도달 프론티어, 포기(blocked) 항목을 보여줍니다."""

    names = names or {}
    tab_labels = tab_labels or {}

    lines.extend(["## Exploration Frontier", ""])
    if ledger is None:
        lines.extend(["- 원장 정보 없음(그래프 기반 탐색을 실행하면 채워집니다).", ""])
        return

    attempted, total = ledger.coverage()
    ratio = f"{attempted}/{total}" if total else "0/0"
    lines.append(
        f"- 탐색 커버리지: {ratio} ((화면,행동) 조합 기준, 눌러본 것/발견한 것, "
        f"누르지 않고 정리된 것 {ledger.closed_without_tap()}개)"
    )
    lines.append("")

    def _screen_label_for(screen_id: str) -> str:
        if screen_id == GLOBAL_SCREEN_ID:
            return "공통 버튼(네비게이션 바)"
        node = graph.nodes.get(screen_id) if graph else None
        return (names.get(screen_id) or _screen_label(node.screen_type)) if node else screen_id

    untried_by_screen: dict[str, list[str]] = defaultdict(list)
    blocked_by_screen: dict[str, list[str]] = defaultdict(list)
    hidden_by_screen: dict[str, list[str]] = defaultdict(list)
    not_found_by_screen: dict[str, list[str]] = defaultdict(list)
    play_entry_by_screen: dict[str, list[str]] = defaultdict(list)
    external_by_screen: dict[str, list[str]] = defaultdict(list)
    for entry in ledger.ledger.entries.values():
        label = _tab_text(entry.action_label, tab_labels) or entry.action_key
        if is_open_entry(entry):
            suffix = " (빗나감, 재시도 대기)" if entry.verdict == "missed" else ""
            untried_by_screen[entry.screen_id].append(label + suffix)
        elif entry.verdict == "blocked":
            blocked_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "not_visible":
            hidden_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "not_found":
            not_found_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "play_entry":
            play_entry_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "external":
            external_by_screen[entry.screen_id].append(label)

    sections = (
        ("### 아직 안 눌러본 프론티어", untried_by_screen,
         "- 없음 — 발견한 모든 (화면,행동)을 처리했습니다."),
        ("### 도달 실패로 포기함(blocked)", blocked_by_screen, "- 없음"),
        ("### 다시 봐도 후보에 안 나와 정리함(not_visible)", hidden_by_screen, "- 없음"),
        ("### 누르기 전 확대 확인에서 못 찾아 정리함(not_found)", not_found_by_screen, "- 없음"),
        ("### 플레이 진입이라 누르지 않음(play_entry)", play_entry_by_screen, "- 없음"),
        ("### 게임 밖·광고·뽑기라 누르지 않음(external)", external_by_screen, "- 없음"),
    )
    for heading, by_screen, empty_text in sections:
        lines.extend([heading, ""])
        if not by_screen:
            lines.append(empty_text)
        for screen_id_key, labels in sorted(by_screen.items()):
            lines.append(
                f"- **{_screen_label_for(screen_id_key)}**: {', '.join(sorted(labels))}"
            )
        lines.append("")


def _active_screen_map() -> ScreenMap | None:
    try:
        return active_profile().screen_map
    except Exception:  # noqa: BLE001 - 지식 파일 문제로 보고서를 못 쓰면 안 됩니다.
        return None


def _append_genre_map_section(
    lines: list[str],
    graph: NavigationGraph | None,
    screen_map: ScreenMap | None,
) -> None:
    """장르 화면 지도(knowledge/map_knowledge.json)와 실제 구조도를 화면 역할 단위로 대 봅니다.
    찾은 역할, 못 찾은 역할(흔한 것부터), 지도에 없는 전환(이상 간선 후보)을 적습니다."""

    if screen_map is None or graph is None or not graph.nodes:
        return
    lines.extend([
        "## Genre Map",
        "",
        "장르 화면 지도(`knowledge/map_knowledge.json`)와 실제 구조도를 화면 역할 단위로 대 봤습니다. "
        "역할은 조사 모델이 화면마다 고른 값(`screen_role`)이고, common/often/some은 지도에 적은 "
        "대략값(측정치 아님)입니다.",
        "",
    ])
    nodes_by_role: dict[str, list[Any]] = defaultdict(list)
    for node in graph.nodes.values():
        if screen_map.role(node.role) is not None:
            nodes_by_role[node.role].append(node)
    if not nodes_by_role:
        lines.extend(["- 역할이 기록된 화면이 없습니다(역할 기록 전에 만든 run이면 새 run부터 채워집니다).", ""])
        return

    def _name(role_id: str) -> str:
        return f"{screen_map.role(role_id).label_ko}(`{role_id}`)"

    found = set(nodes_by_role)
    reachable = screen_map.safe_reachable()
    labeled = sum(len(nodes) for nodes in nodes_by_role.values())
    lines.extend([
        f"- 역할을 붙인 화면 {labeled}/{len(graph.nodes)}개, 찾은 역할 {len(found)}개. "
        f"앱 시작·홈에서 플레이·결제·광고·게임 밖을 거치지 않고 갈 수 있는 역할 {len(reachable)}개 중 "
        f"{len(found & reachable)}개.",
        "",
        "### 찾은 역할",
        "",
    ])
    for role in screen_map.roles:
        nodes = nodes_by_role.get(role.id)
        if not nodes:
            continue
        off_types = sorted({node.screen_type for node in nodes} - set(role.screen_types))
        note = (
            f" — 화면 타입 {', '.join(off_types)}는 지도 후보({', '.join(role.screen_types)})에 없음"
            if off_types else ""
        )
        lines.append(
            f"- {_name(role.id)}, {role.prevalence}: 화면 {len(nodes)}개, "
            f"근거 분류 {', '.join(role.element_categories)}{note}"
        )

    lines.extend(["", "### 못 찾은 역할", ""])
    missing = [role for role in screen_map.roles if role.id not in found]
    for prevalence in PREVALENCE_ORDER:
        names = [_name(r.id) for r in missing if r.prevalence == prevalence and r.id in reachable]
        if names:
            lines.append(f"- {prevalence}: {', '.join(names)}")
    gated = [_name(r.id) for r in missing if r.id not in reachable]
    if gated:
        lines.append(f"- 플레이·결제·광고·게임 밖을 거쳐야 갈 수 있음(기본 동작으로는 안 감): {', '.join(gated)}")
    if not missing:
        lines.append("- 없음")

    lines.extend([
        "",
        "### 지도에 없는 전환",
        "",
        "역할 사이 전환 중 지도에 없는 것입니다. 역할을 잘못 붙였거나, 게임 밖(에뮬레이터 창 등)으로 "
        "나간 흔적이거나, 이 게임만의 구조일 수 있으니 대표 이미지로 확인하세요.",
        "",
    ])
    odd = []
    for edge in graph.edges:
        source, target = graph.nodes.get(edge.source), graph.nodes.get(edge.target)
        if source is None or target is None or source.role == target.role:
            continue
        if {source.role, target.role} <= found and not screen_map.expects(source.role, target.role):
            odd.append(
                f"- {_name(source.role)} --({edge.action or 'unknown'})--> {_name(target.role)} "
                f"(`{edge.source}` → `{edge.target}`)"
            )
    lines.extend(odd or ["- 없음"])
    lines.append("")


def _append_persistent_ui_section(
    lines: list[str],
    graph: NavigationGraph | None,
    ledger: LedgerStore | None,
    chrome: dict[str, Any] | None,
    names: dict[str, str] | None = None,
    tab_labels: dict[int, str] | None = None,
) -> None:
    """여러 화면에 공통으로 보이는 UI(네비게이션 바 등)와 공통 버튼을 보여줍니다."""

    names = names or {}
    tab_labels = tab_labels or {}
    if not chrome:
        return
    bands = chrome.get("bands") or {}
    global_keys = list(chrome.get("global_actions") or [])
    if not (bands.get("top") or bands.get("bottom") or global_keys):
        return

    lines.extend(["## Persistent UI", ""])
    lines.append(
        "여러 화면에 같은 모습·같은 자리로 보이는 UI입니다. 공통 버튼은 화면마다가 아니라 "
        "한 번만 눌러 보고, 그 결과는 버튼이 보이는 모든 화면에서 지름길로 씁니다."
    )
    lines.append("")
    if bands.get("top") or bands.get("bottom"):
        lines.append(
            f"- 공통 띠: 위 {bands.get('top', 0)}px, 아래 {bands.get('bottom', 0)}px "
            f"(화면 {bands.get('support', 0)}개가 공유, 화면 높이 {bands.get('height', 0)}px). "
            "같은 화면 판정에서는 이 영역을 빼고 비교합니다."
        )
    actions = chrome.get("actions") or {}
    for key in global_keys:
        entry = actions.get(key) or {}
        label = _tab_text(entry.get("label") or key, tab_labels)
        screens = len(entry.get("positions") or {})
        result = "아직 안 눌러 봄"
        if ledger is not None:
            ledger_entry = ledger.ledger.entries.get(f"{GLOBAL_SCREEN_ID}:{key}")
            # no_change로 닫힌 공통 버튼은 target이 자기 자신(__global__)이라 판정을 그대로 씁니다.
            target_id = ledger_entry.target_screen_id if ledger_entry is not None else None
            if target_id and target_id != GLOBAL_SCREEN_ID:
                target = graph.nodes.get(ledger_entry.target_screen_id) if graph else None
                target_label = (
                    names.get(target.id) or _screen_label(target.screen_type)
                    if target
                    else ledger_entry.target_screen_id
                )
                result = f"→ {target_label}"
            elif ledger_entry is not None and ledger_entry.verdict:
                result = ledger_entry.verdict
        lines.append(f"- 공통 버튼 **{label}**: {screens}개 화면에서 보임, {result}")
    lines.append("")


def _group_by_category(
    merged: dict[tuple[str, ...], dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """요소를 대표 분류별로 담고, 각 묶음을 표시 이름 사전순으로 둡니다.

    전에는 분류별로 따로 모아서 같은 요소가 분류 수만큼 실렸습니다. LLM은 같은
    요소를 매번 다른 분류에 넣기 때문입니다("레벨 번호"가 core_play·other·
    progression·ui 넷에 걸쳐 있었습니다). 이제 이름으로 먼저 묶고(그 묶음이
    elements_by_name), 분류는 결과에 붙는 표시로 내렸습니다.

    분류를 아예 버리지는 않습니다. 등급분류는 기준별로 봐야 하므로 어떤 분류의
    요소인지가 판단 재료입니다. 가장 자주 붙은 분류를 대표로 쓰고(같으면 사전순),
    나머지 분류는 항목 옆에 적어 분류가 흔들렸다는 사실 자체를 남깁니다."""

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for entry in merged.values():
        counts = entry["categories"]
        dominant = min(counts, key=lambda name: (-counts[name], name))
        grouped[dominant].append((_representative_name(entry["names"]), entry))
    for items in grouped.values():
        # 표시 이름으로 정렬합니다. 전에는 정규화한 이름으로 정렬하면서 표시는
        # 대표 이름으로 해서 목록이 사전순으로 보이지 않았습니다.
        items.sort(key=lambda item: item[0])
    return grouped


def _representative_name(names: Counter[str]) -> str:
    """묶인 표기 중 대표를 고릅니다: 많이 나온 것 → 짧은 것 → 사전순."""
    return min(names, key=lambda name: (-names[name], len(name), name))


def _element_line(name: str, entry: dict[str, Any], dominant: str, labels: dict[str, str]) -> str:
    """요소 한 줄: 이름, (분류가 흔들렸으면) 다른 분류, (2개 이상이면) 등장 화면."""

    line = f"- {name}"
    others = sorted(category for category in entry["categories"] if category != dominant)
    if others:
        line += f" (분류 중복: {', '.join(others)})"
    screens = sorted({labels.get(key, key) for key in entry["screens"]})
    if len(screens) > 1:
        line += f" — {len(screens)}개 화면: {', '.join(screens)}"
    return line


def _append_evidence_index(
    lines: list[str],
    records: list[dict[str, Any]],
    base_path: Path,
) -> None:
    """캡처를 화면 타입이 바뀔 때만 한 줄로 남깁니다.

    같은 화면을 연달아 관찰하면 한 글자도 다르지 않은 줄이 계속 쌓였습니다(상점
    5연속, 레벨 시작 6연속). 화면 타입이 바뀌는 지점만 남기면 탐색이 어떤 순서로
    흘러갔는지는 그대로 보이면서 분량은 크게 줄어듭니다. 세션이 바뀌면 step이 다시
    1부터 시작하므로 세션 경계에서도 끊습니다."""

    groups: list[dict[str, Any]] = []
    for record in records:
        survey = record.get("survey", {})
        screen_type = _display(survey.get("screen_type"))
        session = record.get("session_id")
        last = groups[-1] if groups else None
        if last and last["screen_type"] == screen_type and last["session"] == session:
            last["steps"].append(record.get("step"))
            continue
        groups.append({
            "screen_type": screen_type,
            "session": session,
            "steps": [record.get("step")],
            "image": _relative_link(record.get("raw_image"), base_path),
            "summary": _display(survey.get("summary"), ""),
        })

    for group in groups:
        steps = [step for step in group["steps"] if step is not None]
        if len(steps) > 1:
            step_text = f"Step {steps[0]}–{steps[-1]} ({len(steps)}건)"
        elif steps:
            step_text = f"Step {steps[0]}"
        else:
            step_text = "Step ?"
        lines.append(f"- {step_text}: {group['screen_type']}")
        if group["image"]:
            lines.append(f"  - Image: {group['image']}")
        if group["summary"]:
            lines.append(f"  - Summary: {group['summary']}")


def generate_survey_markdown(
    records: list[dict[str, Any]],
    graph: NavigationGraph | None = None,
    *,
    evidence_limit: int | None = SURVEY_REPORT_EVIDENCE_LIMIT,
    ledger: LedgerStore | None = None,
    chrome: dict[str, Any] | None = None,
    screen_map: ScreenMap | None = None,
) -> str:
    """전체 기록을 누적한 게임 구조 조사 보고서를 생성합니다 (Evidence Index만 최근 건수로 제한).
    screen_map을 주지 않으면 지금 게임(GAME_TITLE)의 장르 화면 지도로 대 봅니다."""

    lines = [
        "# Game Structure Survey",
        "",
        "이 문서는 `game_survey.jsonl`의 화면 캡처와 구조화된 조사 기록으로 생성되었습니다.",
        "",
        "## Overview",
        "",
        f"- Game: {_game_line()}",
        f"- Survey records: {len(records)}",
    ]
    unique_screen_signatures = {
        record.get("dedupe", {}).get("screen_signature")
        for record in records
        if record.get("dedupe", {}).get("screen_signature")
    }
    lines.append(f"- Unique screen signatures: {len(unique_screen_signatures)}")
    actual_graph = graph if graph and graph.edges else None
    root_id = resolve_root_screen_id(graph, ROOT_SCREEN_ID) if actual_graph else None
    view, names, tab_labels, hidden_tab_edges = (
        _structure_view(graph, root_id) if graph else (None, {}, {}, 0)
    )
    if graph and graph.nodes:
        # 0단계 진단용: 같은 타입에 노드가 많으면 화면이 쪼개지고 있다는 신호입니다.
        counts: dict[str, int] = defaultdict(int)
        for node in graph.nodes.values():
            counts[node.screen_type] += 1
        per_type = ", ".join(
            f"{screen_type} {count}" for screen_type, count in sorted(counts.items())
        )
        lines.append(f"- Navigation nodes: {len(graph.nodes)} (화면 타입별: {per_type})")
        _append_fragmentation_warning(lines, graph)
        _append_merge_warning(lines, graph, names)
    lines.append("")

    observed_screen_types = {
        _display(record.get("survey", {}).get("screen_type")) for record in records
    }

    _append_game_structure_diagram(
        lines, observed_screen_types, view, root_id, names, hidden_tab_edges
    )

    if actual_graph and root_id:
        _append_screen_hierarchy(lines, view, root_id, SURVEY_REPORT_PATH.parent, names)

    _append_persistent_ui_section(lines, graph, ledger, chrome, names, tab_labels)
    _append_frontier_section(lines, graph, ledger, names, tab_labels)
    _append_genre_map_section(lines, graph, screen_map or _active_screen_map())

    screen_examples: dict[str, dict[str, Any]] = {}
    # 3단계: 요소 하나가 여러 화면에 나올 수 있으니 첫 등장만 남기지 않고
    # "어느 화면들에 나왔는지" 집합으로 전부 모읍니다(동시출현).
    #
    # 저장된 key 대신 이름에서 키를 다시 계산해 묶습니다. 키 만드는 방식이 바뀌어도
    # 옛 기록과 새 기록이 같은 요소로 모이고, 표기만 다른 이름도 한 항목이 됩니다.
    # 분류는 묶는 기준이 아니라 결과에 붙는 표시입니다(_merge_elements 설명 참고).
    elements_by_name: dict[tuple[str, ...], dict[str, Any]] = {}
    buttons_by_role: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for record in records:
        survey = record.get("survey", {})
        screen_type = _display(survey.get("screen_type"))
        screen_examples.setdefault(screen_type, record)
        screen_key = _screen_key_for_record(record)

        for element in record.get("elements", []):
            name = str(element.get("name") or "")
            tokens = canonical_name_tokens(name)
            if not tokens:
                continue
            item = elements_by_name.get(tokens)
            if item is None:
                item = {"names": Counter(), "screens": set(), "categories": Counter()}
                elements_by_name[tokens] = item
            item["names"][name] += 1
            item["categories"][element.get("category", "other")] += 1
            item["screens"].add(screen_key)

        for button in record.get("buttons", []):
            key = button.get("key")
            if not key:
                continue
            buttons_by_role[button.get("role", "unknown")].setdefault(
                key,
                {
                    "label": button.get("label", ""),
                    "confidence": button.get("confidence", 0.0),
                    "reason": button.get("reason", ""),
                    "raw_image": record.get("raw_image"),
                },
            )

    # 화면 키(그래프 노드 ID 또는 signature/type 폴백) -> 사람이 읽을 라벨.
    screen_label_by_key: dict[str, str] = {}
    if graph:
        for node in graph.nodes.values():
            screen_label_by_key[node.id] = names.get(node.id) or _screen_label(node.screen_type)
    for record in records:
        screen_label_by_key.setdefault(
            _screen_key_for_record(record),
            _display(record.get("survey", {}).get("screen_type")),
        )

    lines.extend(["## Screen Types", ""])
    if not screen_examples:
        lines.append("- No survey screens recorded yet.")
    for screen_type, record in sorted(screen_examples.items()):
        survey = record.get("survey", {})
        image = _relative_link(record.get("raw_image"), SURVEY_REPORT_PATH.parent)
        lines.append(f"- **{screen_type}**: {_display(survey.get('summary'), '')}")
        if image:
            lines.append(f"  - Evidence: {image}")
    lines.append("")

    lines.extend([
        "## Game Elements",
        "",
        "이름이 같으면 분류가 달라도 한 항목으로 묶었습니다. 대표 이름은 가장 자주 나온 "
        "표기, 대표 분류는 가장 자주 붙은 분류이고, 다른 분류에도 기록됐으면 옆에 "
        "적었습니다. 등장 화면은 두 화면 이상일 때만 적습니다.",
        "",
    ])
    if not elements_by_name:
        lines.append("- No game elements recorded yet.")
        lines.append("")
    grouped = _group_by_category(elements_by_name)
    for category, items in sorted(grouped.items()):
        lines.append(f"### {category}")
        for name, entry in items:
            lines.append(_element_line(name, entry, category, screen_label_by_key))
        lines.append("")

    lines.extend(["## Button And Navigation Candidates", ""])
    if not buttons_by_role:
        lines.append("- No button candidates recorded yet.")
        lines.append("")
    for role, items in sorted(buttons_by_role.items()):
        lines.append(f"### {role}")
        for item in items.values():
            confidence = float(item.get("confidence", 0.0) or 0.0)
            reason = _display(item.get("reason"), "")
            suffix = f" - {reason}" if reason else ""
            lines.append(
                f"- {_display(item.get('label'))} "
                f"(confidence={confidence:.2f}){suffix}"
            )
        lines.append("")

    lines.extend([
        "## Evidence Index",
        "",
        "화면 타입이 바뀌는 지점만 남겼습니다(같은 화면 연속 관찰은 건수로 표시).",
        "",
    ])
    evidence_records = (
        records[-evidence_limit:] if evidence_limit is not None else records
    )
    if len(evidence_records) < len(records):
        lines.extend([
            f"- 전체 {len(records)}건 중 최근 {len(evidence_records)}건만 "
            "표시합니다. 위 목록은 전체 기록 기준입니다.",
            "",
        ])
    _append_evidence_index(lines, evidence_records, SURVEY_REPORT_PATH.parent)

    return "\n".join(lines).rstrip() + "\n"


def write_survey_report(
    records: list[dict[str, Any]],
    output_path: Path = SURVEY_REPORT_PATH,
    graph: NavigationGraph | None = None,
    ledger: LedgerStore | None = None,
) -> Path:
    """보고서 Markdown을 생성해 파일로 저장합니다.

    graph/ledger를 안 넘기면 이번 run의 저장 경로(config.NAVIGATION_GRAPH_PATH /
    NAVIGATION_LEDGER_PATH — 1단계에서 게임·run별로 분리된 경로)에서 자동으로
    불러옵니다. `--survey-report`처럼 그래프 없이 단독 호출될 때를 위한 것입니다."""
    if graph is None and CURRENT_RUN_GRAPH_PATH.exists():
        graph = NavigationGraph.model_validate_json(
            CURRENT_RUN_GRAPH_PATH.read_text(encoding="utf-8")
        )
    if ledger is None and CURRENT_RUN_LEDGER_PATH.exists():
        ledger = LedgerStore(CURRENT_RUN_LEDGER_PATH)
    chrome = None
    if CURRENT_RUN_UI_CHROME_PATH.exists():
        try:
            chrome = json.loads(CURRENT_RUN_UI_CHROME_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            chrome = None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        generate_survey_markdown(records, graph, ledger=ledger, chrome=chrome),
        encoding="utf-8",
    )
    return output_path
