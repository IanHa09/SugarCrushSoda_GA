"""조사 로그를 읽기 쉬운 Markdown으로 변환.

3단계: 게임 UI에는 사이클이 있어 그래프 자체는 트리가 아니므로, 그래프를 진실의
원천으로 두고 루트에서 BFS로 만든 스패닝 트리를 "화면 계층"으로 보여줍니다(BFS라서
트리 깊이 = 루트에서 몇 번 눌러야 도달하는지와 같습니다). 트리에 못 들어간 간선은
"교차 링크"로 따로 보여주고(머메이드에서는 점선), 원장이 있으면 "아직 못 눌러본
프론티어"와 "도달 실패로 포기한 항목"도 커버리지 근거로 보여줍니다.

옛 generate_survey_markdown은 같은 요소가 여러 화면에 나와도 setdefault로 첫 등장만
남기고 나머지를 버렸습니다. 이번에 "요소가 어느 화면들에 나왔는지"(동시출현)를 전부
모으도록 고쳤습니다 — 자산 자체가 아니라 자산 간 상호작용을 보려는 목적에 맞습니다.
"""

from __future__ import annotations

from collections import defaultdict
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
from navigation.graph import (
    bfs_spanning_tree,
    fragmented_screen_types,
    resolve_root_screen_id,
)
from navigation.ledger import LedgerStore, is_open_entry
from schemas import NavigationGraph


SCREEN_TYPE_LABELS = {
    "playing_board": "Playing Board",
    "level_start": "Level Start",
    "level_complete": "Level Complete",
    "reward_popup": "Reward Popup",
    "map_or_level_select": "Map / Level Select",
    "booster_panel": "Booster Panel",
    "shop_or_currency": "Shop / Currency",
    "settings_menu": "Settings",
    "event_or_mission": "Event / Mission",
    "ad_or_offer": "Ad / Offer",
    "tutorial": "Tutorial",
    "loading": "Loading",
    "unknown_but_recordable": "Unknown Screen",
}


def _display(value: str | None, fallback: str = "unknown") -> str:
    """값이 비어 있으면 대체 문자열을 반환합니다."""
    if value is None:
        return fallback
    stripped = str(value).strip()
    return stripped if stripped else fallback


def _screen_label(screen_type: str) -> str:
    return SCREEN_TYPE_LABELS.get(screen_type, screen_type.replace("_", " ").title())


def _relative_link(path: str | None, base: Path) -> str:
    """기준 경로 대비 상대 경로 문자열을 계산합니다."""
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def _mermaid_node_id(raw_id: str) -> str:
    """임의 문자열을 mermaid 노드 ID로 변환합니다."""
    safe_name = "".join(
        character if character.isalnum() else "_" for character in raw_id
    ).strip("_")
    return f"screen_{safe_name or 'unknown'}"


def _mermaid_text(value: str) -> str:
    return value.replace('"', "'").replace("|", "/")


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


def _append_game_structure_diagram(
    lines: list[str],
    screen_types: set[str],
    graph: NavigationGraph | None,
    root_id: str | None,
) -> None:
    """실제 그래프(BFS 트리 + 교차 링크) 또는 발견 화면 기반 구조도 추가."""

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
            lines.append(
                f'    {_mermaid_node_id(node.id)}["{_mermaid_text(_screen_label(node.screen_type))}"]'
            )
    for screen_type in ordered_screen_types:
        if screen_type in graph_types:
            continue
        suffix = " (survey only)" if actual_graph else ""
        lines.append(
            f'    {_mermaid_node_id(screen_type)}["{_screen_label(screen_type)}{suffix}"]'
        )

    if actual_graph and root_id:
        tree, cross_links = bfs_spanning_tree(graph, root_id)
        for edges in tree.values():
            for edge in edges:
                lines.append(
                    f"    {_mermaid_node_id(edge.source)} "
                    f"-->|{_mermaid_text(edge.action)}| "
                    f"{_mermaid_node_id(edge.target)}"
                )
        for edge in cross_links:
            lines.append(
                f"    {_mermaid_node_id(edge.source)} "
                f"-.->|{_mermaid_text(edge.action)}| "
                f"{_mermaid_node_id(edge.target)}"
            )
    elif actual_graph:
        for edge in actual_graph.edges:
            lines.append(
                f"    {_mermaid_node_id(edge.source)} "
                f"-->|{_mermaid_text(edge.action)}| "
                f"{_mermaid_node_id(edge.target)}"
            )

    lines.extend([
        "```",
        "",
        (
            "연결선은 실제 전환, `survey only`는 조사 기록에만 있는 화면입니다."
            if actual_graph
            else "현재 화면 간 전환 기록 없음."
        ),
        "",
    ])


def _append_screen_hierarchy(
    lines: list[str],
    graph: NavigationGraph,
    root_id: str,
    base_path: Path,
) -> None:
    """루트에서 BFS로 만든 스패닝 트리를 들여쓰기 목록으로 펼칩니다(트리 본문)."""

    tree, cross_links = bfs_spanning_tree(graph, root_id)

    lines.extend(["## Screen Hierarchy", "", "루트에서 BFS로 투영한 화면 계층입니다.", ""])

    def _describe(node_id: str) -> str:
        node = graph.nodes.get(node_id)
        if node is None:
            return node_id
        label = _screen_label(node.screen_type)
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

    lines.extend(["### Cross Links (트리 밖 간선)", ""])
    if not cross_links:
        lines.append("- 없음 — 관찰된 모든 전환이 계층 안에 들어갑니다.")
    for edge in cross_links:
        lines.append(
            f"- {_describe(edge.source)} --({edge.action or 'unknown'})--> "
            f"{_describe(edge.target)}"
        )
    lines.append("")


