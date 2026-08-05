"""LLM이 좌표를 읽기 쉽도록 이미지를 가공하고 Base64로 변환합니다."""

from __future__ import annotations

import base64

import cv2
import numpy as np


def add_grid_overlay(
    image: np.ndarray,
    rows: int,
    cols: int,
) -> np.ndarray:
    """
    보드 위에 행·열 격자와 번호를 추가합니다.

    LLM에 픽셀 좌표를 직접 요구하면 흔들릴 수 있으므로,
    (행, 열) 단위로 행동을 고르게 만드는 것이 더 단순합니다.
    """

    height, width = image.shape[:2]
    top_margin = 40
    left_margin = 40

    # 좌표 번호를 표시할 흰색 여백을 위와 왼쪽에 추가합니다.
    result = cv2.copyMakeBorder(
        image,
        top=top_margin,
        bottom=0,
        left=left_margin,
        right=0,
        borderType=cv2.BORDER_CONSTANT,
        value=(255, 255, 255),
    )

    board_left = left_margin
    board_top = top_margin

    # 가로 격자선
    for row_index in range(rows + 1):
        y = board_top + round(row_index * height / rows)
        cv2.line(
            result,
            (board_left, y),
            (board_left + width, y),
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    # 세로 격자선
    for col_index in range(cols + 1):
        x = board_left + round(col_index * width / cols)
        cv2.line(
            result,
            (x, board_top),
            (x, board_top + height),
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    # 열 번호
    for col in range(1, cols + 1):
        x = board_left + round((col - 0.5) * width / cols)
        cv2.putText(
            result,
            str(col),
            (x - 6, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    # 행 번호
    for row in range(1, rows + 1):
        y = board_top + round((row - 0.5) * height / rows)
        cv2.putText(
            result,
            str(row),
            (8, y + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    return result


def image_to_data_url(image: np.ndarray) -> str:
    """
    OpenCV 이미지를 PNG로 압축하고 Base64 데이터 URL로 변환합니다.

    캡처 파일을 디스크에서 다시 읽지 않고, 메모리의 이미지를 곧바로
    OpenAI API에 전달하기 위한 함수입니다.
    """

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("이미지를 PNG로 변환하지 못했습니다.")

    base64_image = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/png;base64,{base64_image}"
