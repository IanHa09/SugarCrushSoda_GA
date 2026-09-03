"""허용된 범위 안에서 게임 화면과 보드를 캡처합니다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from capture import capture_bgr, make_absolute_region
from config import BOARD_OFFSET, CAPTURE_MODE, MONITOR_INDEX, WINDOW_OFFSET


CaptureMode = Literal["board", "window", "monitor"]
VALID_CAPTURE_MODES = {"board", "window", "monitor"}


@dataclass(frozen=True)
class CaptureBundle:
    """전체 화면/보드 이미지와 각각의 캡처 영역을 담는 결과 묶음입니다."""

    full_image: np.ndarray
    board_image: np.ndarray
    full_region: dict[str, int]
    board_region: dict[str, int]


def _validate_offset(offset: dict[str, int], name: str) -> None:
    # 오프셋 딕셔너리의 키, 타입, 범위를 검사합니다.
    required_keys = {"left", "top", "width", "height"}
    missing_keys = required_keys.difference(offset)
    if missing_keys:
        raise ValueError(f"{name}에 키가 없습니다: {sorted(missing_keys)}")
    if not all(isinstance(offset[key], int) for key in required_keys):
        raise ValueError(f"{name}의 좌표와 크기는 정수여야 합니다.")
    if offset["left"] < 0 or offset["top"] < 0:
        raise ValueError(f"{name}의 상대 좌표는 음수일 수 없습니다.")
    if offset["width"] <= 0 or offset["height"] <= 0:
        raise ValueError(f"{name}의 너비와 높이는 양수여야 합니다.")


def validate_capture_settings(
    sct,
    mode: str,
    monitor_index: int,
    board_offset: dict[str, int],
    window_offset: dict[str, int],
) -> None:
    # 캡처 모드, 모니터 인덱스, 오프셋이 유효한지 검사합니다.
    if mode not in VALID_CAPTURE_MODES:
        raise ValueError(f"지원하지 않는 CAPTURE_MODE입니다: {mode!r}")
    if monitor_index <= 0 or monitor_index >= len(sct.monitors):
        raise ValueError(
            f"MONITOR_INDEX={monitor_index}가 올바르지 않습니다. "
            "출력된 모니터 목록에서 1 이상의 번호를 선택하세요."
        )

    _validate_offset(board_offset, "BOARD_OFFSET")
    if mode == "window":
        _validate_offset(window_offset, "WINDOW_OFFSET")

    monitor = sct.monitors[monitor_index]
    offsets = [("BOARD_OFFSET", board_offset)]
    if mode == "window":
        offsets.append(("WINDOW_OFFSET", window_offset))

    for name, offset in offsets:
        if offset["left"] + offset["width"] > monitor["width"]:
            raise ValueError(f"{name}이 선택한 모니터의 오른쪽 경계를 벗어납니다.")
        if offset["top"] + offset["height"] > monitor["height"]:
            raise ValueError(f"{name}이 선택한 모니터의 아래쪽 경계를 벗어납니다.")


def capture_bundle(
    sct,
    mode: CaptureMode,
    monitor_index: int,
    board_offset: dict[str, int],
    window_offset: dict[str, int],
) -> CaptureBundle:
    # 설정된 모드에 맞춰 전체 화면과 보드 영역을 캡처합니다.
    validate_capture_settings(
        sct,
        mode,
        monitor_index,
        board_offset,
        window_offset,
    )

    monitor_region = dict(sct.monitors[monitor_index])
    board_region = make_absolute_region(sct, monitor_index, board_offset)

    if mode == "board":
        board_image = capture_bgr(sct, board_region)
        return CaptureBundle(
            board_image,
            board_image,
            board_region,
            board_region,
        )

    if mode == "window":
        full_region = make_absolute_region(sct, monitor_index, window_offset)
    else:
        full_region = monitor_region

    full_image = capture_bgr(sct, full_region)
    board_image = capture_bgr(sct, board_region)
    return CaptureBundle(full_image, board_image, full_region, board_region)


def capture_configured(sct) -> CaptureBundle:
    """config.py에 설정된 모드와 좌표로 현재 화면을 캡처합니다."""

    return capture_bundle(
        sct,
        CAPTURE_MODE,
        MONITOR_INDEX,
        BOARD_OFFSET,
        WINDOW_OFFSET,
    )
