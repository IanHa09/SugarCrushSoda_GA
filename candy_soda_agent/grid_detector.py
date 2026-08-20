"""보드 이미지의 반복 경계로 행과 열을 추정합니다."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class GridShape:
    rows: int
    cols: int
    confidence: float
    source: str
    reason: str = ""


def _estimate_axis(
    gray: np.ndarray,
    *,
    axis: int,
    minimum: int,
    maximum: int,
) -> tuple[int, float, float]:
    length = gray.shape[1 if axis == 0 else 0]
    gradient = cv2.Sobel(
        gray,
        cv2.CV_32F,
        1 if axis == 0 else 0,
        0 if axis == 0 else 1,
        ksize=3,
    )
    projection = np.mean(np.abs(gradient), axis=0 if axis == 0 else 1)
    vector = projection.reshape(1, -1) if axis == 0 else projection.reshape(-1, 1)
    projection -= cv2.GaussianBlur(
        vector,
        (0, 0),
        max(3.0, length / 60),
    ).ravel()
    deviation = float(projection.std())
    if deviation < 1e-6:
        return 0, 0.0, 0.0
    projection = (projection - projection.mean()) / deviation

    scores: list[tuple[float, int]] = []
    for count in range(minimum, maximum + 1):
        center = round(length / count)
        correlations = []
        for lag in range(max(1, center - 2), center + 3):
            correlations.append(float(np.mean(projection[:-lag] * projection[lag:])))
        scores.append((max(correlations), count))

    scores.sort(reverse=True)
    best_score, count = scores[0]
    second_score = max(0.0, scores[1][0]) if len(scores) > 1 else 0.0
    margin = max(0.0, best_score - second_score)
    confidence = min(1.0, max(0.0, best_score) * 1.2 + margin)
    return count, confidence, best_score


def detect_grid_shape(
    image: np.ndarray,
    fallback_rows: int,
    fallback_cols: int,
    *,
    enabled: bool = True,
    min_rows: int = 4,
    max_rows: int = 12,
    min_cols: int = 4,
    max_cols: int = 12,
    min_confidence: float = 0.45,
) -> GridShape:
    if not enabled:
        return GridShape(
            fallback_rows,
            fallback_cols,
            0.0,
            "fallback",
            "자동 감지 꺼짐",
        )

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        rows, row_confidence, row_score = _estimate_axis(
            gray,
            axis=1,
            minimum=min_rows,
            maximum=max_rows,
        )
        cols, col_confidence, col_score = _estimate_axis(
            gray,
            axis=0,
            minimum=min_cols,
            maximum=max_cols,
        )
        confidence = min(row_confidence, col_confidence)
        if rows <= 0 or cols <= 0:
            return GridShape(
                fallback_rows,
                fallback_cols,
                confidence,
                "fallback",
                "반복 경계를 찾지 못함",
            )
        cell_height = image.shape[0] / rows
        cell_width = image.shape[1] / cols
        aspect_error = abs(cell_width / cell_height - 1.0)
        if (
            confidence < min_confidence
            or min(row_score, col_score) < 0.20
            or aspect_error > 0.25
        ):
            reason = (
                f"신뢰도={confidence:.2f}, 셀비율오차={aspect_error:.2f}"
            )
            return GridShape(
                fallback_rows,
                fallback_cols,
                confidence,
                "fallback",
                reason,
            )
        return GridShape(rows, cols, confidence, "auto")
    except Exception as error:
        return GridShape(
            fallback_rows,
            fallback_cols,
            0.0,
            "fallback",
            f"감지 오류: {error}",
        )
