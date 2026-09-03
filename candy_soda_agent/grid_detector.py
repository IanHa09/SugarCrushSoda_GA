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
    # 자기상관으로 한 축(행 또는 열)의 반복 간격과 신뢰도를 추정합니다.
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
    # 이미지의 반복 간격으로 행/열 수를 자동 감지하고, 실패 시 fallback 값을 씁니다.
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

    
@dataclass(frozen=True)
class BoardGeometry:
    """크롭 안에서 실제 보드가 차지하는 사각형과 칸 수 """
    rows: int
    cols: int
    left: int
    top: int
    width: int
    height: int 
    source: str     # "auto" | "fallback "
    reason: str = "" # 실패 이유. source가 fallback일 때만 의미 있음

def _odd(value: float) -> int:
    """모폴로지 커널은 홀수여야 중심이 생깁니다."""

    size = max(3, int(value))
    return size if size % 2 else size + 1


def detect_board_geometry(
    image: np.ndarray,
    *,
    cell_size: float,
    fallback_rows: int,
    fallback_cols: int,
    min_rows: int = 4,
    max_rows: int = 12,
    min_cols: int = 4,
    max_cols: int = 12,
    variance_ratio: float = 0.20,
    squareness_tolerance: float = 0.15,
) -> BoardGeometry:
    """국소 표준편차로 보드 사각형을 찾고, 셀 크기로 나눠 칸 수를 계산합니다."""

    height, width = image.shape[:2]

    def give_up(reason: str) -> BoardGeometry:
        # 실패하면 크롭 전체를 그대로 쓰되, source로 실패를 알립니다.
        return BoardGeometry(
            fallback_rows, fallback_cols, 0, 0, width, height, "fallback", reason
        )

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)

        window = _odd(cell_size / 4)
        mean = cv2.blur(gray, (window, window))
        variance = cv2.blur(gray * gray, (window, window)) - mean * mean
        deviation = np.sqrt(np.maximum(variance, 0))
        if deviation.max() < 1e-6:
            return give_up("화면에 무늬가 없음")

        mask = (deviation > deviation.max() * variance_ratio).astype(np.uint8)
        # 사탕 사이 틈만 메울 정도로 작게. 크게 잡으면 상단 UI까지 한 덩어리가 됩니다.
        kernel = _odd(cell_size * 0.25)
        block = np.ones((kernel, kernel), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, block)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, block)

        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if count <= 1:
            return give_up("보드로 볼 영역을 찾지 못함")

        largest = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA])
        left, top, board_width, board_height = (int(v) for v in stats[largest, :4])

        rows = round(board_height / cell_size)
        cols = round(board_width / cell_size)
        if not (min_rows <= rows <= max_rows and min_cols <= cols <= max_cols):
            return give_up(f"칸 수가 범위 밖: {rows}x{cols}")

        actual_height = board_height / rows
        actual_width = board_width / cols
        skew = abs(actual_width / actual_height - 1.0)
        if skew > squareness_tolerance:
            return give_up(
                f"칸이 정사각형이 아님: 가로 {actual_width:.1f} 세로 {actual_height:.1f}"
            )

        return BoardGeometry(
            rows, cols, left, top, board_width, board_height, "auto"
        )
    except Exception as error:
        return give_up(f"감지 오류: {error}")