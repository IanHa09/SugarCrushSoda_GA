"""Candy Crush Soda 화면 분석 에이전트의 실행 루프입니다."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, SimpleQueue
from uuid import uuid4

import cv2
import keyboard
import mss
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

from action_executor import execute_decision
from agent import analyze_screen, validate_decision
from capture import frame_difference, make_absolute_region, print_monitors
from capture_modes import CaptureBundle, capture_bundle, validate_capture_settings
from config import (
    ACTION_SUCCESS_THRESHOLD,
    API_COOLDOWN,
    API_TIMEOUT,
    BOARD_OFFSET,
    CAPTURE_INTERVAL,
    CAPTURE_MODE,
    COLS,
    DRY_RUN,
    GAME_WINDOW_TITLE,
    MAX_API_CALLS,
    MAX_CONSECUTIVE_ERRORS,
    MEMORY_CONTEXT_LIMIT,
    MODEL,
    MONITOR_INDEX,
    NEW_BOARD_THRESHOLD,
    POST_ACTION_WAIT,
    PRE_ACTION_MAX_CHANGE,
    PRE_ACTION_STABILITY_DELAY,
    ROWS,
    STABLE_FRAME_COUNT,
    STABLE_THRESHOLD,
    STORE_FULL_CAPTURE,
    WINDOW_OFFSET,
    validate_live_action_settings,
)
from image_utils import add_grid_overlay
from reward import RewardResult, evaluate_reward
from safety_guard import (
    cancellation_reason,
    make_cancel_check,
    validate_fresh_board,
)
from storage import (
    append_session_event,
    finish_session,
    load_recent_memories,
    save_image,
    save_memory_entry,
    save_run,
    start_session,
)


AUTO_RUNNING = threading.Event()
ANALYZE_ONCE = threading.Event()
EXIT_REQUESTED = threading.Event()
AUTO_STATE_EVENTS: SimpleQueue[str] = SimpleQueue()


@dataclass
class SessionStats:
    api_call_count: int = 0
    memory_step_count: int = 0
    consecutive_errors: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    blocked_actions: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    action: str
    executed: bool
    blocked: bool
    execution_error: str | None
    reward: RewardResult


class ApiLimitReached(RuntimeError):
    pass


def toggle_auto_mode() -> None:
    if AUTO_RUNNING.is_set():
        AUTO_RUNNING.clear()
        AUTO_STATE_EVENTS.put("auto_stopped_by_user")
        print("\n[AUTO STOP] 자동 분석을 중지했습니다.")
    else:
        AUTO_RUNNING.set()
        AUTO_STATE_EVENTS.put("auto_started")
        print("\n[AUTO START] 자동 분석을 시작했습니다.")


def print_decision(decision, is_valid: bool, message: str) -> None:
    print("\n[LLM 응답]")
    print(json.dumps(decision.model_dump(), ensure_ascii=False, indent=2))
    print(f"[검증] valid={is_valid}, message={message}")


def _capture_current(sct) -> CaptureBundle:
    return capture_bundle(
        sct=sct,
        mode=CAPTURE_MODE,
        monitor_index=MONITOR_INDEX,
        board_offset=BOARD_OFFSET,
        window_offset=WINDOW_OFFSET,
    )


def _wait_with_cancellation(duration: float, mode: str) -> str | None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        reason = cancellation_reason(mode, EXIT_REQUESTED, AUTO_RUNNING)
        if reason:
            return reason
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return cancellation_reason(mode, EXIT_REQUESTED, AUTO_RUNNING)


def _decision_dict(decision) -> dict:
    return {
        "action": decision.action,
        "source": decision.source.model_dump() if decision.source else None,
        "target": decision.target.model_dump() if decision.target else None,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "memory_notes": decision.memory_notes,
    }


def process_frame(
    client: OpenAI,
    sct,
    bundle: CaptureBundle,
    *,
    session_id: str,
    step: int,
    mode: str,
) -> ProcessResult:
    """분석부터 행동 후 보상 기록까지 한 번의 원자적인 단계로 처리합니다."""

    grid_board = add_grid_overlay(bundle.board_image, ROWS, COLS)
    memories = load_recent_memories(MEMORY_CONTEXT_LIMIT)
    print("\n[REQUEST] 제한된 게임 UI와 보드를 LLM에 전송합니다.")
    decision = analyze_screen(
        client,
        bundle.full_image,
        grid_board,
        memory_context=memories,
    )
    is_valid, validation_message = validate_decision(decision)
    print_decision(decision, is_valid, validation_message)

    stored_image = bundle.full_image if STORE_FULL_CAPTURE else bundle.board_image
    image_scope = "full_capture" if STORE_FULL_CAPTURE else "board_only"
    run_record = save_run(
        stored_image,
        grid_board,
        decision,
        is_valid,
        validation_message,
        session_id=session_id,
        step=step,
        stored_image_scope=image_scope,
    )

    attempted = False
    executed = False
    blocked_reason = validation_message if not is_valid else None
    execution_error: str | None = None
    screen_change_score: float | None = None
    after_raw_path: str | None = None
    after_grid_path: str | None = None
    pre_action_change: float | None = None
    pre_action_stability: float | None = None

    if is_valid and decision.action == "swap":
        blocked_reason = cancellation_reason(mode, EXIT_REQUESTED, AUTO_RUNNING)

        if blocked_reason is None:
            latest_first = _capture_current(sct)
            blocked_reason = _wait_with_cancellation(
                PRE_ACTION_STABILITY_DELAY,
                mode,
            )

        if blocked_reason is None:
            latest_second = _capture_current(sct)
            pre_action_change = frame_difference(
                bundle.board_image,
                latest_second.board_image,
            )
            pre_action_stability = frame_difference(
                latest_first.board_image,
                latest_second.board_image,
            )
            fresh, freshness_message = validate_fresh_board(
                pre_action_change,
                pre_action_stability,
                PRE_ACTION_MAX_CHANGE,
            )
            if not fresh:
                blocked_reason = freshness_message

        if blocked_reason is None:
            attempted = True
            try:
                action_result = execute_decision(
                    decision,
                    latest_second.board_region,
                    dry_run=DRY_RUN,
                    expected_window_title=GAME_WINDOW_TITLE,
                    cancel_check=make_cancel_check(
                        mode,
                        EXIT_REQUESTED,
                        AUTO_RUNNING,
                    ),
                )
                executed = action_result and not DRY_RUN
            except RuntimeError as error:
                blocked_reason = str(error)
            except Exception as error:
                execution_error = str(error)

        if executed:
            time.sleep(POST_ACTION_WAIT)
            try:
                after_bundle = _capture_current(sct)
                screen_change_score = frame_difference(
                    latest_second.board_image,
                    after_bundle.board_image,
                )
                after_raw_path = save_image(after_bundle.board_image, "after_raw")
                after_grid = add_grid_overlay(after_bundle.board_image, ROWS, COLS)
                after_grid_path = save_image(after_grid, "after_grid")
            except Exception as error:
                execution_error = f"행동 후 캡처 실패: {error}"

    reward = evaluate_reward(
        action=decision.action,
        validation_passed=is_valid,
        executed=executed,
        dry_run=DRY_RUN,
        screen_change_score=screen_change_score,
        success_threshold=ACTION_SUCCESS_THRESHOLD,
        blocked_reason=blocked_reason,
        execution_error=execution_error,
    )

    memory_record = {
        "session_id": session_id,
        "step": step,
        "mode": mode,
        "before": {
            "raw_image": run_record["raw_image"],
            "grid_image": run_record["grid_image"],
            "ui_state": decision.detected_ui_state,
            "board_status": decision.board_status,
            "visible_elements": decision.visible_elements,
            "objectives": decision.objectives,
            "obstacles": decision.obstacles,
            "uncertainties": decision.uncertainties,
        },
        "decision": _decision_dict(decision),
        "execution": {
            "validation_passed": is_valid,
            "attempted": attempted,
            "executed": executed,
            "dry_run": DRY_RUN,
            "blocked_reason": blocked_reason,
            "execution_error": execution_error,
            "pre_action_change": pre_action_change,
            "pre_action_stability": pre_action_stability,
        },
        "after": {
            "raw_image": after_raw_path,
            "grid_image": after_grid_path,
            "screen_change_score": screen_change_score,
            "new_elements": [],
            "removed_elements": [],
            "observed_effects": [],
        },
        "reward": reward.reward,
        "success_estimate": reward.success_estimate,
        "lesson": reward.lesson,
        "failure_reason": reward.failure_reason,
    }
    try:
        save_memory_entry(**memory_record)
    except Exception as error:
        # 행동은 이미 끝났을 수 있으므로 메모리 실패를 재실행 신호로 만들지 않습니다.
        print(f"[WARNING] 메모리 저장 실패: {error}")
        try:
            append_session_event(
                session_id,
                "memory_write_failed",
                step=step,
                error=str(error),
            )
        except Exception:
            pass

    return ProcessResult(
        action=decision.action,
        executed=executed,
        blocked=blocked_reason is not None,
        execution_error=execution_error,
        reward=reward,
    )


def _reserve_api_call(stats: SessionStats) -> int:
    if stats.api_call_count >= MAX_API_CALLS:
        raise ApiLimitReached(f"최대 API 요청 수 {MAX_API_CALLS}회에 도달했습니다.")
    stats.api_call_count += 1
    stats.memory_step_count += 1
    print(f"[API COUNT] {stats.api_call_count}/{MAX_API_CALLS}")
    return stats.memory_step_count


def _record_result(stats: SessionStats, result: ProcessResult) -> bool:
    if result.execution_error:
        stats.consecutive_errors += 1
        stats.failed_actions += 1
        stats.last_error = result.execution_error
        return stats.consecutive_errors >= MAX_CONSECUTIVE_ERRORS

    stats.consecutive_errors = 0
    if result.blocked:
        stats.blocked_actions += 1
    elif result.executed and result.reward.reward > 0:
        stats.successful_actions += 1
    elif result.executed and result.reward.reward < 0:
        stats.failed_actions += 1
    return False


def _record_error(
    session_id: str,
    stats: SessionStats,
    event: str,
    error: Exception,
) -> bool:
    stats.consecutive_errors += 1
    stats.last_error = str(error)
    print(f"[ERROR] {event}: {error}")
    try:
        append_session_event(
            session_id,
            event,
            error=str(error),
            consecutive_errors=stats.consecutive_errors,
        )
    except Exception as log_error:
        print(f"[WARNING] 오류 이벤트 저장 실패: {log_error}")
    return stats.consecutive_errors >= MAX_CONSECUTIVE_ERRORS


def _drain_auto_events(session_id: str) -> None:
    while True:
        try:
            event = AUTO_STATE_EVENTS.get_nowait()
        except Empty:
            return
        append_session_event(session_id, event)


def _reset_runtime_events() -> None:
    AUTO_RUNNING.clear()
    ANALYZE_ONCE.clear()
    EXIT_REQUESTED.clear()
    while True:
        try:
            AUTO_STATE_EVENTS.get_nowait()
        except Empty:
            break


def main() -> int:
    _reset_runtime_events()
    session_id = (
        datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        + "_"
        + uuid4().hex[:8]
    )
    stats = SessionStats()
    stop_reason = "stopped_by_esc"

    settings = {
        "capture_mode": CAPTURE_MODE,
        "monitor_index": MONITOR_INDEX,
        "dry_run": DRY_RUN,
        "store_full_capture": STORE_FULL_CAPTURE,
        "max_api_calls": MAX_API_CALLS,
    }
    start_session(session_id, settings)

    try:
        validate_live_action_settings()
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

        client = OpenAI(timeout=API_TIMEOUT, max_retries=2)
        keyboard.add_hotkey("f8", ANALYZE_ONCE.set)
        keyboard.add_hotkey("f9", toggle_auto_mode)
        keyboard.add_hotkey("esc", EXIT_REQUESTED.set)

        print("F8  : 현재 화면 한 번 분석")
        print("F9  : 자동 분석 시작/중지")
        print("ESC : 프로그램 종료")
        print(f"MODEL: {MODEL}")
        print(f"ACTION MODE: {'DRY RUN' if DRY_RUN else 'LIVE'}")

        previous_frame: np.ndarray | None = None
        last_sent_frame: np.ndarray | None = None
        stable_count = 0
        last_capture_time = 0.0
        last_api_time = 0.0

        with mss.MSS() as sct:
            print_monitors(sct)
            validate_capture_settings(
                sct,
                CAPTURE_MODE,
                MONITOR_INDEX,
                BOARD_OFFSET,
                WINDOW_OFFSET,
            )
            board_region = make_absolute_region(sct, MONITOR_INDEX, BOARD_OFFSET)
            print(f"\n게임 보드 절대 좌표: {board_region}")
            if CAPTURE_MODE == "monitor":
                print("[PRIVACY WARNING] 선택한 모니터 전체가 API로 전송됩니다.")

            while not EXIT_REQUESTED.is_set():
                cv2.waitKey(1)
                _drain_auto_events(session_id)

                if ANALYZE_ONCE.is_set():
                    ANALYZE_ONCE.clear()
                    try:
                        bundle = _capture_current(sct)
                        cv2.imshow("Candy Soda capture preview", bundle.full_image)
                        step = _reserve_api_call(stats)
                        last_api_time = time.monotonic()
                        result = process_frame(
                            client,
                            sct,
                            bundle,
                            session_id=session_id,
                            step=step,
                            mode="manual",
                        )
                        if _record_result(stats, result):
                            stop_reason = "stopped_by_repeated_errors"
                            EXIT_REQUESTED.set()
                        last_sent_frame = bundle.board_image.copy()
                    except ApiLimitReached as error:
                        stats.last_error = str(error)
                        stop_reason = "stopped_by_api_limit"
                        EXIT_REQUESTED.set()
                    except Exception as error:
                        if _record_error(session_id, stats, "manual_analysis_failed", error):
                            stop_reason = "stopped_by_repeated_errors"
                            EXIT_REQUESTED.set()

                    if stats.api_call_count >= MAX_API_CALLS:
                        stop_reason = "stopped_by_api_limit"
                        EXIT_REQUESTED.set()
                    continue

                if not AUTO_RUNNING.is_set():
                    time.sleep(0.05)
                    continue

                now = time.monotonic()
                if now - last_capture_time < CAPTURE_INTERVAL:
                    time.sleep(0.01)
                    continue
                last_capture_time = now

                try:
                    bundle = _capture_current(sct)
                except Exception as error:
                    if _record_error(session_id, stats, "capture_failed", error):
                        stop_reason = "stopped_by_repeated_errors"
                        EXIT_REQUESTED.set()
                    time.sleep(0.25)
                    continue

                current_frame = bundle.board_image
                cv2.imshow("Candy Soda capture preview", bundle.full_image)

                if previous_frame is None:
                    previous_frame = current_frame.copy()
                    continue

                change = frame_difference(previous_frame, current_frame)
                previous_frame = current_frame.copy()
                stable_count = stable_count + 1 if change < STABLE_THRESHOLD else 0
                if stable_count < STABLE_FRAME_COUNT:
                    continue

                if last_sent_frame is not None:
                    new_board_change = frame_difference(last_sent_frame, current_frame)
                    if new_board_change < NEW_BOARD_THRESHOLD:
                        continue
                if now - last_api_time < API_COOLDOWN:
                    continue

                try:
                    step = _reserve_api_call(stats)
                    last_api_time = now
                    result = process_frame(
                        client,
                        sct,
                        bundle,
                        session_id=session_id,
                        step=step,
                        mode="auto",
                    )
                    if _record_result(stats, result):
                        stop_reason = "stopped_by_repeated_errors"
                        EXIT_REQUESTED.set()
                    last_sent_frame = current_frame.copy()
                    stable_count = 0
                except ApiLimitReached as error:
                    stats.last_error = str(error)
                    stop_reason = "stopped_by_api_limit"
                    EXIT_REQUESTED.set()
                except Exception as error:
                    if _record_error(session_id, stats, "auto_analysis_failed", error):
                        stop_reason = "stopped_by_repeated_errors"
                        EXIT_REQUESTED.set()

                if stats.api_call_count >= MAX_API_CALLS:
                    stop_reason = "stopped_by_api_limit"
                    EXIT_REQUESTED.set()

    except KeyboardInterrupt:
        stop_reason = "stopped_by_keyboard_interrupt"
    except Exception as error:
        stop_reason = "stopped_by_error"
        stats.last_error = str(error)
        print(f"[FATAL] {error}")
        try:
            append_session_event(session_id, "fatal_error", error=str(error))
        except Exception:
            pass
    finally:
        AUTO_RUNNING.clear()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        try:
            finish_session(
                session_id,
                stop_reason=stop_reason,
                api_call_count=stats.api_call_count,
                successful_actions=stats.successful_actions,
                failed_actions=stats.failed_actions,
                blocked_actions=stats.blocked_actions,
                last_error=stats.last_error,
            )
        except Exception as error:
            print(f"[WARNING] 세션 마감 로그 저장 실패: {error}")
        print(f"\n프로그램을 종료했습니다: {stop_reason}")

    if stop_reason == "stopped_by_keyboard_interrupt":
        return 130
    if stop_reason in {"stopped_by_error", "stopped_by_repeated_errors"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
