"""영상에서 최초 인접 swap 구간을 로컬로 검출합니다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from capture import frame_difference


@dataclass(frozen=True)
class SwapCandidate:
    """연속된 두 프레임에서 검출한 인접 셀 교환 후보."""

    source_row: int
    source_col: int
    target_row: int
    target_col: int
    pair_sum: float
    second_cell_change: float
    dominance: float
    active_cells: int
    global_difference: float

    @property
    def normalized_pair(
        self,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        ordered = sorted(
            (
                (self.source_row, self.source_col),
                (self.target_row, self.target_col),
            )
        )
        return ordered[0], ordered[1]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": {
                "row": self.source_row,
                "col": self.source_col,
            },
            "target": {
                "row": self.target_row,
                "col": self.target_col,
            },
            "pair_sum": self.pair_sum,
            "second_cell_change": self.second_cell_change,
            "dominance": self.dominance,
            "active_cells": self.active_cells,
            "global_difference": self.global_difference,
        }


@dataclass(frozen=True)
class DetectedVideoEvent:
    """확인된 swap과 반응 종료 프레임."""

    before_board: np.ndarray
    before_score: np.ndarray
    action_board: np.ndarray
    action_score: np.ndarray
    after_board: np.ndarray
    after_score: np.ndarray
    event_timestamp: float
    action_timestamp: float
    end_timestamp: float
    candidate: SwapCandidate


@dataclass(frozen=True)
class RejectedVideoEvent:
    """API 호출 전에 로컬 검출 단계에서 폐기된 사건."""

    before_board: np.ndarray
    before_score: np.ndarray
    action_board: np.ndarray
    action_score: np.ndarray
    after_board: np.ndarray
    after_score: np.ndarray
    event_timestamp: float
    action_timestamp: float
    end_timestamp: float
    discard_code: str
    candidate: SwapCandidate | None


@dataclass
class _PendingSwap:
    before_board: np.ndarray
    before_score: np.ndarray
    first_action_board: np.ndarray
    first_action_score: np.ndarray
    best_action_board: np.ndarray
    best_action_score: np.ndarray
    event_timestamp: float
    best_action_timestamp: float
    candidate: SwapCandidate
    confirmations: int = 1
    samples: int = 1


@dataclass
class _ConfirmedSwap:
    before_board: np.ndarray
    before_score: np.ndarray
    action_board: np.ndarray
    action_score: np.ndarray
    event_timestamp: float
    action_timestamp: float
    candidate: SwapCandidate
    stable_samples: int = 0


def _cell_change_scores(
    before_board: np.ndarray,
    current_board: np.ndarray,
    rows: int,
    cols: int,
    margin_ratio: float,
) -> np.ndarray:
    if before_board.size == 0 or current_board.size == 0:
        raise ValueError("셀 변화량을 계산할 보드가 비어 있습니다.")
    if before_board.shape != current_board.shape:
        raise ValueError(
            "셀 변화량을 계산할 두 보드의 크기가 다릅니다."
        )
    if rows <= 0 or cols <= 0:
        raise ValueError("rows와 cols는 1 이상이어야 합니다.")
    if not 0.0 <= margin_ratio < 0.5:
        raise ValueError(
            "margin_ratio는 0 이상 0.5 미만이어야 합니다."
        )

    before_gray = cv2.cvtColor(
        before_board,
        cv2.COLOR_BGR2GRAY,
    )
    current_gray = cv2.cvtColor(
        current_board,
        cv2.COLOR_BGR2GRAY,
    )
    height, width = before_gray.shape
    scores = np.zeros((rows, cols), dtype=np.float64)

    for row in range(rows):
        top = round(row * height / rows)
        bottom = round((row + 1) * height / rows)
        margin_y = max(
            1,
            round((bottom - top) * margin_ratio),
        )

        for col in range(cols):
            left = round(col * width / cols)
            right = round((col + 1) * width / cols)
            margin_x = max(
                1,
                round((right - left) * margin_ratio),
            )

            before_cell = before_gray[
                top + margin_y:bottom - margin_y,
                left + margin_x:right - margin_x,
            ]
            current_cell = current_gray[
                top + margin_y:bottom - margin_y,
                left + margin_x:right - margin_x,
            ]

            if before_cell.size == 0 or current_cell.size == 0:
                raise ValueError(
                    "셀 내부 영역이 비었습니다. ROI와 격자를 확인하세요."
                )

            difference = cv2.absdiff(
                before_cell,
                current_cell,
            )
            scores[row, col] = (
                float(np.mean(difference)) / 255.0
            )

    return scores


def detect_swap_candidate(
    before_board: np.ndarray,
    current_board: np.ndarray,
    *,
    rows: int,
    cols: int,
    margin_ratio: float,
    active_threshold: float,
    min_pair_sum: float,
    min_second_cell_change: float,
    min_dominance: float,
    max_active_cells: int,
    max_global_difference: float,
) -> SwapCandidate | None:
    """
    변화가 인접한 두 셀에 집중된 최초 swap 후보만 반환합니다.

    폭발, 연쇄 낙하, 팝업처럼 여러 셀이 동시에 바뀌는 장면은
    API 호출 전에 None으로 거릅니다.
    """

    scores = _cell_change_scores(
        before_board,
        current_board,
        rows,
        cols,
        margin_ratio,
    )
    flat_order = np.argsort(
        scores,
        axis=None,
    )[::-1]
    first_index = int(flat_order[0])
    second_index = int(flat_order[1])
    first_row, first_col = np.unravel_index(
        first_index,
        scores.shape,
    )
    second_row, second_col = np.unravel_index(
        second_index,
        scores.shape,
    )
    first_score = float(scores[first_row, first_col])
    second_score = float(
        scores[second_row, second_col]
    )

    distance = (
        abs(first_row - second_row)
        + abs(first_col - second_col)
    )
    if distance != 1:
        return None

    pair_sum = first_score + second_score
    if pair_sum < min_pair_sum:
        return None
    if second_score < min_second_cell_change:
        return None

    active_cells = int(
        np.count_nonzero(
            scores >= active_threshold
        )
    )
    if active_cells > max_active_cells:
        return None

    total_change = float(np.sum(scores))
    dominance = (
        pair_sum / total_change
        if total_change > 0.0
        else 0.0
    )
    if dominance < min_dominance:
        return None

    global_difference = frame_difference(
        before_board,
        current_board,
    )
    if global_difference > max_global_difference:
        return None

    ordered_pair = sorted(
        (
            (
                int(first_row) + 1,
                int(first_col) + 1,
            ),
            (
                int(second_row) + 1,
                int(second_col) + 1,
            ),
        )
    )
    source, target = ordered_pair

    return SwapCandidate(
        source_row=source[0],
        source_col=source[1],
        target_row=target[0],
        target_col=target[1],
        pair_sum=pair_sum,
        second_cell_change=second_score,
        dominance=dominance,
        active_cells=active_cells,
        global_difference=global_difference,
    )


class VideoSwapDetector:
    """조용한 보드 뒤에 발생한 최초 인접 swap을 추적합니다."""

    def __init__(
        self,
        *,
        rows: int,
        cols: int,
        quiet_threshold: float,
        pre_action_quiet_samples: int,
        confirm_window_samples: int,
        confirm_required_samples: int,
        action_window_seconds: float,
        after_stable_samples: int,
        max_event_seconds: float,
        event_cooldown_seconds: float,
        cell_margin_ratio: float,
        cell_active_threshold: float,
        min_pair_sum: float,
        min_second_cell_change: float,
        min_pair_dominance: float,
        max_active_cells: int,
        max_swap_global_difference: float,
    ) -> None:
        if pre_action_quiet_samples <= 0:
            raise ValueError(
                "pre_action_quiet_samples는 1 이상이어야 합니다."
            )
        if confirm_window_samples <= 0:
            raise ValueError(
                "confirm_window_samples는 1 이상이어야 합니다."
            )
        if not 1 <= confirm_required_samples <= confirm_window_samples:
            raise ValueError(
                "confirm_required_samples는 확인 창 범위 안이어야 합니다."
            )
        if after_stable_samples <= 0:
            raise ValueError(
                "after_stable_samples는 1 이상이어야 합니다."
            )

        self.rows = rows
        self.cols = cols
        self.quiet_threshold = quiet_threshold
        self.pre_action_quiet_samples = (
            pre_action_quiet_samples
        )
        self.confirm_window_samples = (
            confirm_window_samples
        )
        self.confirm_required_samples = (
            confirm_required_samples
        )
        self.action_window_seconds = (
            action_window_seconds
        )
        self.after_stable_samples = (
            after_stable_samples
        )
        self.max_event_seconds = max_event_seconds
        self.event_cooldown_seconds = (
            event_cooldown_seconds
        )
        self.candidate_arguments: dict[str, Any] = {
            "rows": rows,
            "cols": cols,
            "margin_ratio": cell_margin_ratio,
            "active_threshold": (
                cell_active_threshold
            ),
            "min_pair_sum": min_pair_sum,
            "min_second_cell_change": (
                min_second_cell_change
            ),
            "min_dominance": (
                min_pair_dominance
            ),
            "max_active_cells": max_active_cells,
            "max_global_difference": (
                max_swap_global_difference
            ),
        }

        self.previous_board: np.ndarray | None = None
        self.previous_score: np.ndarray | None = None
        self.quiet_samples = 0
        self.pending: _PendingSwap | None = None
        self.confirmed: _ConfirmedSwap | None = None
        self.last_event_end = -999.0

    def _candidate(
        self,
        before_board: np.ndarray,
        current_board: np.ndarray,
    ) -> SwapCandidate | None:
        return detect_swap_candidate(
            before_board,
            current_board,
            **self.candidate_arguments,
        )

    def update(
        self,
        timestamp: float,
        board: np.ndarray,
        score: np.ndarray,
    ) -> DetectedVideoEvent | RejectedVideoEvent | None:
        if self.previous_board is None:
            self.previous_board = board.copy()
            self.previous_score = score.copy()
            return None

        assert self.previous_score is not None

        previous_board = self.previous_board
        previous_score = self.previous_score
        step_difference = frame_difference(
            previous_board,
            board,
        )
        is_quiet = (
            step_difference < self.quiet_threshold
        )
        candidate = self._candidate(
            previous_board,
            board,
        )
        result: (
            DetectedVideoEvent
            | RejectedVideoEvent
            | None
        ) = None

        if self.confirmed is not None:
            confirmed = self.confirmed

            if (
                timestamp - confirmed.event_timestamp
                <= self.action_window_seconds
                and candidate is not None
                and candidate.normalized_pair
                == confirmed.candidate.normalized_pair
                and candidate.pair_sum
                > confirmed.candidate.pair_sum
            ):
                confirmed.action_board = board.copy()
                confirmed.action_score = score.copy()
                confirmed.action_timestamp = timestamp
                confirmed.candidate = candidate

            confirmed.stable_samples = (
                confirmed.stable_samples + 1
                if is_quiet
                else 0
            )
            duration = (
                timestamp
                - confirmed.event_timestamp
            )

            if duration > self.max_event_seconds:
                result = RejectedVideoEvent(
                    before_board=confirmed.before_board,
                    before_score=confirmed.before_score,
                    action_board=confirmed.action_board,
                    action_score=confirmed.action_score,
                    after_board=board.copy(),
                    after_score=score.copy(),
                    event_timestamp=(
                        confirmed.event_timestamp
                    ),
                    action_timestamp=(
                        confirmed.action_timestamp
                    ),
                    end_timestamp=timestamp,
                    discard_code="event_timeout",
                    candidate=confirmed.candidate,
                )
                self.confirmed = None
                self.last_event_end = timestamp
                self.quiet_samples = 0
            elif (
                confirmed.stable_samples
                >= self.after_stable_samples
            ):
                result = DetectedVideoEvent(
                    before_board=confirmed.before_board,
                    before_score=confirmed.before_score,
                    action_board=confirmed.action_board,
                    action_score=confirmed.action_score,
                    after_board=board.copy(),
                    after_score=score.copy(),
                    event_timestamp=(
                        confirmed.event_timestamp
                    ),
                    action_timestamp=(
                        confirmed.action_timestamp
                    ),
                    end_timestamp=timestamp,
                    candidate=confirmed.candidate,
                )
                self.confirmed = None
                self.last_event_end = timestamp
                self.quiet_samples = (
                    self.after_stable_samples
                )

        elif self.pending is not None:
            pending = self.pending
            pending.samples += 1

            if (
                candidate is not None
                and candidate.normalized_pair
                == pending.candidate.normalized_pair
            ):
                pending.confirmations += 1

                if (
                    timestamp - pending.event_timestamp
                    <= self.action_window_seconds
                    and candidate.pair_sum
                    > pending.candidate.pair_sum
                ):
                    pending.best_action_board = (
                        board.copy()
                    )
                    pending.best_action_score = (
                        score.copy()
                    )
                    pending.best_action_timestamp = (
                        timestamp
                    )
                    pending.candidate = candidate

            if (
                pending.samples
                >= self.confirm_window_samples
            ):
                if (
                    pending.confirmations
                    >= self.confirm_required_samples
                ):
                    self.confirmed = _ConfirmedSwap(
                        before_board=(
                            pending.before_board
                        ),
                        before_score=(
                            pending.before_score
                        ),
                        action_board=(
                            pending.best_action_board
                        ),
                        action_score=(
                            pending.best_action_score
                        ),
                        event_timestamp=(
                            pending.event_timestamp
                        ),
                        action_timestamp=(
                            pending.best_action_timestamp
                        ),
                        candidate=pending.candidate,
                    )
                else:
                    result = RejectedVideoEvent(
                        before_board=(
                            pending.before_board
                        ),
                        before_score=(
                            pending.before_score
                        ),
                        action_board=(
                            pending.first_action_board
                        ),
                        action_score=(
                            pending.first_action_score
                        ),
                        after_board=board.copy(),
                        after_score=score.copy(),
                        event_timestamp=(
                            pending.event_timestamp
                        ),
                        action_timestamp=(
                            pending.event_timestamp
                        ),
                        end_timestamp=timestamp,
                        discard_code=(
                            "candidate_not_confirmed"
                        ),
                        candidate=pending.candidate,
                    )
                    self.last_event_end = timestamp
                    self.quiet_samples = 0

                self.pending = None

        else:
            can_arm = (
                self.quiet_samples
                >= self.pre_action_quiet_samples
                and (
                    timestamp - self.last_event_end
                    >= self.event_cooldown_seconds
                )
            )

            if can_arm and candidate is not None:
                self.pending = _PendingSwap(
                    before_board=previous_board.copy(),
                    before_score=previous_score.copy(),
                    first_action_board=board.copy(),
                    first_action_score=score.copy(),
                    best_action_board=board.copy(),
                    best_action_score=score.copy(),
                    event_timestamp=timestamp,
                    best_action_timestamp=timestamp,
                    candidate=candidate,
                )
                self.quiet_samples = 0
            else:
                self.quiet_samples = (
                    self.quiet_samples + 1
                    if is_quiet
                    else 0
                )

        self.previous_board = board.copy()
        self.previous_score = score.copy()
        return result
