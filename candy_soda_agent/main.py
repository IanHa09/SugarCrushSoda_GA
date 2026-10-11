"""Candy Crush Soda 에이전트의 실행 진입점입니다."""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

# config.py가 import 시점에 환경변수를 읽으므로 반드시 먼저 불러옵니다.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# --new-run은 config.py가 run_id를 정하기 전에 알려야 하므로 argparse보다 먼저 봅니다.
if "--new-run" in sys.argv:
    os.environ["NEW_RUN"] = "1"

from openai import OpenAI

from cli import build_run_plan, parse_args
from config import RUN_DIR, RUN_ID
from hotkeys import AUTO_RUNNING, EXIT_REQUESTED, release_hotkeys, setup_hotkeys
from navigation.navigator import run_autodrive
from run_loop import run
from safety_guard import make_cancel_check
from storage import load_all_survey_records
from survey_metrics import format_session_line, write_metrics
from survey_report import write_survey_report


def _print_metrics(header: str) -> None:
    """현재 run의 세션별 지표를 갱신하고 마지막 세션 한 줄을 출력합니다."""

    try:
        sessions, _summary, md_path = write_metrics(RUN_DIR)
    except Exception as error:  # noqa: BLE001 - 지표 실패로 탐색을 멈추지 않습니다.
        print(f"[METRICS] 지표 계산 실패: {error}")
        return
    if sessions:
        print(f"[METRICS] {header} {format_session_line(sessions[-1])}")
    print(f"[METRICS] {md_path}")


def run_autodrive_sessions(client, plan, cancel_check) -> None:
    """--autodrive-repeat N: 세션을 N번 이어서 돌립니다(같은 run에 누적).

    프론티어가 소진(converged)되거나, 취소·오류·클릭 차단이면 남은 세션을 건너뜁니다.
    unreachable(가는 길을 모름)로 끝난 세션은 다음 세션이 루트 정렬부터 다시
    시작하므로 계속 진행합니다."""

    repeats = plan.args.autodrive_repeat
    print(f"[AUTODRIVE] run: {RUN_ID} / 세션 {repeats}회 x 최대 {plan.args.autodrive_steps}스텝")
    for index in range(1, repeats + 1):
        if repeats > 1:
            print(f"\n[AUTODRIVE] ===== 세션 {index}/{repeats} 시작 =====")
        summary = run_autodrive(
            client,
            max_steps=plan.args.autodrive_steps,
            allow_taps=plan.autodrive_taps,
            cancel_check=cancel_check,
        )
        _print_metrics(f"[{index}/{repeats}]")
        # play_stuck: 플레이 화면에서 못 나옴. 다음 세션도 같은 화면에서 시작하므로 멈춥니다.
        if summary.stop_kind in {"converged", "cancelled", "error", "blocked", "play_stuck"}:
            if index < repeats:
                print(f"[AUTODRIVE] 세션을 더 돌리지 않습니다: {summary.stop_reason}")
            break
        if index < repeats:
            reason = cancel_check() if cancel_check else None
            if reason:
                print(f"[AUTODRIVE] {reason}")
                break
            time.sleep(plan.args.autodrive_pause)


# 단축키: F8=1회 분석, F9=자동 모드 토글, ESC=종료. F8로 먼저 확인 후 F9 사용을 권장합니다.
def main() -> None:
    """옵션에 따라 조사 보고서 생성, 자동 탐색, 일반 실행 루프 중 하나를 수행합니다."""

    plan = build_run_plan(parse_args())

    if plan.args.survey_metrics:
        _print_metrics(f"[{RUN_ID}]")
        return

    if plan.args.survey_report:
        records = load_all_survey_records()
        report_path = write_survey_report(records)
        print(f"[SURVEY REPORT] {report_path} ({len(records)} records)")
        if not (plan.args.once or plan.args.auto or plan.survey_mode):
            return

    # OpenAI()는 환경변수 OPENAI_API_KEY를 읽습니다.
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            'PowerShell에서 $env:OPENAI_API_KEY="키"를 먼저 실행하세요.'
        )

    client = OpenAI()
    if plan.autodrive_mode:
        # --hotkeys를 켜야 ESC로 자동 탐색을 취소할 수 있습니다.
        keyboard = setup_hotkeys(plan.args.hotkeys)
        if plan.args.hotkeys:
            print("ESC : 자동 탐색을 중지합니다.")
        try:
            run_autodrive_sessions(
                client,
                plan,
                make_cancel_check("manual", EXIT_REQUESTED, AUTO_RUNNING),
            )
        finally:
            release_hotkeys(keyboard)
        return

    run(client, plan)


if __name__ == "__main__":
    main()
