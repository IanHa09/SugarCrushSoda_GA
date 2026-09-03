"""MSS 캡처와 화면 변화량 계산을 담당합니다."""

from __future__ import annotations

import cv2
import mss
import numpy as np


def print_monitors(sct: mss.MSS) -> None:
    """MSS가 인식한 모니터 번호와 좌표를 콘솔에 출력합니다."""

    print("\n[MSS 모니터 목록]")
    for index, monitor in enumerate(sct.monitors):
        label = "모든 모니터 결합 영역" if index == 0 else f"{index}번 모니터"
        print(f"{label}: {monitor}")


def make_absolute_region(
    sct: mss.MSS,
    monitor_index: int,
    board_offset: dict[str, int],
) -> dict[str, int]:
    """모니터 내부의 상대 좌표를 실제 화면의 절대 좌표로 바꿉니다(음수 left 포함)."""

    if monitor_index <= 0 or monitor_index >= len(sct.monitors):
        raise ValueError(
            f"MONITOR_INDEX={monitor_index}가 올바르지 않습니다. "
            "calibrate_region.py에서 모니터 목록을 확인하세요."
        )

    monitor = sct.monitors[monitor_index]

    return {
        "left": monitor["left"] + board_offset["left"],
        "top": monitor["top"] + board_offset["top"],
        "width": board_offset["width"],
        "height": board_offset["height"],
    }


def capture_bgr(
    sct: mss.MSS,
    region: dict[str, int],
) -> np.ndarray:
    """지정 영역을 캡처해 파일 저장 없이 BGR numpy 배열로 반환합니다."""

    shot = sct.grab(region)

    # MSS 결과는 BGRA 4채널이므로 OpenCV용 BGR 3채널로 바꿉니다.
    bgra_image = np.asarray(shot)
    return cv2.cvtColor(bgra_image, cv2.COLOR_BGRA2BGR)


def frame_difference(
    image_a: np.ndarray,
    image_b: np.ndarray,
) -> float:
    """두 프레임의 평균 차이(0~1)를 반환해 애니메이션/중복 요청을 줄이는 휴리스틱입니다."""

    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)

    # 빠르게 비교하고 작은 반짝임의 영향을 줄이기 위해 축소합니다.
    small_a = cv2.resize(gray_a, (160, 160), interpolation=cv2.INTER_AREA)
    small_b = cv2.resize(gray_b, (160, 160), interpolation=cv2.INTER_AREA)

    difference = cv2.absdiff(small_a, small_b)
    return float(np.mean(difference)) / 255.0
