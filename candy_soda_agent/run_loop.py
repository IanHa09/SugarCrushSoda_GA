"""F8 단발 분석과 F9 자동 분석을 스케줄링하는 실행 루프입니다."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import cv2
import mss
import numpy as np
from openai import OpenAI

from capture import frame_difference, make_absolute_region, print_monitors
from capture_modes import CaptureBundle, capture_configured
from cli import RunPlan
from config import (
    API_COOLDOWN,
    BOARD_OFFSET,
    CAPTURE_INTERVAL,
    DRY_RUN,
    MAX_API_CALLS,
    MODEL,
    MONITOR_INDEX,
    NEW_BOARD_THRESHOLD,
    STABLE_FRAME_COUNT,
    STABLE_THRESHOLD,
    SURVEY_MODEL,
)
from hotkeys import (
    ANALYZE_ONCE,
    AUTO_RUNNING,
    EXIT_REQUESTED,
    release_hotkeys,
    setup_hotkeys,
)
from play_session import process_frame
from safety_guard import make_cancel_check
from survey import process_survey_frame


# 이 결과들이면 보드가 그대로라는 뜻이므로, 다음 차례에 다시 분석하도록 기록을 지웁니다.
REANALYZE_OUTCOMES = frozenset({
    "rejected",
    "no_change",
    "validation_failed",
})

PREVIEW_WINDOW = "Candy Soda capture preview"


@dataclass
class AutoCaptureState:
    """자동 모드가 언제 API를 부를지 판단하기 위한 상태입니다."""

    previous_frame: np.ndarray | None = None
    last_sent_frame: np.ndarray | None = None
    stable_count: int = 0
    last_capture_time: float = 0.0
    last_api_time: float = 0.0
    # 이번 실행에서 실제 API를 몇 번 호출했는지 기록
    api_call_count: int = 0
    step_count: int = 0

    def observe(self, current_frame: np.ndarray) -> bool:
        """직전 프레임과 비교해 안정 횟수를 갱신합니다(첫 프레임은 저장만 하고 False)."""

        if self.previous_frame is None:
            self.previous_frame = current_frame.copy()
            return False

        change = frame_difference(self.previous_frame, current_frame)
        self.previous_frame = current_frame.copy()
        self.stable_count = (
            self.stable_count + 1 if change < STABLE_THRESHOLD else 0
        )
        return True

    def api_skip_reason(
        self,
        current_frame: np.ndarray,
        now: float,
    ) -> str | None:
        """지금 API를 부르지 않을 이유를 반환합니다. None이면 호출 가능합니다."""

        if self.stable_count < STABLE_FRAME_COUNT:
            return "not_stable"
        # 마지막으로 보낸 보드와 거의 같으면 중복 API 요청을 생략합니다.
        if self.last_sent_frame is not None:
            new_board_change = frame_difference(
                self.last_sent_frame,
                current_frame,
            )
            if new_board_change < NEW_BOARD_THRESHOLD:
                return "same_board"
        # 짧은 시간에 요청이 몰리는 것을 막습니다.
        if now - self.last_api_time < API_COOLDOWN:
            return "cooldown"
        if self.api_call_count >= MAX_API_CALLS:
            return "budget_exhausted"
        return None

    def remember_sent_frame(
        self,
        frame: np.ndarray,
        action_outcome: str,
    ) -> None:
        """실패한 결과면 기록을 지워 같은 화면이라도 다시 분석하게 합니다."""

        self.last_sent_frame = (
            None if action_outcome in REANALYZE_OUTCOMES else frame.copy()
        )


def _show_preview(bundle: CaptureBundle) -> None:
    # DRY_RUN일 때만 미리보기 창을 띄웁니다.
    if DRY_RUN:
        cv2.imshow(PREVIEW_WINDOW, bundle.full_image)


def _run_cycle(
    client: OpenAI,
    sct,
    bundle: CaptureBundle,
    *,
    plan: RunPlan,
    state: AutoCaptureState,
    session_id: str,
    mode: str,
) -> str:
    """한 프레임을 분석하고 마지막 전송 프레임 기록을 갱신합니다."""

    state.step_count += 1
    if plan.survey_mode:
        action_outcome = process_survey_frame(
            client,
            bundle,
            session_id=session_id,
            step=state.step_count,
            mode=mode,
        )
        sent_frame = bundle.full_image
    else:
        # ESC 또는(자동 모드일 때) F9로 취소되면 행동 실행 직전에 취소합니다.
        cancel_check = make_cancel_check(mode, EXIT_REQUESTED, AUTO_RUNNING)
        action_outcome = process_frame(
            client,
            sct,
            bundle,
            session_id=session_id,
            step=state.step_count,
            mode=mode,
            cancel_check=cancel_check,
        )
        sent_frame = bundle.board_image

    state.remember_sent_frame(sent_frame, action_outcome)
    return action_outcome


def _analyze_once(
    client: OpenAI,
    sct,
    plan: RunPlan,
    state: AutoCaptureState,
    session_id: str,
) -> None:
    """F8 또는 --once로 요청한 단발 분석: 안정화 검사 없이 현재 화면을 바로 분석합니다."""

    bundle = capture_configured(sct)
    _show_preview(bundle)

    try:
        _run_cycle(
            client,
            sct,
            bundle,
            plan=plan,
            state=state,
            session_id=session_id,
            mode="manual_survey" if plan.survey_mode else "manual",
        )
        state.last_api_time = time.time()
    except Exception as error:
        print(f"[ERROR] 한 번 분석 실패: {error}")


def _analyze_auto(
    client: OpenAI,
    sct,
    plan: RunPlan,
    state: AutoCaptureState,
    session_id: str,
) -> None:
    """자동 모드에서 캡처 주기와 안정화, 요청 예산을 확인하고 분석합니다."""

    now = time.time()

    # 지정한 캡처 주기가 되지 않았으면 다음 반복으로 넘어갑니다.
    if now - state.last_capture_time < CAPTURE_INTERVAL:
        time.sleep(0.01)
        return

    state.last_capture_time = now
    bundle = capture_configured(sct)
    _show_preview(bundle)

    if not state.observe(bundle.board_image):
        return

    skip_reason = state.api_skip_reason(bundle.board_image, now)
    if skip_reason == "budget_exhausted":
        # 설정한 최대 요청 횟수에 도달 시 자동 모드를 중지
        AUTO_RUNNING.clear()
        print(
            f"\n[LIMIT] 최대 API 요청 수 "
            f"{MAX_API_CALLS}회에 도달했습니다."
        )
        print("[LIMIT] F9 자동 분석 중지")
        return
    if skip_reason is not None:
        return

    # 실제 API 호출 직전에 증가 시키기.
    state.api_call_count += 1
    print(f"[API COUNT] {state.api_call_count}/{MAX_API_CALLS}")
    state.last_api_time = now

    try:
        _run_cycle(
            client,
            sct,
            bundle,
            plan=plan,
            state=state,
            session_id=session_id,
            mode="auto",
        )
        state.stable_count = 0
    except Exception as error:
        print(f"[ERROR] 자동 분석 실패: {error}")


def _print_startup_help(plan: RunPlan) -> None:
    # 실행 모드에 맞는 조작법과 설정을 안내합니다.
    if plan.args.hotkeys:
        print("F8  : 현재 화면 한 번 분석")
        print("F9  : 자동 분석 시작/중지")
        print("ESC : 프로그램 종료")
    else:
        print("--once : 현재 화면 한 번 분석")
        print("--auto : 자동 분석 바로 시작")
        print("--survey-once : 현재 화면 한 번 구조 조사")
        print("--survey-auto : Autodrive 기반 자동 구조 조사")
        print("--survey-report : 조사 보고서 생성")
        print("종료   : Ctrl+C")
    print(f"MODEL(play): {MODEL}")
    print(f"MODEL(survey): {SURVEY_MODEL}")
    print(f"RUN MODE: {'survey' if plan.survey_mode else 'play'}")
    if plan.survey_mode:
        print("[SURVEY ONCE] 현재 화면만 기록하고 종료합니다.")


def run(client: OpenAI, plan: RunPlan) -> None:
    """단축키와 실행 옵션에 따라 캡처와 분석을 반복합니다."""

    keyboard = setup_hotkeys(plan.args.hotkeys)
    if plan.args.once or plan.args.survey_once:
        ANALYZE_ONCE.set()
    if plan.args.auto:
        AUTO_RUNNING.set()

    _print_startup_help(plan)

    state = AutoCaptureState()
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    try:
        # MSS 객체는 반복문 안에서 매번 만들지 않고 한 번만 열어 재사용합니다.
        with mss.MSS() as sct:
            print_monitors(sct)

            board_region = make_absolute_region(
                sct,
                MONITOR_INDEX,
                BOARD_OFFSET,
            )
            print(f"\n게임 보드 절대 좌표: {board_region}")

            while not EXIT_REQUESTED.is_set():
                # OpenCV 미리보기 창이 반응하도록 이벤트를 처리
                cv2.waitKey(1)

                if ANALYZE_ONCE.is_set():
                    ANALYZE_ONCE.clear()
                    _analyze_once(client, sct, plan, state, session_id)
                    if plan.args.once or plan.args.survey_once:
                        EXIT_REQUESTED.set()
                    continue

                # 자동 모드가 꺼져 있으면 캡처하지 않고 잠깐 쉽니다.
                if not AUTO_RUNNING.is_set():
                    time.sleep(0.05)
                    continue

                _analyze_auto(client, sct, plan, state, session_id)
    finally:
        release_hotkeys(keyboard)
        cv2.destroyAllWindows()
        print("\n프로그램을 종료했습니다.")
