"""조사 기록의 LLM 버튼 좌표(center)를 원본 캡처 위에 점으로 찍어 확인합니다.

클릭이 빗나갈 때 원인이 'LLM이 좌표를 잘못 찍음'인지, '캡처 영역/변환 문제'인지
눈으로 구분하기 위한 진단 도구입니다. 게임·API·마우스에는 손대지 않습니다.

사용 예 (저장소 루트에서):
    python candy_soda_agent/debug_survey_points.py
    python candy_soda_agent/debug_survey_points.py --run run_2026-09-21_10-51-14 --limit 10
    python candy_soda_agent/debug_survey_points.py --log output/candy_crush_soda/runs/<run>/game_survey.jsonl

결과: <run 폴더>/debug_points/
    NNN_step_XXX.png  (기록 순번_세션 내 스텝) 번호 마커 + 버튼 bbox 사각형 + 10% 보조선 + 오른쪽 범례
                  실제로 누른 버튼은 자홍색 이중 원, 확대 재확인으로 옮긴 경우
                  처음 좌표(회색 X)에서 실제 누른 곳까지 화살표
    summary.md    단계별 버튼 라벨/좌표/픽셀, 확대 재확인 결과, 탭 뒤 화면 변화,
                  좌표 정밀도 통계
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


from survey_metrics import game_root

REPO_ROOT = Path(__file__).resolve().parent.parent

# 역할별 마커 색(BGR)
ROLE_COLORS = {
    "safe_navigation": (80, 200, 80),
    "progression": (255, 160, 0),
    "structure": (0, 200, 255),
    "risky_monetization": (60, 60, 230),
    "risky_ad": (60, 60, 230),
    "risky_account": (60, 60, 230),
    "unknown": (180, 180, 180),
}
EXECUTED_COLOR = (255, 0, 255)  # 실제로 누른 버튼은 자홍색 이중 원
PANEL_WIDTH = 330


def _resolve_log(args: argparse.Namespace) -> Path:
    # --log > --run > current_run.txt 순서로 조사 로그 경로를 정합니다.
    if args.log:
        return Path(args.log).resolve()
    root = game_root()  # output/<GAME_SLUG>
    run_id = args.run
    if not run_id:
        pointer = root / "current_run.txt"
        if not pointer.exists():
            raise SystemExit(f"{pointer}가 없습니다. --run 또는 --log를 지정하세요(GAME_SLUG 확인).")
        run_id = pointer.read_text(encoding="utf-8").strip()
    return root / "runs" / run_id / "game_survey.jsonl"


def _resolve_image(raw_path: str) -> Path | None:
    # 기록된 경로는 Windows 구분자(\)의 상대경로라서 저장소 루트 기준으로도 찾아봅니다.
    candidate = Path(raw_path.replace("\\", "/"))
    for path in (candidate, REPO_ROOT / candidate):
        if path.exists():
            return path
    return None


def _decimals(value: float) -> int:
    # 0.5 -> 1, 0.85 -> 2, 0.123 -> 3. LLM이 좌표를 얼마나 거칠게 찍는지 봅니다.
    text = repr(float(value))
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0


def _draw_guides(image: np.ndarray) -> None:
    # 10% 간격 보조선: 점들이 선 교차점에 몰려 있으면 LLM이 좌표를 반올림하고 있다는 뜻입니다.
    height, width = image.shape[:2]
    overlay = image.copy()
    for i in range(1, 10):
        x = round(width * i / 10)
        y = round(height * i / 10)
        cv2.line(overlay, (x, 0), (x, height), (255, 255, 255), 1)
        cv2.line(overlay, (0, y), (width, y), (255, 255, 255), 1)
    cv2.addWeighted(overlay, 0.35, image, 0.65, 0, dst=image)


def _same_button(a: dict, b: dict | None) -> bool:
    # 확대 재확인이 center를 옮기므로 라벨+역할로 같은 버튼인지 봅니다.
    if not b:
        return False
    return a.get("label") == b.get("label") and a.get("role") == b.get("role")


def render_record(record: dict, image: np.ndarray, guides: bool) -> tuple[np.ndarray, list[dict]]:
    """한 기록의 버튼 좌표를 이미지에 그리고, 요약용 행 목록을 반환합니다."""

    height, width = image.shape[:2]
    canvas = image.copy()
    if guides:
        _draw_guides(canvas)

    action = record.get("action") or {}
    executed_button = action.get("button") if action.get("executed") else None
    refine = action.get("refine") or {}

    rows: list[dict] = []
    for index, button in enumerate(record.get("buttons", []), start=1):
        center = button.get("center")
        role = button.get("role", "unknown")
        row = {
            "no": index,
            "label": button.get("label", ""),
            "role": role,
            "confidence": button.get("confidence"),
            "center": center,
            "pixel": None,
            "executed": _same_button(button, executed_button),
        }
        rows.append(row)
        bbox = button.get("bbox")
        if bbox:
            color = ROLE_COLORS.get(role, ROLE_COLORS["unknown"])
            cv2.rectangle(canvas, (bbox["x1"], bbox["y1"]), (bbox["x2"], bbox["y2"]),
                          color, 1, cv2.LINE_AA)
        if not center:
            continue

        # action_executor.execute_button_tap과 같은 식(캡처 영역 기준)으로 픽셀을 계산합니다.
        x = round(center["x"] * width)
        y = round(center["y"] * height)
        row["pixel"] = (x, y)
        color = ROLE_COLORS.get(role, ROLE_COLORS["unknown"])

        if row["executed"] and not refine.get("refined_px"):
            cv2.circle(canvas, (x, y), 26, EXECUTED_COLOR, 3, cv2.LINE_AA)
        cv2.circle(canvas, (x, y), 16, color, 2, cv2.LINE_AA)
        cv2.drawMarker(canvas, (x, y), color, cv2.MARKER_CROSS, 12, 2)
        cv2.putText(canvas, str(index), (x + 18, y - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(canvas, str(index), (x + 18, y - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color, 2, cv2.LINE_AA)

    # 확대 재확인으로 옮긴 경우: 처음 좌표(회색 X) -> 실제로 누른 곳(자홍색 이중 원).
    refined = refine.get("refined_px")
    initial = refine.get("initial_px")
    if refined and initial:
        start, end = tuple(int(v) for v in initial), tuple(int(v) for v in refined)
        cv2.drawMarker(canvas, start, (160, 160, 160), cv2.MARKER_TILTED_CROSS, 18, 2)
        cv2.arrowedLine(canvas, start, end, EXECUTED_COLOR, 2, cv2.LINE_AA, tipLength=0.08)
        cv2.circle(canvas, end, 26, EXECUTED_COLOR, 3, cv2.LINE_AA)

    # 오른쪽 범례(OpenCV는 한글을 못 그려서 번호/역할/좌표만, 라벨은 summary.md에).
    panel = np.full((height, PANEL_WIDTH, 3), 30, np.uint8)
    lines = [
        f"step {record.get('step')}  {record.get('model', '')}",
        f"screen: {record.get('survey', {}).get('screen_type', '?')}",
        f"outcome: {action.get('outcome', action.get('type', '-'))}",
        f"image: {width}x{height}",
        "",
    ]
    for row in rows:
        mark = "*" if row["executed"] else " "
        if row["center"]:
            c = row["center"]
            lines.append(f"{mark}{row['no']:>2} ({c['x']:.3g},{c['y']:.3g}) {row['role'][:12]}")
        else:
            lines.append(f"{mark}{row['no']:>2} (no center) {row['role'][:12]}")
    if refine:
        shift = refine.get("shift_px")
        lines += ["", f"refine: {refine.get('used_pass') or ('error' if refine.get('error') else 'not found')}",
                  f"  shift: {shift if shift is not None else '-'} px"]
    effect = action.get("tap_effect") or {}
    if effect:
        lines.append(f"effect: {effect.get('final_change')} {'changed' if effect.get('changed') else 'SAME'}")
    lines += ["", "* = tapped (magenta ring)", "grey X -> arrow = refined", "box = bbox", "grid = 10% guides"]
    for i, text in enumerate(lines):
        cv2.putText(panel, text, (10, 28 + i * 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (230, 230, 230), 1, cv2.LINE_AA)
    return np.hstack([canvas, panel]), rows


def main() -> None:
    parser = argparse.ArgumentParser(description="조사 기록의 버튼 좌표를 캡처 위에 그려 확인합니다.")
    parser.add_argument("--run", help="runs 아래 폴더 이름 (기본: current_run.txt)")
    parser.add_argument("--log", help="game_survey.jsonl 경로를 직접 지정")
    parser.add_argument("--out", help="결과 폴더 (기본: <run>/debug_points)")
    parser.add_argument("--limit", type=int, default=0, help="앞에서부터 N개 기록만 (0=전체)")
    parser.add_argument("--no-guides", action="store_true", help="10%% 보조선 끄기")
    args = parser.parse_args()

    log_path = _resolve_log(args)
    if not log_path.exists():
        raise SystemExit(f"조사 로그가 없습니다: {log_path}")
    out_dir = Path(args.out) if args.out else log_path.parent / "debug_points"
    out_dir.mkdir(parents=True, exist_ok=True)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit > 0:
        records = records[: args.limit]

    decimals: Counter[int] = Counter()
    x_values: Counter[float] = Counter()
    summary = [f"# 버튼 좌표 진단: {log_path.parent.name}", ""]
    missing_images = 0

    for number, record in enumerate(records, start=1):
        step = record.get("step", 0)
        image_path = _resolve_image(str(record.get("raw_image", "")))
        image = cv2.imread(str(image_path)) if image_path else None
        if image is None:
            missing_images += 1
            summary.append(f"## step {step}: 이미지 없음 ({record.get('raw_image')})")
            summary.append("")
            continue

        rendered, rows = render_record(record, image, guides=not args.no_guides)
        # step 번호는 세션마다 1부터 다시 시작하므로 기록 순번을 앞에 붙여 덮어쓰지 않습니다.
        output_path = out_dir / f"{number:03d}_step_{step:03d}.png"
        cv2.imwrite(str(output_path), rendered)

        action = record.get("action") or {}
        summary.append(
            f"## #{number} session {record.get('session_id', '?')} step {step} · "
            f"{record.get('survey', {}).get('screen_type', '?')} · "
            f"{action.get('outcome', action.get('type', '-'))}"
        )
        summary.append(f"![step {step}]({output_path.name})")
        summary.append("")
        refine = action.get("refine") or {}
        if refine:
            if refine.get("error"):
                refine_text = f"오류({refine['error']}) → 처음 좌표로 누름"
            elif refine.get("found"):
                refine_text = (
                    f"{refine.get('initial_px')} → {refine.get('refined_px')} "
                    f"({refine.get('shift_px')}px, {refine.get('used_pass')})"
                )
            else:
                refine_text = "못 찾음 → 누르지 않음"
            summary.append(f"- 확대 재확인: {refine_text}")
        effect = action.get("tap_effect") or {}
        if effect:
            summary.append(
                f"- 탭 뒤 화면 변화: {effect.get('final_change')} "
                f"({'바뀜' if effect.get('changed') else '그대로 = 빗나감 의심'})"
            )
        if refine or effect:
            summary.append("")
        summary.append("| # | 라벨 | 역할 | 확신도 | center (0~1) | 픽셀(캡처 기준) | 누름 |")
        summary.append("|---|---|---|---|---|---|---|")
        for row in rows:
            center = row["center"]
            if center:
                decimals[max(_decimals(center["x"]), _decimals(center["y"]))] += 1
                x_values[center["x"]] += 1
            center_text = f"{center['x']}, {center['y']}" if center else "-"
            pixel_text = f"{row['pixel'][0]}, {row['pixel'][1]}" if row["pixel"] else "-"
            summary.append(
                f"| {row['no']} | {row['label']} | {row['role']} | {row['confidence']} | "
                f"{center_text} | {pixel_text} | {'✔' if row['executed'] else ''} |"
            )
        summary.append("")

    # 좌표 정밀도 통계: 소수 1자리(=10% 단위)가 대부분이면 LLM이 사실상 거친 격자로 찍는 중입니다.
    total = sum(decimals.values())
    stats = ["## 좌표 정밀도 통계", ""]
    if total:
        for places in sorted(decimals):
            stats.append(f"- 소수 {places}자리: {decimals[places]}개 ({decimals[places] / total:.0%})")
        center_x = x_values.get(0.5, 0)
        stats.append(f"- x=0.5(정중앙)로 찍은 버튼: {center_x}개 ({center_x / total:.0%})")
    else:
        stats.append("- center가 있는 버튼이 없습니다.")
    if missing_images:
        stats.append(f"- 이미지를 찾지 못한 기록: {missing_images}개")
    stats.append("")

    summary_path = out_dir / "summary.md"
    summary_path.write_text("\n".join(summary[:2] + stats + summary[2:]), encoding="utf-8")
    print("\n".join(stats))
    print(f"[DEBUG POINTS] {len(records) - missing_images}개 이미지 -> {out_dir}")


if __name__ == "__main__":
    main()
