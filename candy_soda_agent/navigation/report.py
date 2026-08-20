from pathlib import Path

from navigation.schemas import NavigationGraph


REPORT_PATH = Path("output/navigation/game_structure.md")


def _safe(value: str) -> str:
    return value.replace('"', "'").replace("|", "/")


def write_report(graph: NavigationGraph) -> Path:
    lines = [
        "# Game Structure",
        "",
        "```mermaid",
        "flowchart TD",
    ]

    for node in graph.nodes.values():
        label = _safe(f"{node.screen_type}: {node.summary}")
        lines.append(f'    {node.id}["{label}"]')

    for edge in graph.edges:
        lines.append(
            f"    {edge.source} -->|{_safe(edge.action)}| {edge.target}"
        )

    lines.extend(["```", "", "## Representative Screens", ""])

    for node in graph.nodes.values():
        image = Path(node.representative).name
        lines.extend([
            f"### {node.screen_type}",
            "",
            node.summary,
            "",
            f"![{node.screen_type}](representatives/{image})",
            "",
        ])

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH