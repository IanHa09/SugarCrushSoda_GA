"""조사 로그를 사람이 읽을 수 있는 Markdown 문서로 변환합니다."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from config import SURVEY_REPORT_PATH
from survey_utils import RATING_SIGNAL_FIELDS


def _display(value: str | None, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    stripped = str(value).strip()
    return stripped if stripped else fallback


def _relative_link(path: str | None, base: Path) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path


def generate_survey_markdown(records: list[dict[str, Any]]) -> str:
    """중복 요소를 접어 게임 구조 조사 보고서를 만듭니다."""

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
                    "description": element.get("description", ""),
                    "evidence_text": element.get("evidence_text", ""),
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
            detail = _display(item.get("description"), "")
            evidence = _display(item.get("evidence_text"), "")
            suffix = f" - {detail}" if detail else ""
            lines.append(f"- {_display(item.get('name'))}{suffix}")
            if evidence:
                lines.append(f"  - Evidence text: {evidence}")
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

    lines.extend(["## Rating Signal Variables", ""])
    for field in RATING_SIGNAL_FIELDS:
        lines.append(f"- `{field}`: unknown")
    lines.append("")

    lines.extend(["## Evidence Index", ""])
    for record in records:
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
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_survey_markdown(records), encoding="utf-8")
    return output_path
