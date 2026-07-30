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

    if image.size == 0:
        raise ValueError("격자를 추가할 이미지가 비어 있습니다.")
    if rows <= 0 or cols <= 0:
        raise ValueError("rows와 cols는 1 이상이어야 합니다.")

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

    if image.size == 0:
        raise ValueError("인코딩할 이미지가 비어 있습니다.")

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("이미지를 PNG로 변환하지 못했습니다.")

    base64_image = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/png;base64,{base64_image}"

#############################################################

def crop_roi(
    frame: np.ndarray,
    roi: dict[str, int],
) -> np.ndarray:
    """영상 프레임에서 지정한 사각형 영역을 자릅니다."""

    if frame.size == 0:
        raise ValueError("ROI를 자를 영상 프레임이 비어 있습니다.")

    required_keys = {
        "left",
        "top",
        "width",
        "height",
    }
    missing_keys = required_keys.difference(roi)
    if missing_keys:
        raise ValueError(
            "ROI에 필요한 키가 없습니다: "
            + ", ".join(sorted(missing_keys))
        )

    left = roi["left"]
    top = roi["top"]
    width = roi["width"]
    height = roi["height"]

    if min(left, top) < 0:
        raise ValueError(
            f"ROI 시작 좌표는 0 이상이어야 합니다: {roi}"
        )
    if width <= 0 or height <= 0:
        raise ValueError(
            f"ROI 너비와 높이는 1 이상이어야 합니다: {roi}"
        )

    frame_height, frame_width = frame.shape[:2]
    right = left + width
    bottom = top + height

    if right > frame_width or bottom > frame_height:
        raise ValueError(
            "ROI가 영상 프레임 범위를 벗어났습니다. "
            f"frame={frame_width}x{frame_height}, roi={roi}"
        )

    return frame[
        top:bottom,
        left:right,
    ].copy()


def make_video_observation(
    board_image: np.ndarray,
    score_image: np.ndarray,
    rows: int,
    cols: int,
) -> np.ndarray:
    """
    점수 영역과 격자가 표시된 보드를 한 이미지로 합치기.

    점수용 API와 보드용 API를 따로 호출하지 않기 위한 처리.
    """

    if board_image.size == 0:
        raise ValueError("보드 이미지가 비어 있습니다.")
    if score_image.size == 0:
        raise ValueError("점수 이미지가 비어 있습니다.")

    grid_image = add_grid_overlay(
        board_image,
        rows,
        cols,
    )

    canvas_width = grid_image.shape[1]
    panel_height = 140

    score_panel = np.full(
        (
            panel_height,
            canvas_width,
            3,
        ),
        255,
        dtype=np.uint8,
    )

    cv2.putText(
        score_panel,
        "CURRENT SCORE",
        (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )

    score_height, score_width = (
        score_image.shape[:2]
    )

    max_width = canvas_width - 20
    max_height = panel_height - 40

    scale = min(
        max_width / score_width,
        max_height / score_height,
    )

    resized_score = cv2.resize(
        score_image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    x = (
        canvas_width
        - resized_score.shape[1]
    ) // 2

    y = 34

    score_panel[
        y:y + resized_score.shape[0],
        x:x + resized_score.shape[1],
    ] = resized_score

    return np.vstack(
        [
            score_panel,
            grid_image,
        ]
    )
