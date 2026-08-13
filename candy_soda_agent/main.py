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
import argparse
import threading
import time

import cv2
import mss
import numpy as np
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from action_executor import execute_decision
from agent import analyze_screen, validate_decision
from capture import (
    frame_difference,
    make_absolute_region,
    print_monitors,
)
from capture_modes import CaptureBundle, capture_bundle
from config import (
    ACTION_ACCEPTED_THRESHOLD,
    ACTION_ATTEMPT_THRESHOLD,
    ACTION_RETURNED_THRESHOLD,
    API_COOLDOWN,
    AUTO_GRID,
    BOARD_FINGERPRINT_MAX_DISTANCE,
    BOARD_OFFSET,
    CAPTURE_MODE,
    CAPTURE_INTERVAL,
    COLS,
    DRY_RUN,
    GRID_MAX_COLS,
    GRID_MAX_ROWS,
    GRID_MIN_COLS,
    GRID_MIN_CONFIDENCE,
    GRID_MIN_ROWS,
    MAX_API_CALLS,
    MEMORY_CONTEXT_LIMIT,
    MEMORY_SCAN_LIMIT,
    MODEL,
    MONITOR_INDEX,
    NEW_BOARD_THRESHOLD,
    POST_ACTION_INTERVAL,
    POST_ACTION_MIN_WAIT,
    POST_ACTION_STABLE_FRAMES,
    POST_ACTION_TIMEOUT,
    ROWS,
    STABLE_FRAME_COUNT,
    STABLE_THRESHOLD,
    WINDOW_OFFSET,
)
from grid_detector import detect_grid_shape
from image_utils import add_grid_overlay, board_fingerprint
from reward import ActionObservation, classify_action_outcome, evaluate_reward
from storage import (
    load_failed_moves_for_board,
    save_image,
    save_memory_entry,
    save_run,
)


# 키보드 콜백과 메인 루프가 상태를 공유할 수 있도록 Event를 사용합니다.
AUTO_RUNNING = threading.Event()
ANALYZE_ONCE = threading.Event()
EXIT_REQUESTED = threading.Event()


def parse_args() -> argparse.Namespace:
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
        "--hotkeys",
        action="store_true",
        help=(
            "keyboard 패키지로 F8/F9/ESC 단축키를 등록합니다. "
            "macOS 일부 환경에서는 이 패키지가 segfault를 낼 수 있습니다."
        ),
    )
    return parser.parse_args()


def setup_hotkeys(enabled: bool):
    """keyboard import가 macOS에서 segfault를 낼 수 있어 명시적으로만 켭니다."""

    if not enabled:
        return None

    import keyboard  # type: ignore

    keyboard.add_hotkey("f8", ANALYZE_ONCE.set)
    keyboard.add_hotkey("f9", toggle_auto_mode)
    keyboard.add_hotkey("esc", EXIT_REQUESTED.set)
    return keyboard


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


def _capture_current(sct) -> CaptureBundle:
    return capture_bundle(
        sct=sct,
        mode=CAPTURE_MODE,
        monitor_index=MONITOR_INDEX,
        board_offset=BOARD_OFFSET,
        window_offset=WINDOW_OFFSET,
    )


def observe_action_result(
    sct,
    before: CaptureBundle,
) -> tuple[ActionObservation, CaptureBundle]:
    """드래그 후 보드가 바뀌었는지, 원상복귀했는지 관찰합니다."""

    started = time.monotonic()
    previous = before.board_image
    final_bundle = before
    peak_change = 0.0
    stable_frames = 0
    settled = False

    while time.monotonic() - started < POST_ACTION_TIMEOUT:
        time.sleep(POST_ACTION_INTERVAL)
        final_bundle = _capture_current(sct)
        current = final_bundle.board_image
        peak_change = max(
            peak_change,
            frame_difference(before.board_image, current),
        )
        frame_change = frame_difference(previous, current)
        stable_frames = stable_frames + 1 if frame_change < STABLE_THRESHOLD else 0
        previous = current
        if (
            time.monotonic() - started >= POST_ACTION_MIN_WAIT
            and stable_frames >= POST_ACTION_STABLE_FRAMES
        ):
            settled = True
            break

    final_change = frame_difference(before.board_image, final_bundle.board_image)
    observation = classify_action_outcome(
        peak_change=peak_change,
        final_change=final_change,
        settled=settled,
        accepted_threshold=ACTION_ACCEPTED_THRESHOLD,
        attempt_threshold=ACTION_ATTEMPT_THRESHOLD,
        returned_threshold=ACTION_RETURNED_THRESHOLD,
    )
    print(
        "[OUTCOME] "
        f"{observation.status}, peak={peak_change:.4f}, "
        f"final={final_change:.4f}, settled={settled}"
    )
    return observation, final_bundle


