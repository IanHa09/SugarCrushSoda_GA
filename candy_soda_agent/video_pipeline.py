"""영상에서 행동 경험을 추출하거나 예측 정확도를 평가."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
from openai import OpenAI

from agent import (
    analyze_board,
    label_video_outcome,
    validate_decision,
)
from config import (
    CACHED_INPUT_USD_PER_MILLION_TOKENS,
    COLS,
    INPUT_USD_PER_MILLION_TOKENS,
    MAX_EVALUATION_ATTEMPTS,
    MAX_EVALUATION_EVENTS,
    MAX_TRAIN_ATTEMPTS,
    MAX_TRAIN_EVENTS,
    MAX_VIDEO_API_CALLS,
    MAX_VIDEO_DIAGNOSTIC_EVENTS,
    MAX_VIDEO_TOTAL_TOKENS,
    MODEL,
    OUTPUT_USD_PER_MILLION_TOKENS,
    ROWS,
    VIDEO_ACTION_WINDOW_SECONDS,
    VIDEO_AFTER_STABLE_SAMPLES,
    VIDEO_BOARD_ROI,
    VIDEO_CAPTURE_DIR,
    VIDEO_CELL_ACTIVE_THRESHOLD,
    VIDEO_CELL_MARGIN_RATIO,
    VIDEO_DEFAULT_START_SECONDS,
    VIDEO_DISCARD_PATH,
    VIDEO_EVALUATION_PATH,
    VIDEO_EVENT_COOLDOWN_SECONDS,
    VIDEO_EXPERIENCE_PATH,
    VIDEO_MAX_ACTIVE_CELLS,
    VIDEO_MAX_EVENT_SECONDS,
    VIDEO_MAX_SWAP_GLOBAL_DIFFERENCE,
    VIDEO_MIN_PAIR_DOMINANCE,
    VIDEO_MIN_PAIR_SUM,
    VIDEO_MIN_SECOND_CELL_CHANGE,
    VIDEO_PATH,
    VIDEO_PRE_ACTION_QUIET_SAMPLES,
    VIDEO_QUIET_THRESHOLD,
    VIDEO_SAMPLE_SECONDS,
    VIDEO_SCORE_ROI,
    VIDEO_SWAP_CONFIRM_REQUIRED_SAMPLES,
    VIDEO_SWAP_CONFIRM_WINDOW_SAMPLES,
    VIDEO_USAGE_PATH,
)
from image_utils import (
    add_grid_overlay,
    crop_roi,
    make_swap_zoom,
    make_video_observation,
)
from memory import build_memory_prompt
from schemas import Cell, VideoTransitionLabel
from usage import (
    ApiBudgetExceeded,
    ApiUsage,
    UsageBudget,
)
from video_detection import (
    DetectedVideoEvent,
    RejectedVideoEvent,
    SwapCandidate,
    VideoSwapDetector,
)


def append_jsonl(
    path: Path,
    record: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
            + "\n"
        )


def normalized_swap(source, target) -> tuple:
    """A↔B와 B↔A를 동일한 행동으로 처리."""

    return tuple(
        sorted(
            [
                (
                    int(source.row),
                    int(source.col),
                ),
                (
                    int(target.row),
                    int(target.col),
                ),
            ]
        )
    )


def actions_match(prediction, label) -> bool:
    if (
        prediction.action != "swap"
        or label.actual_action != "swap"
    ):
        return False

    if (
        prediction.source is None
        or prediction.target is None
        or label.source is None
        or label.target is None
    ):
        return False

    return normalized_swap(
        prediction.source,
        prediction.target,
    ) == normalized_swap(
        label.source,
        label.target,
    )


def candidate_cells(
    candidate: SwapCandidate,
) -> tuple[Cell, Cell]:
    return (
        Cell(
            row=candidate.source_row,
            col=candidate.source_col,
        ),
        Cell(
            row=candidate.target_row,
            col=candidate.target_col,
        ),
    )


def validate_roi_bounds(
    name: str,
    roi: dict[str, int],
    frame_width: int,
    frame_height: int,
) -> None:
    required = {
        "left",
        "top",
        "width",
        "height",
    }
    missing = required.difference(roi)
    if missing:
        raise ValueError(
            f"{name}에 필요한 키가 없습니다: "
            + ", ".join(sorted(missing))
        )

    left = roi["left"]
    top = roi["top"]
    width = roi["width"]
    height = roi["height"]

    if min(left, top) < 0 or width <= 0 or height <= 0:
        raise ValueError(f"{name} 값이 올바르지 않습니다: {roi}")

    if (
        left + width > frame_width
        or top + height > frame_height
    ):
        raise ValueError(
            f"{name}이 영상 범위를 벗어났습니다. "
            f"video={frame_width}x{frame_height}, roi={roi}. "
            "calibrate_video.py로 다시 선택하세요."
        )


def save_event_images(
    event_id: str,
    before,
    action,
    after,
) -> dict[str, str]:
    VIDEO_CAPTURE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    paths = {
        "before": str(
            VIDEO_CAPTURE_DIR
            / f"{event_id}_before.png"
        ),
        "action": str(
            VIDEO_CAPTURE_DIR
            / f"{event_id}_action.png"
        ),
        "after": str(
            VIDEO_CAPTURE_DIR
            / f"{event_id}_after.png"
        ),
    }

    for key, image in (
        ("before", before),
        ("action", action),
        ("after", after),
    ):
        if not cv2.imwrite(paths[key], image):
            raise RuntimeError(
                f"이벤트 이미지 저장 실패: {paths[key]}"
            )

    return paths


def usage_cost(usage: ApiUsage) -> float:
    return usage.estimated_cost_usd(
        INPUT_USD_PER_MILLION_TOKENS,
        OUTPUT_USD_PER_MILLION_TOKENS,
        CACHED_INPUT_USD_PER_MILLION_TOKENS,
    )


def save_discard_record(
    *,
    event_id: str,
    mode: str,
    timestamp: float,
    action_timestamp: float,
    experiment_label: str,
    discard_code: str,
    discard_message: str,
    candidate: SwapCandidate | None,
    paths: dict[str, str],
    api_calls: int,
    api_usage: ApiUsage,
    label=None,
    prediction=None,
    error: Exception | None = None,
) -> None:
    record: dict[str, Any] = {
        "record_type": "video_discard",
        "event_id": event_id,
        "mode": mode,
        "video_timestamp_seconds": timestamp,
        "action_timestamp_seconds": action_timestamp,
        "experiment_label": experiment_label,
        "discard_code": discard_code,
        "discard_message": discard_message,
        "local_candidate": (
            candidate.to_dict()
            if candidate is not None
            else None
        ),
        "images": paths,
        "api_calls": api_calls,
        "api_usage": api_usage.to_dict(),
        "estimated_cost_usd": usage_cost(api_usage),
    }

    if label is not None:
        record["model_label"] = label.model_dump()
    if prediction is not None:
        record["prediction"] = prediction.model_dump()
    if error is not None:
        record["error_type"] = type(error).__name__
        record["error_message"] = str(error)

    append_jsonl(
        VIDEO_DISCARD_PATH,
        record,
    )


def print_budget(budget: UsageBudget) -> None:
    cost = usage_cost(budget.usage)
    print(
        f"[VIDEO API] calls={budget.calls}/"
        f"{budget.max_calls}, "
        f"tokens={budget.usage.total_tokens:,}/"
        f"{budget.max_total_tokens:,}, "
        f"estimated_cost=${cost:.4f}"
    )


def process_event(
    client: OpenAI,
    budget: UsageBudget,
    mode: str,
    before_board,
    before_score,
    action_board,
    action_score,
    after_board,
    after_score,
    timestamp: float,
    action_timestamp: float,
    candidate: SwapCandidate,
    memory_limit: int | None,
    experiment_label: str,
) -> bool:
    """한 행동 구간을 학습 또는 평가 데이터로 저장."""

    before_observation = make_video_observation(
        before_board,
        before_score,
        ROWS,
        COLS,
    )
    action_observation = make_swap_zoom(
        before_board,
        action_board,
        candidate.source_row,
        candidate.source_col,
        candidate.target_row,
        candidate.target_col,
        ROWS,
        COLS,
    )
    after_observation = make_video_observation(
        after_board,
        after_score,
        ROWS,
        COLS,
    )

    event_id = uuid.uuid4().hex
    paths = save_event_images(
        event_id,
        before_observation,
        action_observation,
        after_observation,
    )

    prediction = None
    outcome = None
    label = None
    memory_size = 0
    event_usage = ApiUsage()
    calls_before_event = budget.calls
    candidate_source, candidate_target = (
        candidate_cells(candidate)
    )

    def discard(
        code: str,
        message: str,
        *,
        error: Exception | None = None,
    ) -> bool:
        save_discard_record(
            event_id=event_id,
            mode=mode,
            timestamp=timestamp,
            action_timestamp=action_timestamp,
            experiment_label=experiment_label,
            discard_code=code,
            discard_message=message,
            candidate=candidate,
            paths=paths,
            api_calls=(
                budget.calls - calls_before_event
            ),
            api_usage=event_usage,
            label=label,
            prediction=prediction,
            error=error,
        )
        print(
            f"[DISCARDED] t={timestamp:.2f}s: "
            f"{message} ({code})"
        )
        return False

    try:
        if mode == "evaluate":
            memory_prompt, memory_size = (
                build_memory_prompt(memory_limit)
            )
            prediction_image = add_grid_overlay(
                before_board,
                ROWS,
                COLS,
            )

            budget.start_call()
            prediction, usage = analyze_board(
                client,
                prediction_image,
                memory_prompt,
            )
            budget.add_usage(usage)
            event_usage = event_usage + usage
            print_budget(budget)

        budget.start_call()
        outcome, usage = label_video_outcome(
            client,
            before_observation,
            action_observation,
            after_observation,
            candidate_source,
            candidate_target,
        )
        budget.add_usage(usage)
        event_usage = event_usage + usage
        print_budget(budget)

        label = VideoTransitionLabel(
            action_observable=True,
            actual_action="swap",
            source=candidate_source,
            target=candidate_target,
            move_type=outcome.move_type,
            board_summary=outcome.board_summary,
            score_before=outcome.score_before,
            score_after=outcome.score_after,
            confidence=outcome.confidence,
            reason=outcome.reason,
        )
    except ApiBudgetExceeded as error:
        discard(
            "api_budget_exceeded",
            str(error),
            error=error,
        )
        raise
    except Exception as error:
        discard(
            "api_error",
            "API 요청 또는 응답 처리에 실패했습니다.",
            error=error,
        )
        raise

    assert label.source is not None
    assert label.target is not None

    score_delta = None
    reward_valid = False

    if (
        label.score_before is not None
        and label.score_after is not None
    ):
        score_delta = (
            label.score_after - label.score_before
        )
        reward_valid = score_delta >= 0

    common_record = {
        "event_id": event_id,
        "video_timestamp_seconds": timestamp,
        "action_timestamp_seconds": action_timestamp,
        "experiment_label": experiment_label,
        "local_candidate": candidate.to_dict(),
        "action_source": "local_detector",
        "actual_action": {
            "source": label.source.model_dump(),
            "target": label.target.model_dump(),
        },
        "move_type": label.move_type,
        "board_summary": label.board_summary,
        "score_before": label.score_before,
        "score_after": label.score_after,
        "score_delta": score_delta,
        "reward_valid": reward_valid,
        "label_confidence": label.confidence,
        "outcome_confidence": label.confidence,
        "reason": label.reason,
        "images": paths,
        "api_calls": (
            budget.calls - calls_before_event
        ),
        "api_usage": event_usage.to_dict(),
        "estimated_cost_usd": usage_cost(
            event_usage
        ),
    }

    if mode == "train":
        append_jsonl(
            VIDEO_EXPERIENCE_PATH,
            {
                "record_type": "video_training",
                **common_record,
            },
        )
    else:
        assert prediction is not None

        prediction_valid, validation_message = (
            validate_decision(prediction)
        )
        append_jsonl(
            VIDEO_EVALUATION_PATH,
            {
                "record_type": "video_evaluation",
                **common_record,
                "memory_limit": memory_limit,
                "memory_size": memory_size,
                "prediction": prediction.model_dump(),
                "prediction_valid": prediction_valid,
                "validation_message": (
                    validation_message
                ),
                "prediction_matches_actual": (
                    actions_match(
                        prediction,
                        label,
                    )
                ),
            },
        )

    print(
        f"[SAVED] t={timestamp:.2f}s, "
        f"type={label.move_type}, "
        f"score_delta={score_delta}"
    )
    return True


def save_detector_rejection(
    event: RejectedVideoEvent,
    *,
    mode: str,
    experiment_label: str,
) -> None:
    before_observation = make_video_observation(
        event.before_board,
        event.before_score,
        ROWS,
        COLS,
    )
    action_observation = make_video_observation(
        event.action_board,
        event.action_score,
        ROWS,
        COLS,
    )
    after_observation = make_video_observation(
        event.after_board,
        event.after_score,
        ROWS,
        COLS,
    )
    event_id = uuid.uuid4().hex
    paths = save_event_images(
        event_id,
        before_observation,
        action_observation,
        after_observation,
    )
    save_discard_record(
        event_id=event_id,
        mode=mode,
        timestamp=event.event_timestamp,
        action_timestamp=event.action_timestamp,
        experiment_label=experiment_label,
        discard_code=event.discard_code,
        discard_message=(
            "로컬 swap 후보가 확인 조건을 통과하지 못했습니다."
        ),
        candidate=event.candidate,
        paths=paths,
        api_calls=0,
        api_usage=ApiUsage(),
    )


def run_video(
    mode: str,
    video_path: str,
    start_seconds: float,
    end_seconds: float | None,
    memory_limit: int | None,
    experiment_label: str,
    max_events_override: int | None = None,
    max_attempts_override: int | None = None,
) -> None:
    if mode not in {"train", "evaluate"}:
        raise ValueError(
            "mode는 train 또는 evaluate여야 합니다."
        )
    if start_seconds < 0:
        raise ValueError("start는 0 이상이어야 합니다.")
    if (
        end_seconds is not None
        and end_seconds <= start_seconds
    ):
        raise ValueError(
            "end는 start보다 커야 합니다."
        )
    if memory_limit is not None and memory_limit < 0:
        raise ValueError(
            "memory-limit은 0 이상이어야 합니다."
        )
    if (
        max_events_override is not None
        and max_events_override <= 0
    ):
        raise ValueError(
            "max-events는 1 이상이어야 합니다."
        )
    if (
        max_attempts_override is not None
        and max_attempts_override <= 0
    ):
        raise ValueError(
            "max-attempts는 1 이상이어야 합니다."
        )
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            ".env 파일 또는 환경변수를 확인하세요."
        )

    client = OpenAI()
    budget = UsageBudget(
        max_calls=MAX_VIDEO_API_CALLS,
        max_total_tokens=MAX_VIDEO_TOTAL_TOKENS,
    )
    capture = cv2.VideoCapture(video_path)

    if not capture.isOpened():
        raise RuntimeError(
            f"영상을 열 수 없습니다: {video_path}"
        )

    started_at = datetime.now().isoformat()
    saved_events = 0
    detected_events = 0
    attempted_events = 0
    locally_rejected_events = 0
    diagnostic_events = 0
    stop_reason = "video_ended"
    default_max_events = (
        MAX_TRAIN_EVENTS
        if mode == "train"
        else MAX_EVALUATION_EVENTS
    )
    max_events = (
        max_events_override
        if max_events_override is not None
        else default_max_events
    )
    default_max_attempts = (
        MAX_TRAIN_ATTEMPTS
        if mode == "train"
        else MAX_EVALUATION_ATTEMPTS
    )
    max_attempts = (
        max_attempts_override
        if max_attempts_override is not None
        else default_max_attempts
    )

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = int(
            capture.get(cv2.CAP_PROP_FRAME_COUNT)
        )
        frame_width = int(
            capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        )
        frame_height = int(
            capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        )

        if fps <= 0:
            raise RuntimeError(
                "영상 FPS를 읽지 못했습니다."
            )

        validate_roi_bounds(
            "VIDEO_BOARD_ROI",
            VIDEO_BOARD_ROI,
            frame_width,
            frame_height,
        )
        validate_roi_bounds(
            "VIDEO_SCORE_ROI",
            VIDEO_SCORE_ROI,
            frame_width,
            frame_height,
        )
        if (
            VIDEO_SCORE_ROI["height"]
            > VIDEO_SCORE_ROI["width"] * 1.5
        ):
            print(
                "[WARNING] VIDEO_SCORE_ROI가 세로로 너무 깁니다. "
                "현재 점수 숫자만 더 촘촘하게 선택해야 "
                "점수 오독을 줄일 수 있습니다."
            )

        sample_step = max(
            1,
            round(fps * VIDEO_SAMPLE_SECONDS),
        )
        start_frame = round(start_seconds * fps)

        if start_frame >= frame_count:
            raise ValueError(
                "start가 영상 길이를 벗어났습니다."
            )

        capture.set(
            cv2.CAP_PROP_POS_FRAMES,
            start_frame,
        )

        detector = VideoSwapDetector(
            rows=ROWS,
            cols=COLS,
            quiet_threshold=VIDEO_QUIET_THRESHOLD,
            pre_action_quiet_samples=(
                VIDEO_PRE_ACTION_QUIET_SAMPLES
            ),
            confirm_window_samples=(
                VIDEO_SWAP_CONFIRM_WINDOW_SAMPLES
            ),
            confirm_required_samples=(
                VIDEO_SWAP_CONFIRM_REQUIRED_SAMPLES
            ),
            action_window_seconds=(
                VIDEO_ACTION_WINDOW_SECONDS
            ),
            after_stable_samples=(
                VIDEO_AFTER_STABLE_SAMPLES
            ),
            max_event_seconds=(
                VIDEO_MAX_EVENT_SECONDS
            ),
            event_cooldown_seconds=(
                VIDEO_EVENT_COOLDOWN_SECONDS
            ),
            cell_margin_ratio=(
                VIDEO_CELL_MARGIN_RATIO
            ),
            cell_active_threshold=(
                VIDEO_CELL_ACTIVE_THRESHOLD
            ),
            min_pair_sum=VIDEO_MIN_PAIR_SUM,
            min_second_cell_change=(
                VIDEO_MIN_SECOND_CELL_CHANGE
            ),
            min_pair_dominance=(
                VIDEO_MIN_PAIR_DOMINANCE
            ),
            max_active_cells=(
                VIDEO_MAX_ACTIVE_CELLS
            ),
            max_swap_global_difference=(
                VIDEO_MAX_SWAP_GLOBAL_DIFFERENCE
            ),
        )
        frame_index = start_frame

        print(
            f"[VIDEO DETECTOR] sample="
            f"{VIDEO_SAMPLE_SECONDS:.3f}s, "
            f"max_events={max_events}, "
            f"max_attempts={max_attempts}"
        )

        while (
            saved_events < max_events
            and attempted_events < max_attempts
        ):
            if budget.exhausted:
                stop_reason = "api_budget_exhausted"
                break

            success, frame = capture.read()
            if not success:
                stop_reason = "video_ended"
                break

            current_index = frame_index
            frame_index += 1

            if (
                current_index - start_frame
            ) % sample_step != 0:
                continue

            timestamp = current_index / fps
            if (
                end_seconds is not None
                and timestamp > end_seconds
            ):
                stop_reason = "end_time_reached"
                break

            board = crop_roi(
                frame,
                VIDEO_BOARD_ROI,
            )
            score = crop_roi(
                frame,
                VIDEO_SCORE_ROI,
            )

            detector_result = detector.update(
                timestamp,
                board,
                score,
            )
            if detector_result is None:
                continue

            detected_events += 1

            if isinstance(
                detector_result,
                RejectedVideoEvent,
            ):
                locally_rejected_events += 1
                print(
                    f"[LOCAL SKIP] "
                    f"t={detector_result.event_timestamp:.2f}s, "
                    f"reason={detector_result.discard_code}, "
                    "API calls=0"
                )
                if (
                    diagnostic_events
                    < MAX_VIDEO_DIAGNOSTIC_EVENTS
                ):
                    save_detector_rejection(
                        detector_result,
                        mode=mode,
                        experiment_label=(
                            experiment_label
                        ),
                    )
                    diagnostic_events += 1
                continue

            assert isinstance(
                detector_result,
                DetectedVideoEvent,
            )
            attempted_events += 1
            source, target = candidate_cells(
                detector_result.candidate
            )
            print(
                f"[LOCAL SWAP] "
                f"t={detector_result.event_timestamp:.2f}s, "
                f"({source.row},{source.col})"
                f"↔({target.row},{target.col}), "
                f"attempt={attempted_events}/"
                f"{max_attempts}"
            )

            budget_exceeded = False
            try:
                stored = process_event(
                    client=client,
                    budget=budget,
                    mode=mode,
                    before_board=(
                        detector_result.before_board
                    ),
                    before_score=(
                        detector_result.before_score
                    ),
                    action_board=(
                        detector_result.action_board
                    ),
                    action_score=(
                        detector_result.action_score
                    ),
                    after_board=(
                        detector_result.after_board
                    ),
                    after_score=(
                        detector_result.after_score
                    ),
                    timestamp=(
                        detector_result.event_timestamp
                    ),
                    action_timestamp=(
                        detector_result.action_timestamp
                    ),
                    candidate=(
                        detector_result.candidate
                    ),
                    memory_limit=memory_limit,
                    experiment_label=(
                        experiment_label
                    ),
                )
                if stored:
                    saved_events += 1
            except ApiBudgetExceeded as error:
                print(f"[LIMIT] {error}")
                stop_reason = (
                    "api_budget_exhausted"
                )
                budget_exceeded = True
            except Exception as error:
                print(
                    f"[EVENT ERROR] "
                    f"t={detector_result.event_timestamp:.2f}: "
                    f"{error}"
                )

            if budget_exceeded:
                break

        if saved_events >= max_events:
            stop_reason = "max_events_reached"
        elif (
            attempted_events >= max_attempts
            and stop_reason == "video_ended"
        ):
            stop_reason = "max_attempts_reached"

    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
        print(
            "\n[STOP] 사용자 요청으로 영상 처리를 중단했습니다."
        )
    except Exception:
        stop_reason = "error"
        raise
    finally:
        capture.release()

        cost = usage_cost(budget.usage)
        append_jsonl(
            VIDEO_USAGE_PATH,
            {
                "record_type": "video_run_usage",
                "started_at": started_at,
                "finished_at": (
                    datetime.now().isoformat()
                ),
                "mode": mode,
                "model": MODEL,
                "video_path": video_path,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "experiment_label": experiment_label,
                "detected_events": detected_events,
                "attempted_events": attempted_events,
                "locally_rejected_events": (
                    locally_rejected_events
                ),
                "diagnostic_events": diagnostic_events,
                "saved_events": saved_events,
                "max_events": max_events,
                "max_attempts": max_attempts,
                "stop_reason": stop_reason,
                "budget": budget.to_dict(),
                "estimated_cost_usd": cost,
            },
        )

    print_budget(budget)
    print(
        f"\n완료: 감지 {detected_events}개, "
        f"API 시도 {attempted_events}개, "
        f"유효 경험 {saved_events}개 저장, "
        f"종료 사유={stop_reason}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["train", "evaluate"],
        required=True,
    )
    parser.add_argument(
        "--video",
        default=VIDEO_PATH,
    )
    parser.add_argument(
        "--start",
        type=float,
        default=VIDEO_DEFAULT_START_SECONDS,
    )
    parser.add_argument(
        "--end",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--memory-limit",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help=(
            "API로 보낼 확인된 swap 후보의 최대 수 "
            "(저장 성공 수와 별도)"
        ),
    )
    parser.add_argument(
        "--label",
        default="default",
    )
    arguments = parser.parse_args()

    run_video(
        mode=arguments.mode,
        video_path=arguments.video,
        start_seconds=arguments.start,
        end_seconds=arguments.end,
        memory_limit=arguments.memory_limit,
        experiment_label=arguments.label,
        max_events_override=arguments.max_events,
        max_attempts_override=arguments.max_attempts,
    )


if __name__ == "__main__":
    main()
