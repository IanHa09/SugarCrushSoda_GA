"""플레이 모드에서 한 프레임을 분석하고 행동 결과까지 기록합니다."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from openai import OpenAI

from action_executor import execute_decision
from agent import analyze_screen, validate_decision
from capture import frame_difference
from capture_modes import CaptureBundle, capture_configured
from config import (
    ACTION_ACCEPTED_THRESHOLD,
    ACTION_ATTEMPT_THRESHOLD,
    ACTION_RETURNED_THRESHOLD,
    BOARD_FINGERPRINT_MAX_DISTANCE,
    CAPTURE_MODE,
    COLS,
    DRY_RUN,
    GRID_MAX_COLS,
    GRID_MAX_ROWS,
    GRID_MIN_COLS,
    GRID_MIN_ROWS,
    MEMORY_CONTEXT_LIMIT,
    MEMORY_SCAN_LIMIT,
    POST_ACTION_INTERVAL,
    POST_ACTION_MIN_WAIT,
    POST_ACTION_STABLE_FRAMES,
    POST_ACTION_TIMEOUT,
    ROWS,
    STABLE_THRESHOLD,
    CELL_SIZE_PX,
)
from grid_detector import detect_board_geometry
from image_utils import add_grid_overlay, board_fingerprint
from reward import ActionObservation, classify_action_outcome, evaluate_reward
from storage import (
    load_failed_moves_for_board,
    save_image,
    save_memory_entry,
    save_run,
)


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
        final_bundle = capture_configured(sct)
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
    cancel_check: Callable[[], str | None] | None = None,
) -> str:
    """화면을 분석하고 행동 결과까지 메모리에 기록합니다."""

    geometry = detect_board_geometry(
        bundle.board_image,
        cell_size=CELL_SIZE_PX,
        fallback_rows=ROWS,
        fallback_cols=COLS,
        min_rows=GRID_MIN_ROWS,
        max_rows=GRID_MAX_ROWS,
        min_cols=GRID_MIN_COLS,
        max_cols=GRID_MAX_COLS,
    )
    detail = f", reason={geometry.reason}" if geometry.reason else ""
    print(
        f"[GRID] rows={geometry.rows}, cols={geometry.cols}, "
        f"rect=({geometry.left},{geometry.top}) "
        f"{geometry.width}x{geometry.height}, source={geometry.source}{detail}"
    )

    # 캡처 영역(ROI)이 아니라 실제 보드만 잘라냅니다.
    board_image = bundle.board_image[
        geometry.top : geometry.top + geometry.height,
        geometry.left : geometry.left + geometry.width,
    ]
    # 클릭 좌표도 같은 사각형을 기준으로 계산해야 어긋나지 않습니다.
    board_region = {
        "left": bundle.board_region["left"] + geometry.left,
        "top": bundle.board_region["top"] + geometry.top,
        "width": geometry.width,
        "height": geometry.height,
    }

    fingerprint = board_fingerprint(board_image)
    failed_memories, forbidden_moves = load_failed_moves_for_board(
        fingerprint,
        geometry.rows,
        geometry.cols,
        scan_limit=MEMORY_SCAN_LIMIT,
        context_limit=MEMORY_CONTEXT_LIMIT,
        max_distance=BOARD_FINGERPRINT_MAX_DISTANCE,
    )
    if forbidden_moves:
        print(f"[MEMORY] 같은 보드의 실패한 swap {len(forbidden_moves)}개 제외")

    grid_board = add_grid_overlay(board_image, geometry.rows, geometry.cols)
    print("\n[REQUEST] 전체 게임 화면과 보드를 LLM에 전송합니다.")
    decision = analyze_screen(
        client,
        bundle.full_image,
        grid_board,
        memory_context=failed_memories,
        rows=geometry.rows,
        cols=geometry.cols,
    )
    is_valid, message = validate_decision(
        decision,
        rows=geometry.rows,
        cols=geometry.cols,
        forbidden_moves=forbidden_moves,
        geometry_ok=geometry.source == "auto",
    )
    print_decision(decision, is_valid, message)
    if decision.action == "wait" and decision.detected_ui_state != "playing":
        print(
            "[MODE HINT] 현재 화면은 플레이 가능한 보드가 아닙니다. "
            "게임 구조 탐색은 --autodrive로 실행하세요."
        )

    grid_record = {
        "rows": geometry.rows,
        "cols": geometry.cols,
        "source": geometry.source,
        "left": geometry.left,
        "top": geometry.top,
        "width": geometry.width,
        "height": geometry.height,
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
                board_region,
                dry_run=DRY_RUN,
                rows=geometry.rows,
                cols=geometry.cols,
                cancel_check=cancel_check,
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
            add_grid_overlay(after_bundle.board_image, geometry.rows, geometry.cols),
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