def process_frame(
    client: OpenAI,
    sct,
    bundle: CaptureBundle,
    *,
    session_id: str,
    step: int,
    mode: str,
) -> str:
    """화면을 분석하고 행동 결과까지 메모리에 기록합니다."""

    shape = detect_grid_shape(
        bundle.board_image,
        ROWS,
        COLS,
        enabled=AUTO_GRID,
        min_rows=GRID_MIN_ROWS,
        max_rows=GRID_MAX_ROWS,
        min_cols=GRID_MIN_COLS,
        max_cols=GRID_MAX_COLS,
        min_confidence=GRID_MIN_CONFIDENCE,
    )
    detail = f", reason={shape.reason}" if shape.reason else ""
    print(
        f"[GRID] rows={shape.rows}, cols={shape.cols}, "
        f"confidence={shape.confidence:.2f}, source={shape.source}{detail}"
    )

    fingerprint = board_fingerprint(bundle.board_image)
    failed_memories, forbidden_moves = load_failed_moves_for_board(
        fingerprint,
        shape.rows,
        shape.cols,
        scan_limit=MEMORY_SCAN_LIMIT,
        context_limit=MEMORY_CONTEXT_LIMIT,
        max_distance=BOARD_FINGERPRINT_MAX_DISTANCE,
    )
    if forbidden_moves:
        print(f"[MEMORY] 같은 보드의 실패한 swap {len(forbidden_moves)}개 제외")

    grid_board = add_grid_overlay(bundle.board_image, shape.rows, shape.cols)
    print("\n[REQUEST] 전체 게임 화면과 보드를 LLM에 전송합니다.")
    decision = analyze_screen(
        client,
        bundle.full_image,
        grid_board,
        memory_context=failed_memories,
        rows=shape.rows,
        cols=shape.cols,
    )
    is_valid, message = validate_decision(
        decision,
        rows=shape.rows,
        cols=shape.cols,
        forbidden_moves=forbidden_moves,
    )
    print_decision(decision, is_valid, message)

    grid_record = {
        "rows": shape.rows,
        "cols": shape.cols,
        "confidence": shape.confidence,
        "source": shape.source,
    }
    run_record = save_run(
        bundle.full_image,
        grid_board,
        decision,
        is_valid,
        message,
        session_id=session_id,
        step=step,
        stored_image_scope=CAPTURE_MODE,
        grid=grid_record,
    )

    action_result = False
    execution_error = None
    observation: ActionObservation | None = None
    after_bundle: CaptureBundle | None = None
    if is_valid:
        try:
            action_result = execute_decision(
                decision,
                bundle.board_region,
                dry_run=DRY_RUN,
                rows=shape.rows,
                cols=shape.cols,
            )
            if action_result and not DRY_RUN and decision.action == "swap":
                observation, after_bundle = observe_action_result(sct, bundle)
        except Exception as error:
            execution_error = str(error)

    if not is_valid:
        action_outcome = "validation_failed"
    elif execution_error:
        action_outcome = "execution_failed"
    elif decision.action == "wait":
        action_outcome = "wait"
    elif DRY_RUN:
        action_outcome = "dry_run"
    elif observation:
        action_outcome = observation.status
    else:
        action_outcome = "unclear"

    final_change = observation.final_change if observation else None
    reward_result = evaluate_reward(
        action=decision.action,
        validation_passed=is_valid,
        executed=action_result and not DRY_RUN,
        dry_run=DRY_RUN,
        screen_change_score=final_change,
        success_threshold=ACTION_ACCEPTED_THRESHOLD,
        blocked_reason=message if not is_valid else None,
        execution_error=execution_error,
        action_outcome=action_outcome,
    )
    lesson = reward_result.lesson
    if action_outcome in {"rejected", "no_change"}:
        lesson += " 동일 보드에서는 이 swap과 역방향을 다시 선택하지 않는다."

    after_raw = None
    after_grid = None
    if after_bundle is not None:
        after_raw = save_image(after_bundle.board_image, "after_raw")
        after_grid = save_image(
            add_grid_overlay(after_bundle.board_image, shape.rows, shape.cols),
            "after_grid",
        )

    save_memory_entry(
        session_id=session_id,
        step=step,
        mode=mode,
        before={
            "raw_image": run_record["raw_image"],
            "grid_image": run_record["grid_image"],
            "board_fingerprint": fingerprint,
            "grid": grid_record,
            "ui_state": decision.detected_ui_state,
            "board_status": decision.board_status,
            "visible_elements": decision.visible_elements,
            "objectives": decision.objectives,
            "obstacles": decision.obstacles,
        },
        decision={
            "action": decision.action,
            "source": decision.source.model_dump() if decision.source else None,
            "target": decision.target.model_dump() if decision.target else None,
            "confidence": decision.confidence,
            "reason": decision.reason,
        },
        execution={
            "validation_passed": is_valid,
            "attempted": is_valid and decision.action == "swap",
            "executed": action_result and not DRY_RUN,
            "dry_run": DRY_RUN,
            "blocked_reason": message if not is_valid else execution_error,
            "action_outcome": action_outcome,
            "peak_change": observation.peak_change if observation else None,
            "final_change": final_change,
            "settled": observation.settled if observation else None,
        },
        after={
            "raw_image": after_raw,
            "grid_image": after_grid,
            "screen_change_score": final_change,
            "observed_effects": [action_outcome],
        },
        reward=reward_result.reward,
        success_estimate=reward_result.success_estimate,
        lesson=lesson,
        failure_reason=reward_result.failure_reason,
    )

    if execution_error is not None:
        raise RuntimeError(f"행동 실행 실패: {execution_error}")
    return action_outcome

