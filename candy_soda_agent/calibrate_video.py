"""영상 프레임에서 보드와 점수 ROI를 선택하는 도구."""

from __future__ import annotations

import argparse

import cv2

from config import (
    VIDEO_CALIBRATION_SECONDS,
    VIDEO_PATH,
)


def select_roi(
    frame,
    title: str,
) -> dict[str, int]:
    x, y, width, height = cv2.selectROI(
        title,
        frame,
        showCrosshair=True,
        fromCenter=False,
    )
    cv2.destroyWindow(title)

    if width == 0 or height == 0:
        raise RuntimeError(
            f"{title} 영역 선택이 취소됐습니다."
        )

    return {
        "left": int(x),
        "top": int(y),
        "width": int(width),
        "height": int(height),
    }


def print_roi(
    name: str,
    roi: dict[str, int],
) -> None:
    print(f"\n{name} = {{")
    print(f'    "left": {roi["left"]},')
    print(f'    "top": {roi["top"]},')
    print(f'    "width": {roi["width"]},')
    print(f'    "height": {roi["height"]},')
    print("}")


def calibrate(
    video_path: str,
    timestamp_seconds: float,
) -> None:
    if timestamp_seconds < 0:
        raise ValueError(
            "보정 시점은 0 이상이어야 합니다."
        )

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise RuntimeError(
            f"영상을 열 수 없습니다: {video_path}"
        )

    try:
        capture.set(
            cv2.CAP_PROP_POS_MSEC,
            timestamp_seconds * 1000,
        )
        success, frame = capture.read()
    finally:
        capture.release()

    if not success:
        raise RuntimeError(
            f"{timestamp_seconds:.2f}초 프레임을 "
            "읽지 못했습니다."
        )

    print(
        "1. 현재 레벨의 전체 9×9 보드를 "
        "드래그하세요."
    )
    board_roi = select_roi(
        frame,
        "Select puzzle board",
    )

    print(
        "2. 별과 현재 점수가 표시된 패널을 "
        "드래그하세요."
    )
    score_roi = select_roi(
        frame,
        "Select current score panel",
    )

    cv2.destroyAllWindows()
    print("\nconfig.py에 아래 값을 복사하세요.")
    print_roi("VIDEO_BOARD_ROI", board_roi)
    print_roi("VIDEO_SCORE_ROI", score_roi)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--video",
        default=VIDEO_PATH,
    )
    parser.add_argument(
        "--time",
        type=float,
        default=VIDEO_CALIBRATION_SECONDS,
        help="보드가 보이는 영상 시점(초)",
    )
    arguments = parser.parse_args()

    calibrate(
        arguments.video,
        arguments.time,
    )


if __name__ == "__main__":
    main()
