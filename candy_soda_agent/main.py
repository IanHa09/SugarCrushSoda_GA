"""Candy Crush Soda 에이전트의 실행 진입점입니다."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# config.py가 import 시점에 환경변수를 읽으므로 반드시 먼저 불러옵니다.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from openai import OpenAI

from cli import build_run_plan, parse_args
from hotkeys import AUTO_RUNNING, EXIT_REQUESTED, release_hotkeys, setup_hotkeys
from navigation.navigator import run_autodrive
from run_loop import run
from safety_guard import make_cancel_check
from storage import load_all_survey_records
from survey_report import write_survey_report


# 단축키: F8=1회 분석, F9=자동 모드 토글, ESC=종료. F8로 먼저 확인 후 F9 사용을 권장합니다.
def main() -> None:
    """옵션에 따라 조사 보고서 생성, 자동 탐색, 일반 실행 루프 중 하나를 수행합니다."""

    plan = build_run_plan(parse_args())

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
            run_autodrive(
                client,
                max_steps=plan.args.autodrive_steps,
                allow_taps=plan.autodrive_taps,
                cancel_check=make_cancel_check(
                    "manual", EXIT_REQUESTED, AUTO_RUNNING
                ),
            )
        finally:
            release_hotkeys(keyboard)
        return

    run(client, plan)


if __name__ == "__main__":
    main()