def main() -> None:
    args = parse_args()

    # OpenAI()는 환경변수 OPENAI_API_KEY를 읽습니다.
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            'PowerShell에서 $env:OPENAI_API_KEY="키"를 먼저 실행하세요.'
        )

    client = OpenAI()

    keyboard = setup_hotkeys(args.hotkeys)
    if args.once:
        ANALYZE_ONCE.set()
    if args.auto:
        AUTO_RUNNING.set()

    if args.hotkeys:
        print("F8  : 현재 화면 한 번 분석")
        print("F9  : 자동 분석 시작/중지")
        print("ESC : 프로그램 종료")
    else:
        print("--once : 현재 화면 한 번 분석")
        print("--auto : 자동 분석 바로 시작")
        print("종료   : Ctrl+C")
    print(f"MODEL: {MODEL}")

    previous_frame: np.ndarray | None = None
    last_sent_frame: np.ndarray | None = None

    stable_count = 0
    last_capture_time = 0.0
    last_api_time = 0.0
    # 이번 실행에서 실제 API 몇 번 호출했는지 기록
    api_call_count = 0
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    memory_step_count = 0

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

                # --------------------------------------------------------
                # F8: 안정화 검사를 생략하고 현재 화면을 한 번 분석
                # 설정이 맞는지 처음 시험할 때 사용
                # --------------------------------------------------------
                if ANALYZE_ONCE.is_set():
                    ANALYZE_ONCE.clear()

                    bundle = capture_bundle(
                        sct=sct,
                        mode=CAPTURE_MODE,
                        monitor_index=MONITOR_INDEX,
                        board_offset=BOARD_OFFSET,
                        window_offset=WINDOW_OFFSET,
                    )
                    if DRY_RUN:
                        cv2.imshow("Candy Soda capture preview", bundle.full_image)

                    try:
                        memory_step_count += 1

                        action_outcome = process_frame(
                            client,
                            sct,
                            bundle,
                            session_id=session_id,
                            step=memory_step_count,
                            mode="manual",
                        )
                        last_sent_frame = (
                            None
                            if action_outcome in {
                                "rejected",
                                "no_change",
                                "validation_failed",
                            }
                            else bundle.board_image.copy()
                        )
                        last_api_time = time.time()
                    except Exception as error:
                        print(f"[ERROR] 한 번 분석 실패: {error}")

                    if args.once:
                        EXIT_REQUESTED.set()
                    continue
                # --------------------------------------------------------
                # F8: 안정화 검사를 생략하고 현재 화면을 한 번 분석
                # 설정이 맞는지 처음 시험할 때 사용
                # --------------------------------------------------------
                
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
                bundle = capture_bundle(
                    sct=sct,
                    mode=CAPTURE_MODE,
                    monitor_index=MONITOR_INDEX,
                    board_offset=BOARD_OFFSET,
                    window_offset=WINDOW_OFFSET,
                )
                current_frame = bundle.board_image
                if DRY_RUN:
                    cv2.imshow("Candy Soda capture preview", bundle.full_image)

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
                if api_call_count >= MAX_API_CALLS:
                    AUTO_RUNNING.clear()

                    print(
                        f"\n[LIMIT] 최대 API 요청 수 "
                        f"{MAX_API_CALLS}회에 도달했습니다."
                    )
                    print("[LIMIT] F9 자동 분석 중지")
                    continue

                # 실제 API 호출 직전에 증가 시키기.
                api_call_count += 1
                print(f"[API COUNT] {api_call_count}/{MAX_API_CALLS}")

                last_api_time = now

                try:
                    memory_step_count += 1

                    action_outcome = process_frame(
                        client,
                        sct,
                        bundle,
                        session_id=session_id,
                        step=memory_step_count,
                        mode="auto",
                    )

                    last_sent_frame = (
                        None
                        if action_outcome in {
                            "rejected",
                            "no_change",
                            "validation_failed",
                        }
                        else current_frame.copy()
                    )
                    stable_count = 0
                    
                except Exception as error:
                    print(f"[ERROR] 자동 분석 실패: {error}")

    finally:
        if keyboard is not None:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        cv2.destroyAllWindows()
        print("\n프로그램을 종료했습니다.")


if __name__ == "__main__":
    main()
