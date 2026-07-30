"""
Candy Crush Soda 실시간 화면 분석 프로그램.

단축키:
    F8  현재 화면을 한 번 분석
    F9  자동 분석 시작/중지
    ESC 프로그램 종료
"""

from __future__ import annotations

import json
import os
import threading
import time

import cv2
import keyboard
import mss
import numpy as np
from openai import OpenAI

from agent import analyze_board, validate_decision
from capture import (
    capture_bgr,
    frame_difference,
    make_absolute_region,
    print_monitors,
)
from config import (
    API_COOLDOWN,
    BOARD_OFFSET,
    CACHED_INPUT_USD_PER_MILLION_TOKENS,
    CAPTURE_INTERVAL,
    COLS,
    INPUT_USD_PER_MILLION_TOKENS,
    MAX_API_CALLS,
    MAX_TOTAL_TOKENS,
    MODEL,
    MONITOR_INDEX,
    NEW_BOARD_THRESHOLD,
    OUTPUT_USD_PER_MILLION_TOKENS,
    ROWS,
    STABLE_FRAME_COUNT,
    STABLE_THRESHOLD,
)
from image_utils import add_grid_overlay
from memory import build_memory_prompt
from storage import save_run
from usage import (
    ApiBudgetExceeded,
    ApiUsage,
    UsageBudget,
)


AUTO_RUNNING = threading.Event()
ANALYZE_ONCE = threading.Event()
EXIT_REQUESTED = threading.Event()


def toggle_auto_mode() -> None:
    """F9를 누를 때 자동 분석 상태를 전환."""

    if AUTO_RUNNING.is_set():
        AUTO_RUNNING.clear()
        print("\n[AUTO STOP] 자동 분석을 중지했습니다.")
    else:
        AUTO_RUNNING.set()
        print("\n[AUTO START] 자동 분석을 시작했습니다.")


