"""run 하나의 탐색 품질을 세션(=autodrive 한 번 실행)별로 셉니다.

0단계(기준선)와 5단계(40스텝 반복 실험)에 씁니다. 세션을 거듭할수록
  - 화면을 실제로 바꾼 탭 비율(적중률)이 높고,
  - 누적 화면 수 / 누적 전환 수가 늘다가 평평해지면(수렴)
그래프가 안정적으로 쌓이고 있다는 뜻입니다.

탭이 화면을 바꿨는지는 이렇게 정합니다.
  1. 기록에 tap_effect(탭 뒤 픽셀 변화)가 있으면 그대로 씁니다(새 코드로 돌린 run).
  2. 없으면(옛 run) 다음 캡처 이미지를 찾아 픽셀 차이를 직접 계산합니다.
  3. 이미지도 없으면 다음 기록의 screen_id가 바뀌었는지로 추정합니다.

사용 예 (저장소 루트에서):
    python candy_soda_agent/survey_metrics.py
    python candy_soda_agent/survey_metrics.py --run run_2026-09-21_10-51-14

결과: <run 폴더>/metrics.md, metrics.csv
config.py를 import하지 않으므로(새 run 포인터를 만들지 않음) 아무 때나 돌려도 됩니다.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GAME_ROOT = REPO_ROOT / "output" / "candy_crush_soda"
# config.SAME_SCREEN_PIXEL_THRESHOLD와 같은 값입니다(config를 import하지 않으려고 복사).
DEFAULT_CHANGE_THRESHOLD = 0.01
TAP_ACTION_TYPES = {"tap_button", "back_key"}


@dataclass
class SessionMetrics:
    """세션 하나의 지표."""

    session: int
    session_id: str
    steps: int = 0
    taps: int = 0
    taps_changed: int = 0
    taps_unchanged: int = 0
    taps_unknown: int = 0
    hit_rate: float | None = None
    change_source: str = ""
    refine_used: int = 0
    refine_found: int = 0
    refine_not_found: int = 0
    refine_errors: int = 0
    refine_median_shift_px: float | None = None
    back_keys: int = 0
    new_screens: int = 0
    cum_screens: int = 0
    new_transitions: int = 0
    cum_transitions: int = 0
    stop: str = ""
    _sources: Counter = field(default_factory=Counter, repr=False)

    def row(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_sources", None)
        return data


def _resolve_image(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(str(raw_path).replace("\\", "/"))
    for path in (candidate, REPO_ROOT / candidate):
        if path.exists():
            return path
    return None


def _load_image(raw_path: str | None) -> np.ndarray | None:
    path = _resolve_image(raw_path)
    return cv2.imread(str(path)) if path else None


def frame_difference(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """capture.frame_difference와 같은 계산(mss import 없이 쓰려고 복사)."""
    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)
    small_a = cv2.resize(gray_a, (160, 160), interpolation=cv2.INTER_AREA)
    small_b = cv2.resize(gray_b, (160, 160), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(small_a, small_b))) / 255.0


def _tap_label(action: dict) -> str:
    button = action.get("button") or {}
    return str(button.get("label", ""))


def _split_sessions(records: list[dict]) -> list[list[dict]]:
    sessions: dict[str, list[dict]] = {}
    for record in records:
        sessions.setdefault(str(record.get("session_id", "")), []).append(record)
    return sorted(sessions.values(), key=lambda items: str(items[0].get("timestamp", "")))


def _stop_label(last: dict) -> str:
    action = last.get("action") or {}
    outcome = action.get("outcome", "")
    if action.get("type") == "record_only" and outcome in {
        "frontier_converged",
        "frontier_unreachable",
    }:
        return outcome.replace("frontier_", "")
    return f"other({outcome})" if outcome else "other"


def compute_session_metrics(
    records: list[dict],
    *,
    image_loader: Callable[[str | None], np.ndarray | None] | None = _load_image,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> list[SessionMetrics]:
    """조사 기록(game_survey.jsonl)을 세션별 지표로 바꿉니다(autodrive 기록만)."""

    autodrive = [r for r in records if r.get("mode") == "autodrive"]
    seen_screens: set[str] = set()
    seen_transitions: set[tuple[str, str, str]] = set()
    results: list[SessionMetrics] = []

    for index, session_records in enumerate(_split_sessions(autodrive), start=1):
        metrics = SessionMetrics(session=index, session_id=str(session_records[0].get("session_id", "")))
        metrics.steps = len(session_records)
        shifts: list[float] = []
        screens_before = len(seen_screens)
        transitions_before = len(seen_transitions)

        for position, record in enumerate(session_records):
            screen = record.get("screen_id")
            if screen:
                seen_screens.add(screen)
            action = record.get("action") or {}
            refine = action.get("refine")
            if refine:
                metrics.refine_used += 1
                if refine.get("error"):
                    metrics.refine_errors += 1
                elif refine.get("found"):
                    metrics.refine_found += 1
                    if refine.get("shift_px") is not None:
                        shifts.append(float(refine["shift_px"]))
                else:
                    metrics.refine_not_found += 1

            if action.get("type") not in TAP_ACTION_TYPES or not action.get("executed"):
                continue
            metrics.taps += 1
            if action.get("type") == "back_key":
                metrics.back_keys += 1

            following = session_records[position + 1] if position + 1 < len(session_records) else None
            changed: bool | None = None
            effect = action.get("tap_effect")
            if effect and "changed" in effect:
                changed = bool(effect["changed"])
                metrics._sources["tap_effect"] += 1
            elif following is not None:
                before = image_loader(record.get("raw_image")) if image_loader else None
                after = image_loader(following.get("raw_image")) if image_loader else None
                if before is not None and after is not None and before.shape == after.shape:
                    changed = frame_difference(before, after) >= change_threshold
                    metrics._sources["pixels"] += 1
                else:
                    changed = following.get("screen_id") != screen
                    metrics._sources["screen_id"] += 1

            if changed is None:
                metrics.taps_unknown += 1
                continue
            if not changed:
                metrics.taps_unchanged += 1
                continue
            metrics.taps_changed += 1
            if following is not None:
                target = following.get("screen_id")
                if screen and target and target != screen:
                    seen_transitions.add((screen, target, _tap_label(action)))

        judged = metrics.taps_changed + metrics.taps_unchanged
        metrics.hit_rate = round(metrics.taps_changed / judged, 3) if judged else None
        metrics.change_source = "+".join(sorted(metrics._sources)) or "-"
        metrics.refine_median_shift_px = round(statistics.median(shifts), 1) if shifts else None
        metrics.new_screens = len(seen_screens) - screens_before
        metrics.cum_screens = len(seen_screens)
        metrics.new_transitions = len(seen_transitions) - transitions_before
        metrics.cum_transitions = len(seen_transitions)
        metrics.stop = _stop_label(session_records[-1])
        results.append(metrics)
    return results


def graph_summary(graph: dict | None, ledger: dict | None) -> dict[str, Any]:
    """graph.json / ledger.json 원본 dict로 그래프 상태를 요약합니다."""

    summary: dict[str, Any] = {}
    if graph:
        nodes = graph.get("nodes", {}) or {}
        edges = graph.get("edges", []) or []
        self_loops = [e for e in edges if e.get("source") == e.get("target")]
        summary["nodes"] = len(nodes)
        summary["edges"] = len(edges) - len(self_loops)
        summary["self_loop_edges"] = len(self_loops)
        summary["screen_types"] = dict(Counter(n.get("screen_type", "?") for n in nodes.values()))
        if nodes:
            root = next(iter(nodes))
            adjacency: dict[str, list[str]] = {}
            for edge in edges:
                adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))
            reached, queue = {root}, deque([root])
            while queue:
                for target in adjacency.get(queue.popleft(), []):
                    if target not in reached:
                        reached.add(target)
                        queue.append(target)
            summary["unreached_from_root"] = len(nodes) - len(reached)
    if ledger:
        entries = (ledger.get("entries") or {}).values()
        total = len(entries)
        attempted = sum(1 for e in entries if e.get("attempts", 0) > 0)
        verdicts = Counter(e.get("verdict") or "open" for e in entries)
        summary["coverage"] = f"{attempted}/{total}"
        summary["verdicts"] = dict(verdicts)
    return summary


COLUMNS = [
    ("session", "세션"),
    ("steps", "스텝"),
    ("taps", "탭"),
    ("taps_changed", "화면 바뀜"),
    ("taps_unchanged", "그대로"),
    ("hit_rate", "적중률"),
    ("refine_found", "재확인 찾음"),
    ("refine_not_found", "재확인 못찾음"),
    ("refine_median_shift_px", "보정 중앙값(px)"),
    ("back_keys", "뒤로키"),
    ("new_screens", "새 화면"),
    ("cum_screens", "누적 화면"),
    ("new_transitions", "새 전환"),
    ("cum_transitions", "누적 전환"),
    ("stop", "종료"),
]


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0%}" if value <= 1 else f"{value:g}"
    return str(value)


def render_markdown(run_name: str, sessions: list[SessionMetrics], summary: dict[str, Any]) -> str:
    lines = [f"# 탐색 지표: {run_name}", ""]
    if summary:
        lines.append("## 그래프 상태")
        lines.append("")
        for key in ("nodes", "edges", "self_loop_edges", "unreached_from_root", "coverage"):
            if key in summary:
                lines.append(f"- {key}: {summary[key]}")
        if summary.get("screen_types"):
            lines.append(f"- screen_types: {summary['screen_types']}")
        if summary.get("verdicts"):
            lines.append(f"- ledger verdicts: {summary['verdicts']}")
        lines.append("")
    lines.append("## 세션별")
    lines.append("")
    lines.append("| " + " | ".join(label for _, label in COLUMNS) + " |")
    lines.append("|" + "---|" * len(COLUMNS))
    for metrics in sessions:
        row = metrics.row()
        lines.append("| " + " | ".join(_fmt(row[key]) for key, _ in COLUMNS) + " |")
    lines.append("")
    lines.append(
        "- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. "
        "판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id)."
    )
    lines.append("- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.")
    lines.append("")
    return "\n".join(lines)


def write_metrics(run_dir: Path, *, image_loader=_load_image) -> tuple[list[SessionMetrics], dict, Path]:
    """run 폴더의 기록으로 metrics.md / metrics.csv를 쓰고 (세션 지표, 그래프 요약, md 경로)를 돌려줍니다."""

    log_path = run_dir / "game_survey.jsonl"
    records = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except json.JSONDecodeError:
            return None

    sessions = compute_session_metrics(records, image_loader=image_loader)
    summary = graph_summary(
        _read_json(run_dir / "navigation" / "graph.json"),
        _read_json(run_dir / "navigation" / "ledger.json"),
    )

    run_dir.mkdir(parents=True, exist_ok=True)
    md_path = run_dir / "metrics.md"
    md_path.write_text(render_markdown(run_dir.name, sessions, summary), encoding="utf-8")
    csv_path = run_dir / "metrics.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        fieldnames = list(SessionMetrics(session=0, session_id="").row().keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for metrics in sessions:
            writer.writerow(metrics.row())
    return sessions, summary, md_path


def format_session_line(metrics: SessionMetrics) -> str:
    """반복 실행 중 콘솔에 찍는 한 줄 요약."""
    hit = _fmt(metrics.hit_rate)
    return (
        f"세션 {metrics.session}: 스텝 {metrics.steps}, 탭 {metrics.taps} "
        f"(바뀜 {metrics.taps_changed}/그대로 {metrics.taps_unchanged}, 적중률 {hit}), "
        f"누적 화면 {metrics.cum_screens}(+{metrics.new_screens}), "
        f"누적 전환 {metrics.cum_transitions}(+{metrics.new_transitions}), 종료 {metrics.stop}"
    )


def _resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        return Path(args.run_dir).resolve()
    run_id = args.run
    if not run_id:
        pointer = DEFAULT_GAME_ROOT / "current_run.txt"
        if not pointer.exists():
            raise SystemExit("current_run.txt가 없습니다. --run 또는 --run-dir를 지정하세요.")
        run_id = pointer.read_text(encoding="utf-8").strip()
    return DEFAULT_GAME_ROOT / "runs" / run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="run의 세션별 탐색 지표(적중률, 누적 화면/전환)를 계산합니다.")
    parser.add_argument("--run", help="runs 아래 폴더 이름 (기본: current_run.txt)")
    parser.add_argument("--run-dir", help="run 폴더 경로를 직접 지정")
    parser.add_argument("--no-images", action="store_true", help="옛 기록의 픽셀 비교를 건너뜁니다(빠름)")
    args = parser.parse_args()

    run_dir = _resolve_run_dir(args)
    if not (run_dir / "game_survey.jsonl").exists():
        raise SystemExit(f"조사 로그가 없습니다: {run_dir / 'game_survey.jsonl'}")
    sessions, summary, md_path = write_metrics(
        run_dir, image_loader=None if args.no_images else _load_image
    )
    print(render_markdown(run_dir.name, sessions, summary))
    print(f"[METRICS] {md_path}")


if __name__ == "__main__":
    main()
