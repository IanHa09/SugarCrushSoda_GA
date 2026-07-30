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
    label_video_transition,
    validate_decision,
    validate_transition_label,
)
from capture import frame_difference
from config import (
    CACHED_INPUT_USD_PER_MILLION_TOKENS,
    COLS,
    INPUT_USD_PER_MILLION_TOKENS,
    MAX_EVALUATION_EVENTS,
    MAX_TRAIN_EVENTS,
    MAX_VIDEO_API_CALLS,
    MAX_VIDEO_TOTAL_TOKENS,
    MODEL,
    OUTPUT_USD_PER_MILLION_TOKENS,
    ROWS,
    VIDEO_BOARD_ROI,
    VIDEO_CAPTURE_DIR,
    VIDEO_DEFAULT_START_SECONDS,
    VIDEO_EVALUATION_PATH,
    VIDEO_EVENT_COOLDOWN_SECONDS,
    VIDEO_EXPERIENCE_PATH,
    VIDEO_LABEL_MIN_CONFIDENCE,
    VIDEO_MAX_EVENT_SECONDS,
    VIDEO_MOTION_THRESHOLD,
    VIDEO_PATH,
    VIDEO_SAMPLE_SECONDS,
    VIDEO_SCORE_ROI,
    VIDEO_STABLE_SAMPLES,
    VIDEO_STABLE_THRESHOLD,
    VIDEO_USAGE_PATH,
)
from image_utils import (
    add_grid_overlay,
    crop_roi,
    make_video_observation,
)
from memory import build_memory_prompt
from usage import (
    ApiBudgetExceeded,
    ApiUsage,
    UsageBudget,
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


def print_budget(budget: UsageBudget) -> None:
    cost = budget.usage.estimated_cost_usd(
        INPUT_USD_PER_MILLION_TOKENS,
        OUTPUT_USD_PER_MILLION_TOKENS,
        CACHED_INPUT_USD_PER_MILLION_TOKENS,
    )
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
    action_observation = make_video_observation(
        action_board,
        action_score,
        ROWS,
        COLS,
    )
    after_observation = make_video_observation(
        after_board,
        after_score,
        ROWS,
        COLS,
    )

    prediction = None
    memory_size = 0
    event_usage = ApiUsage()
    calls_before_event = budget.calls

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
    label, usage = label_video_transition(
        client,
        before_observation,
        action_observation,
        after_observation,
    )
    budget.add_usage(usage)
    event_usage = event_usage + usage
    print_budget(budget)

    label_valid, label_message = (
        validate_transition_label(label)
    )
    if not label_valid:
        print(
            f"[DISCARDED] t={timestamp:.2f}s: "
            f"{label_message}"
        )
        return False

    if label.confidence < VIDEO_LABEL_MIN_CONFIDENCE:
        print(
            f"[DISCARDED] t={timestamp:.2f}s: "
            f"confidence={label.confidence:.2f}"
        )
        return False

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

    event_id = uuid.uuid4().hex
    paths = save_event_images(
        event_id,
        before_observation,
        action_observation,
        after_observation,
    )

    common_record = {
        "event_id": event_id,
        "video_timestamp_seconds": timestamp,
        "experiment_label": experiment_label,
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
        "reason": label.reason,
        "images": paths,
        "api_calls": (
            budget.calls - calls_before_event
        ),
        "api_usage": event_usage.to_dict(),
        "estimated_cost_usd": (
            event_usage.estimated_cost_usd(
                INPUT_USD_PER_MILLION_TOKENS,
                OUTPUT_USD_PER_MILLION_TOKENS,
                CACHED_INPUT_USD_PER_MILLION_TOKENS,
            )
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


def run_video(
    mode: str,
    video_path: str,
    start_seconds: float,
    end_seconds: float | None,
    memory_limit: int | None,
    experiment_label: str,
    max_events_override: int | None = None,
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
    stop_reason = "video_ended"

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

        previous_board = None
        last_stable_board = None
        last_stable_score = None
        stable_count = 0
        event_stable_count = 0
        in_event = False
        event_start_time = 0.0
        last_finished_time = -999.0
        before_board = None
        before_score = None
        action_board = None
        action_score = None
        action_max_difference = 0.0
        frame_index = start_frame

        while saved_events < max_events:
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

            if previous_board is None:
                previous_board = board.copy()
                continue

            difference = frame_difference(
                previous_board,
                board,
            )
            previous_board = board.copy()
            is_stable = (
                difference < VIDEO_STABLE_THRESHOLD
            )
            is_motion = (
                difference > VIDEO_MOTION_THRESHOLD
            )

            if not in_event:
                if is_stable:
                    stable_count += 1
                    if (
                        stable_count
                        >= VIDEO_STABLE_SAMPLES
                    ):
                        last_stable_board = (
                            board.copy()
                        )
                        last_stable_score = (
                            score.copy()
                        )
                else:
                    stable_count = 0

                can_start = (
                    is_motion
                    and last_stable_board is not None
                    and last_stable_score is not None
                    and (
                        timestamp - last_finished_time
                        >= VIDEO_EVENT_COOLDOWN_SECONDS
                    )
                )

                if can_start:
                    assert last_stable_board is not None
                    assert last_stable_score is not None

                    in_event = True
                    event_start_time = timestamp
                    before_board = (
                        last_stable_board.copy()
                    )
                    before_score = (
                        last_stable_score.copy()
                    )
                    action_board = board.copy()
                    action_score = score.copy()
                    action_max_difference = difference
                    event_stable_count = 0

            else:
                if difference > action_max_difference:
                    action_board = board.copy()
                    action_score = score.copy()
                    action_max_difference = difference

                if is_stable:
                    event_stable_count += 1
                else:
                    event_stable_count = 0

                event_duration = (
                    timestamp - event_start_time
                )
                if (
                    event_duration
                    > VIDEO_MAX_EVENT_SECONDS
                ):
                    in_event = False
                    event_stable_count = 0
                    stable_count = 0
                    last_stable_board = None
                    last_stable_score = None
                    last_finished_time = timestamp
                    continue

                if (
                    event_stable_count
                    >= VIDEO_STABLE_SAMPLES
                ):
                    detected_events += 1

                    assert before_board is not None
                    assert before_score is not None
                    assert action_board is not None
                    assert action_score is not None

                    budget_exceeded = False
                    try:
                        stored = process_event(
                            client=client,
                            budget=budget,
                            mode=mode,
                            before_board=before_board,
                            before_score=before_score,
                            action_board=action_board,
                            action_score=action_score,
                            after_board=board,
                            after_score=score,
                            timestamp=event_start_time,
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
                            f"t={event_start_time:.2f}: "
                            f"{error}"
                        )

                    in_event = False
                    last_finished_time = timestamp
                    last_stable_board = board.copy()
                    last_stable_score = score.copy()
                    stable_count = (
                        VIDEO_STABLE_SAMPLES
                    )
                    event_stable_count = 0

                    if budget_exceeded:
                        break

        if saved_events >= max_events:
            stop_reason = "max_events_reached"

    finally:
        capture.release()

        cost = budget.usage.estimated_cost_usd(
            INPUT_USD_PER_MILLION_TOKENS,
            OUTPUT_USD_PER_MILLION_TOKENS,
            CACHED_INPUT_USD_PER_MILLION_TOKENS,
        )
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
                "saved_events": saved_events,
                "stop_reason": stop_reason,
                "budget": budget.to_dict(),
                "estimated_cost_usd": cost,
            },
        )

    print_budget(budget)
    print(
        f"\n완료: 감지 {detected_events}개, "
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
    )


if __name__ == "__main__":
    main()