def print_decision(
    decision,
    is_valid: bool,
    message: str,
) -> None:
    print("\n[LLM 응답]")
    print(
        json.dumps(
            decision.model_dump(),
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        f"[검증] valid={is_valid}, message={message}"
    )


def print_budget(budget: UsageBudget) -> None:
    cost = budget.usage.estimated_cost_usd(
        INPUT_USD_PER_MILLION_TOKENS,
        OUTPUT_USD_PER_MILLION_TOKENS,
        CACHED_INPUT_USD_PER_MILLION_TOKENS,
    )

    print(
        f"[API USAGE] calls={budget.calls}/"
        f"{budget.max_calls}, "
        f"tokens={budget.usage.total_tokens:,}/"
        f"{budget.max_total_tokens:,}, "
        f"input={budget.usage.input_tokens:,}, "
        f"output={budget.usage.output_tokens:,}, "
        f"estimated_cost=${cost:.4f}"
    )


def process_frame(
    client: OpenAI,
    raw_image: np.ndarray,
) -> ApiUsage:
    """보드 한 장을 분석하고 결과와 사용량을 저장."""

    grid_image = add_grid_overlay(
        raw_image,
        ROWS,
        COLS,
    )

    memory_prompt, memory_size = build_memory_prompt()
    print(
        f"\n[REQUEST] 게임 보드를 전송합니다. "
        f"영상 경험 {memory_size}개 반영"
    )

    decision, usage = analyze_board(
        client,
        grid_image,
        memory_prompt,
    )
    is_valid, message = validate_decision(decision)

    print_decision(decision, is_valid, message)
    save_run(
        raw_image,
        grid_image,
        decision,
        is_valid,
        message,
        usage,
    )

    return usage


def run_api_analysis(
    client: OpenAI,
    raw_image: np.ndarray,
    budget: UsageBudget,
) -> ApiUsage:
    """예산을 차감한 뒤 보드 분석을 한 번 실행."""

    budget.start_call()
    print(
        f"[API COUNT] {budget.calls}/"
        f"{budget.max_calls}"
    )

    usage = process_frame(client, raw_image)
    budget.add_usage(usage)
    print_budget(budget)
    return usage


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            ".env 파일 또는 환경변수를 확인하세요."
        )

    client = OpenAI()
    budget = UsageBudget(
        max_calls=MAX_API_CALLS,
        max_total_tokens=MAX_TOTAL_TOKENS,
    )

    keyboard.add_hotkey("f8", ANALYZE_ONCE.set)
    keyboard.add_hotkey("f9", toggle_auto_mode)
    keyboard.add_hotkey("esc", EXIT_REQUESTED.set)

    print("F8  : 현재 화면 한 번 분석")
    print("F9  : 자동 분석 시작/중지")
    print("ESC : 프로그램 종료")
    print(f"MODEL: {MODEL}")
    print(
        f"LIMIT: {MAX_API_CALLS} calls / "
        f"{MAX_TOTAL_TOKENS:,} tokens"
    )

    previous_frame: np.ndarray | None = None
    last_sent_frame: np.ndarray | None = None
    stable_count = 0
    last_capture_time = 0.0
    last_api_time = 0.0

    try:
        with mss.mss() as sct:
            print_monitors(sct)

            board_region = make_absolute_region(
                sct,
                MONITOR_INDEX,
                BOARD_OFFSET,
            )
            print(
                f"\n게임 보드 절대 좌표: {board_region}"
            )

            while not EXIT_REQUESTED.is_set():
                cv2.waitKey(1)

                if ANALYZE_ONCE.is_set():
                    ANALYZE_ONCE.clear()

                    raw_image = capture_bgr(
                        sct,
                        board_region,
                    )
                    cv2.imshow(
                        "Candy Soda board preview",
                        raw_image,
                    )

                    try:
                        last_api_time = time.monotonic()
                        run_api_analysis(
                            client,
                            raw_image,
                            budget,
                        )
                        last_sent_frame = raw_image.copy()
                    except ApiBudgetExceeded as error:
                        AUTO_RUNNING.clear()
                        print(f"[LIMIT] {error}")
                    except Exception as error:
                        print(
                            f"[ERROR] 한 번 분석 실패: "
                            f"{error}"
                        )

                    continue

                if not AUTO_RUNNING.is_set():
                    time.sleep(0.05)
                    continue

                now = time.monotonic()

                if (
                    now - last_capture_time
                    < CAPTURE_INTERVAL
                ):
                    time.sleep(0.01)
                    continue

                last_capture_time = now
                current_frame = capture_bgr(
                    sct,
                    board_region,
                )
                cv2.imshow(
                    "Candy Soda board preview",
                    current_frame,
                )

                if previous_frame is None:
                    previous_frame = current_frame.copy()
                    continue

                change = frame_difference(
                    previous_frame,
                    current_frame,
                )
                previous_frame = current_frame.copy()

                if change < STABLE_THRESHOLD:
                    stable_count += 1
                else:
                    stable_count = 0

                if stable_count < STABLE_FRAME_COUNT:
                    continue

                if last_sent_frame is not None:
                    new_board_change = frame_difference(
                        last_sent_frame,
                        current_frame,
                    )
                    if (
                        new_board_change
                        < NEW_BOARD_THRESHOLD
                    ):
                        continue

                if (
                    now - last_api_time
                    < API_COOLDOWN
                ):
                    continue

                try:
                    last_api_time = now
                    run_api_analysis(
                        client,
                        current_frame,
                        budget,
                    )
                    last_sent_frame = (
                        current_frame.copy()
                    )
                    stable_count = 0
                except ApiBudgetExceeded as error:
                    AUTO_RUNNING.clear()
                    print(f"[LIMIT] {error}")
                except Exception as error:
                    print(
                        f"[ERROR] 자동 분석 실패: "
                        f"{error}"
                    )

    finally:
        keyboard.unhook_all_hotkeys()
        cv2.destroyAllWindows()
        print_budget(budget)
        print("\n프로그램을 종료했습니다.")


if __name__ == "__main__":
    main()
