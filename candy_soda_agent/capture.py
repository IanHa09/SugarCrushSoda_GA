"""MSS 캡처와 화면 변화량 계산을 담당합니다."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def print_monitors(sct: Any) -> None:
    """MSS가 인식한 모니터 번호와 좌표를 콘솔에 출력합니다."""

    print("\n[MSS 모니터 목록]")
    for index, monitor in enumerate(sct.monitors):
        label = "모든 모니터 결합 영역" if index == 0 else f"{index}번 모니터"
        print(f"{label}: {monitor}")


def make_absolute_region(
    sct: Any,
    monitor_index: int,
    board_offset: dict[str, int],
) -> dict[str, int]:
    """
    모니터 내부의 상대 좌표를 실제 Windows 화면의 절대 좌표로 바꿉니다.

    보조 모니터가 기본 모니터 왼쪽에 있으면 monitor['left']가
    음수일 수 있지만, 아래처럼 더해 주면 MSS가 정상적으로 처리합니다.
    """

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
    sct: Any,
    region: dict[str, int],
) -> np.ndarray:
    """
    지정한 영역을 캡처해 OpenCV에서 사용하는 BGR 이미지로 반환합니다.

    파일을 먼저 저장할 필요가 없습니다.
    게임 화면은 메모리 안의 numpy 배열로 바로 처리됩니다.
    """

    shot = sct.grab(region)

    # MSS 결과는 BGRA 4채널이므로 OpenCV용 BGR 3채널로 바꿉니다.
    bgra_image = np.asarray(shot)
    return cv2.cvtColor(bgra_image, cv2.COLOR_BGRA2BGR)


def frame_difference(
    image_a: np.ndarray,
    image_b: np.ndarray,
) -> float:
    """
    두 프레임의 평균적인 차이를 0에 가까운 값부터 반환합니다.

    값이 작으면 거의 같은 화면이고, 값이 커지면 화면 변화가 큽니다.
    정확한 게임 규칙 판별이 아니라 애니메이션과 중복 요청을 줄이기 위한
    간단한 휴리스틱입니다.
    """

    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)

    # 빠르게 비교하고 작은 반짝임의 영향을 줄이기 위해 축소합니다.
    small_a = cv2.resize(gray_a, (160, 160), interpolation=cv2.INTER_AREA)
    small_b = cv2.resize(gray_b, (160, 160), interpolation=cv2.INTER_AREA)

    difference = cv2.absdiff(small_a, small_b)
    return float(np.mean(difference)) / 255.0
