"""조사 로그를 읽기 쉬운 Markdown으로 변환."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from config import SURVEY_REPORT_EVIDENCE_LIMIT, SURVEY_REPORT_PATH
from schemas import NavigationGraph


NAVIGATION_GRAPH_PATH = Path("output/navigation/graph.json")


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


def _relative_link(path: str | None, base: Path) -> str:
    """기준 경로 대비 상대 경로 문자열을 계산합니다."""
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def _mermaid_node_id(screen_type: str) -> str:
    """화면 타입 문자열을 mermaid 노드 ID로 변환합니다."""
    safe_name = "".join(
        character if character.isalnum() else "_"
        for character in screen_type
    ).strip("_")
    return f"screen_{safe_name or 'unknown'}"


def _mermaid_text(value: str) -> str:
    return value.replace('"', "'").replace("|", "/")


def _append_game_structure_diagram(
    lines: list[str],
    screen_types: set[str],
    graph: NavigationGraph | None = None,
) -> None:
    """실제 그래프 또는 발견 화면 기반 구조도 추가."""

    actual_graph = graph if graph and graph.edges else None
    lines.extend([
        "## Game Structure Diagram",
        "",
        (
            "Autodrive가 관찰한 실제 화면 전환입니다."
            if actual_graph
            else "조사에서 확인된 화면입니다. 전환선은 Autodrive 실행 후 추가됩니다."
        ),
        "",
    ])
    if not screen_types and not actual_graph:
        lines.extend(["- No survey screens recorded yet.", ""])
        return

    ordered_screen_types = [
        screen_type
        for screen_type in SCREEN_TYPE_LABELS
        if screen_type in screen_types
    ]
    ordered_screen_types.extend(sorted(screen_types - set(ordered_screen_types)))

    lines.extend(["```mermaid", "flowchart LR"])
    graph_types = set()
    if actual_graph:
        for node in actual_graph.nodes.values():
            graph_types.add(node.screen_type)
            label = SCREEN_TYPE_LABELS.get(
                node.screen_type,
                node.screen_type.replace("_", " ").title(),
            )
            lines.append(
                f'    {_mermaid_node_id(node.id)}["{_mermaid_text(label)}"]'
            )
    for screen_type in ordered_screen_types:
        if screen_type in graph_types:
            continue
        label = SCREEN_TYPE_LABELS.get(
            screen_type,
            screen_type.replace("_", " ").title(),
        )
        suffix = " (survey only)" if actual_graph else ""
        lines.append(
            f'    {_mermaid_node_id(screen_type)}["{label}{suffix}"]'
        )

    if actual_graph:
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


def generate_survey_markdown(
    records: list[dict[str, Any]],
    graph: NavigationGraph | None = None,
    *,
    evidence_limit: int | None = SURVEY_REPORT_EVIDENCE_LIMIT,
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
    lines.append("")

    observed_screen_types = {
        _display(record.get("survey", {}).get("screen_type"))
        for record in records
    }
    _append_game_structure_diagram(lines, observed_screen_types, graph)

    screen_examples: dict[str, dict[str, Any]] = {}
    elements_by_category: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    buttons_by_role: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for record in records:
        survey = record.get("survey", {})
        screen_type = _display(survey.get("screen_type"))
        screen_examples.setdefault(screen_type, record)

        for element in record.get("elements", []):
            key = element.get("key")
            if not key:
                continue
            elements_by_category[element.get("category", "other")].setdefault(
                key,
                {
                    "name": element.get("name", ""),
                    # 옛 필드(description/evidence_text)로 저장된 기록도 있어 폴백합니다.
                    "evidence": (
                        element.get("evidence")
                        or element.get("evidence_text")
                        or element.get("description")
                        or ""
                    ),
                    "raw_image": record.get("raw_image"),
                },
            )

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

    lines.extend(["## Game Elements", ""])
    if not elements_by_category:
        lines.append("- No game elements recorded yet.")
        lines.append("")
    for category, items in sorted(elements_by_category.items()):
        lines.append(f"### {category}")
        for item in items.values():
            evidence = _display(item.get("evidence"), "")
            lines.append(f"- {_display(item.get('name'))}")
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
) -> Path:
    """보고서 Markdown을 생성해 파일로 저장합니다."""
    if graph is None and NAVIGATION_GRAPH_PATH.exists():
        graph = NavigationGraph.model_validate_json(
            NAVIGATION_GRAPH_PATH.read_text(encoding="utf-8")
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        generate_survey_markdown(records, graph),
        encoding="utf-8",
    )
    return output_path
