"""명령줄 옵션 파싱과 실행 모드 조합 검증을 담당합니다."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from config import SURVEY_ALLOW_TAPS


def parse_args() -> argparse.Namespace:
    """명령줄 인자를 정의하고 파싱합니다."""

    parser = argparse.ArgumentParser(
        description="Candy Crush Soda 화면 분석 에이전트"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="현재 화면을 한 번 분석한 뒤 종료합니다.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="자동 분석 모드를 바로 시작합니다.",
    )
    parser.add_argument(
        "--survey-once",
        action="store_true",
        help="현재 화면을 한 번 게임 구조 조사 모드로 분석한 뒤 종료합니다.",
    )
    parser.add_argument(
        "--survey-auto",
        action="store_true",
        help="Autodrive 기반 자동 구조 조사를 시작합니다.",
    )
    parser.add_argument(
        "--survey-report",
        action="store_true",
        help="저장된 조사 로그로 Markdown 보고서를 생성한 뒤 종료합니다.",
    )
    parser.add_argument(
        "--survey-taps",
        action="store_true",
        help=(
            "--survey-auto에서 안전 버튼 후보 탭을 허용합니다. "
            "DRY_RUN=false 상태에서만 실제 클릭합니다."
        ),
    )
    parser.add_argument(
        "--hotkeys",
        action="store_true",
        help=(
            "keyboard 패키지로 F8/F9/ESC 단축키를 등록합니다. "
            "macOS 일부 환경에서는 이 패키지가 segfault를 낼 수 있습니다."
        ),
    )
    parser.add_argument(
        "--autodrive",
        action="store_true",
        help="게임 화면을 자동 탐색하고 통합 조사 보고서를 생성합니다.",
    )
    parser.add_argument(
        "--autodrive-steps",
        type=int,
        default=40,
        help="자동 탐색의 최대 단계 수입니다.",
    )
    return parser.parse_args()


@dataclass(frozen=True)
class RunPlan:
    """옵션 검증을 마치고 확정한 실행 모드입니다."""

    args: argparse.Namespace
    survey_mode: bool
    autodrive_mode: bool
    autodrive_taps: bool


def build_run_plan(args: argparse.Namespace) -> RunPlan:
    """옵션 조합을 검증하고 실행 모드를 확정합니다."""

    autodrive_mode = args.autodrive or args.survey_auto

    if args.autodrive and args.survey_auto:
        raise ValueError("--autodrive와 --survey-auto는 같은 모드입니다.")
    if autodrive_mode and any((
        args.once,
        args.auto,
        args.survey_once,
        args.survey_report,
    )):
        raise ValueError("자동 구조 조사는 다른 실행 모드와 함께 사용할 수 없습니다.")
    # --hotkeys는 예외: ESC로 자동 탐색을 취소하려면 함께 켤 수 있어야 합니다.

    return RunPlan(
        args=args,
        survey_mode=args.survey_once,
        autodrive_mode=autodrive_mode,
        autodrive_taps=SURVEY_ALLOW_TAPS or args.survey_taps,
    )