def _append_frontier_section(lines: list[str], graph: NavigationGraph | None, ledger: LedgerStore | None) -> None:
    """원장이 있으면 커버리지, 미도달 프론티어, 포기(blocked) 항목을 보여줍니다."""

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
        node = graph.nodes.get(screen_id) if graph else None
        return _screen_label(node.screen_type) if node else screen_id

    untried_by_screen: dict[str, list[str]] = defaultdict(list)
    blocked_by_screen: dict[str, list[str]] = defaultdict(list)
    hidden_by_screen: dict[str, list[str]] = defaultdict(list)
    for entry in ledger.ledger.entries.values():
        label = entry.action_label or entry.action_key
        if is_open_entry(entry):
            untried_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "blocked":
            blocked_by_screen[entry.screen_id].append(label)
        elif entry.verdict == "not_visible":
            hidden_by_screen[entry.screen_id].append(label)

    sections = (
        ("### 아직 안 눌러본 프론티어", untried_by_screen,
         "- 없음 — 발견한 모든 (화면,행동)을 처리했습니다."),
        ("### 도달 실패로 포기함(blocked)", blocked_by_screen, "- 없음"),
        ("### 다시 봐도 후보에 안 나와 정리함(not_visible)", hidden_by_screen, "- 없음"),
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


def generate_survey_markdown(
    records: list[dict[str, Any]],
    graph: NavigationGraph | None = None,
    *,
    evidence_limit: int | None = SURVEY_REPORT_EVIDENCE_LIMIT,
    ledger: LedgerStore | None = None,
) -> str:
    """전체 기록을 누적한 게임 구조 조사 보고서를 생성합니다 (Evidence Index만 최근 건수로 제한)."""

    lines = [
        "# Game Structure Survey",
        "",
        "이 문서는 `game_survey.jsonl`의 화면 캡처와 구조화된 조사 기록으로 생성되었습니다.",
        "",
        "## Overview",
        "",
        f"- Survey records: {len(records)}",
    ]
    unique_screen_signatures = {
        record.get("dedupe", {}).get("screen_signature")
        for record in records
        if record.get("dedupe", {}).get("screen_signature")
    }
    lines.append(f"- Unique screen signatures: {len(unique_screen_signatures)}")
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
    lines.append("")

    observed_screen_types = {
        _display(record.get("survey", {}).get("screen_type")) for record in records
    }

    actual_graph = graph if graph and graph.edges else None
    root_id = resolve_root_screen_id(graph, ROOT_SCREEN_ID) if actual_graph else None

    _append_game_structure_diagram(lines, observed_screen_types, graph, root_id)

    if actual_graph and root_id:
        _append_screen_hierarchy(lines, graph, root_id, SURVEY_REPORT_PATH.parent)

    _append_frontier_section(lines, graph, ledger)

    screen_examples: dict[str, dict[str, Any]] = {}
    # 3단계: 요소 하나가 여러 화면에 나올 수 있으니 첫 등장만 남기지 않고
    # "어느 화면들에 나왔는지" 집합으로 전부 모읍니다(동시출현).
    elements_by_category: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    buttons_by_role: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for record in records:
        survey = record.get("survey", {})
        screen_type = _display(survey.get("screen_type"))
        screen_examples.setdefault(screen_type, record)
        screen_key = _screen_key_for_record(record)

        for element in record.get("elements", []):
            key = element.get("key")
            if not key:
                continue
            bucket = elements_by_category[element.get("category", "other")]
            item = bucket.get(key)
            if item is None:
                item = {
                    "name": element.get("name", ""),
                    # 옛 필드(description/evidence_text)로 저장된 기록도 있어 폴백합니다.
                    "evidence": (
                        element.get("evidence")
                        or element.get("evidence_text")
                        or element.get("description")
                        or ""
                    ),
                    "raw_image": record.get("raw_image"),
                    "screens": set(),
                }
                bucket[key] = item
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
            screen_label_by_key[node.id] = _screen_label(node.screen_type)
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

    lines.extend(["## Game Elements (화면 동시출현 포함)", ""])
    if not elements_by_category:
        lines.append("- No game elements recorded yet.")
        lines.append("")
    for category, items in sorted(elements_by_category.items()):
        lines.append(f"### {category}")
        for item in items.values():
            evidence = _display(item.get("evidence"), "")
            screens = sorted(
                screen_label_by_key.get(key, key) for key in item.get("screens", ())
            )
            screen_suffix = (
                f" — 등장 화면({len(screens)}): {', '.join(screens)}" if screens else ""
            )
            lines.append(f"- {_display(item.get('name'))}{screen_suffix}")
            if evidence:
                lines.append(f"  - Evidence: {evidence}")
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

    lines.extend(["## Evidence Index", ""])
    evidence_records = (
        records[-evidence_limit:] if evidence_limit is not None else records
    )
    if len(evidence_records) < len(records):
        lines.extend([
            f"- 전체 {len(records)}건 중 최근 {len(evidence_records)}건만 "
            "표시합니다. 위 목록은 전체 기록 기준입니다.",
            "",
        ])
    for record in evidence_records:
        survey = record.get("survey", {})
        image = _relative_link(record.get("raw_image"), SURVEY_REPORT_PATH.parent)
        duplicate = record.get("dedupe", {}).get("is_duplicate_screen", False)
        duplicate_text = " duplicate-screen" if duplicate else ""
        lines.append(
            f"- Step {record.get('step')}: "
            f"{_display(survey.get('screen_type'))}{duplicate_text}"
        )
        if image:
            lines.append(f"  - Image: {image}")
        summary = _display(survey.get("summary"), "")
        if summary:
            lines.append(f"  - Summary: {summary}")

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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        generate_survey_markdown(records, graph, ledger=ledger),
        encoding="utf-8",
    )
    return output_path
