"""
Candy Crush Soda 화면 분석 에이전트의 실행 파일입니다.

단축키:
    F8  : 현재 화면을 한 번 캡처하고 즉시 분석
    F9  : 자동 분석 모드 시작/중지
    ESC : 프로그램 종료

처음에는 F8 모드부터 성공시킨 뒤 F9 자동 모드를 사용하는 것이 좋습니다.
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
from dotenv import load_dotenv

load_dotenv()

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
    CAPTURE_INTERVAL,
    COLS,
    MAX_API_CALLS,
    MODEL,
    MONITOR_INDEX,
    NEW_BOARD_THRESHOLD,
    ROWS,
    STABLE_FRAME_COUNT,
    STABLE_THRESHOLD,
)
from image_utils import add_grid_overlay
from storage import save_run


# 키보드 콜백과 메인 루프가 상태를 공유할 수 있도록 Event를 사용합니다.
AUTO_RUNNING = threading.Event()
ANALYZE_ONCE = threading.Event()
EXIT_REQUESTED = threading.Event()


def toggle_auto_mode() -> None:
    """F9를 누를 때 자동 분석 상태를 전환합니다."""

    if AUTO_RUNNING.is_set():
        AUTO_RUNNING.clear()
        print("\n[AUTO STOP] 자동 분석을 중지했습니다.")
    else:
        AUTO_RUNNING.set()
        print("\n[AUTO START] 자동 분석을 시작했습니다.")


def print_decision(decision, is_valid: bool, message: str) -> None:
    """콘솔에서 한 번의 판단 결과를 읽기 좋게 출력합니다."""

    print("\n[LLM 응답]")
    print(
        json.dumps(
            decision.model_dump(),
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"[검증] valid={is_valid}, message={message}")


def process_frame(
    client: OpenAI,
    raw_image: np.ndarray,
) -> None:
    """
    캡처 한 장을 격자 이미지로 바꾸고 LLM에 전달한 뒤 결과를 저장합니다.
    이 함수는 게임을 클릭하지 않고 행동 후보만 출력합니다.
    """

    grid_image = add_grid_overlay(raw_image, ROWS, COLS)

    print("\n[REQUEST] 게임 보드를 LLM에 전송합니다.")

    decision = analyze_board(client, grid_image)
    is_valid, message = validate_decision(decision)

    print_decision(decision, is_valid, message)
    save_run(raw_image, grid_image, decision, is_valid, message)


def main() -> None:
    # OpenAI()는 환경변수 OPENAI_API_KEY를 읽습니다.
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            'PowerShell에서 $env:OPENAI_API_KEY="키"를 먼저 실행하세요.'
        )

    client = OpenAI()

    # 키를 누르면 별도 콜백 스레드에서 Event 상태만 변경합니다.
    keyboard.add_hotkey("f8", ANALYZE_ONCE.set)
    keyboard.add_hotkey("f9", toggle_auto_mode)
    keyboard.add_hotkey("esc", EXIT_REQUESTED.set)

    print("F8  : 현재 화면 한 번 분석")
    print("F9  : 자동 분석 시작/중지")
    print("ESC : 프로그램 종료")
    print(f"MODEL: {MODEL}")

    previous_frame: np.ndarray | None = None
    last_sent_frame: np.ndarray | None = None

    stable_count = 0
    last_capture_time = 0.0
    last_api_time = 0.0
    # 이번 실행에서 실제 API 몇 번 호출했는지 기록
    api_call_count = 0

    try:
        # MSS 객체는 반복문 안에서 매번 만들지 않고 한 번만 열어 재사용합니다.
        with mss.mss() as sct:
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

                # --------------------------------------------------------
                # F8: 안정화 검사를 생략하고 현재 화면을 한 번 분석
                # 설정이 맞는지 처음 시험할 때 사용
                # --------------------------------------------------------
                if ANALYZE_ONCE.is_set():
                    ANALYZE_ONCE.clear()

                    raw_image = capture_bgr(sct, board_region)
                    cv2.imshow("Candy Soda board preview", raw_image)

                    try:
                        process_frame(client, raw_image)
                        last_sent_frame = raw_image.copy()
                        last_api_time = time.time()
                    except Exception as error:
                        print(f"[ERROR] 한 번 분석 실패: {error}")

                    continue

                # 자동 모드가 꺼져 있으면 캡처하지 않고 잠깐 쉽니다.
                if not AUTO_RUNNING.is_set():
                    time.sleep(0.05)
                    continue

                now = time.time()

                # 지정한 캡처 주기가 되지 않았으면 다음 반복으로 넘어갑니다.
                if now - last_capture_time < CAPTURE_INTERVAL:
                    time.sleep(0.01)
                    continue

                last_capture_time = now
                current_frame = capture_bgr(sct, board_region)
                cv2.imshow("Candy Soda board preview", current_frame)

                # 첫 프레임에는 비교 대상이 없으므로 저장만 합니다.
                if previous_frame is None:
                    previous_frame = current_frame.copy()
                    continue

                # 직전 프레임과 비교해 애니메이션이 멈췄는지 대략 확인합니다.
                change = frame_difference(previous_frame, current_frame)
                previous_frame = current_frame.copy()

                if change < STABLE_THRESHOLD:
                    stable_count += 1
                else:
                    stable_count = 0

                if stable_count < STABLE_FRAME_COUNT:
                    continue

                # 마지막으로 보낸 보드와 거의 같으면 중복 API 요청을 생략합니다.
                if last_sent_frame is not None:
                    new_board_change = frame_difference(
                        last_sent_frame,
                        current_frame,
                    )
                    if new_board_change < NEW_BOARD_THRESHOLD:
                        continue

                # 짧은 시간에 요청이 몰리는 것을 막습니다.
                if now - last_api_time < API_COOLDOWN:
                    continue

                # 설정한 최대 요청 횟수에 도달 시 자동 모드를 중지
                if api_call_count >- MAX_API_CALLS:
                    AUTO_RUNNING.clear()

                    print(
                        f"\n[LIMIT] 최대 API 요청 수 "
                        f"{MAX_API_CALLS}회에 도달했습니다."
                    )
                    print("[LIIMIT] F9 자동 분석 중지")
                    continue

                # 실제 API 호출 직전에 증가 시키기.
                api_call_count += 1
                print(f"[API COUNT] {api_call_count}/{MAX_API_CALLS}")

                last_api_time = now

                try:
                    process_frame(client, current_frame)

                    last_sent_frame = current_frame.copy()
                    stable_count = 0
                    
                except Exception as error:
                    print(f"[ERROR] 자동 분석 실패: {error}")

    finally:
        keyboard.unhook_all_hotkeys()
        cv2.destroyAllWindows()
        print("\n프로그램을 종료했습니다.")


if __name__ == "__main__":
    main()
