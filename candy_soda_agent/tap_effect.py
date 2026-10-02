"""탭 뒤 화면이 멈출 때까지 기다리고, 탭이 화면을 바꿨는지 픽셀로 판정합니다(2단계).

예전에는 탭 뒤 고정 1.5초만 쉬고 다음 관찰을 했습니다. 전환 애니메이션 도중에
캡처하면 같은 화면으로 잘못 기록될 수 있고, 무엇보다 "눌렀는데 아무 일도 없었다"
(빗나감)와 "눌러서 다른 화면으로 갔다"를 LLM 분류에만 기대 구분했습니다.

실측(run_2026-09-21_10-51-14) 기준 frame_difference 값:
  - 같은 화면 두 캡처: 0.0000 ~ 0.0038 (맵의 반짝임 애니메이션 포함)
  - 실제 화면 전환:     0.054 이상
그래서 SAME_SCREEN_PIXEL_THRESHOLD(기본 0.01)보다 작으면 "화면 그대로"로 봅니다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from capture import frame_difference
from config import (
    POST_TAP_INTERVAL,
    POST_TAP_MIN_WAIT,
    POST_TAP_STABLE_FRAMES,
    POST_TAP_STABLE_THRESHOLD,
    POST_TAP_TIMEOUT,
    SAME_SCREEN_PIXEL_THRESHOLD,
)


@dataclass(frozen=True)
class TapEffect:
    """탭 전 화면과 멈춘 뒤 화면의 차이."""

    changed: bool
    final_change: float
    peak_change: float
    settled: bool
    waited: float
    frames: int

    def to_record(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "final_change": round(self.final_change, 4),
            "peak_change": round(self.peak_change, 4),
            "settled": self.settled,
            "waited": round(self.waited, 2),
            "frames": self.frames,
        }


def same_screen(
    image_a: np.ndarray | None,
    image_b: np.ndarray | None,
    threshold: float = SAME_SCREEN_PIXEL_THRESHOLD,
) -> bool:
    """두 캡처가 픽셀로 같은 화면인지. 크기가 다르거나 없으면 False."""

    if image_a is None or image_b is None or image_a.shape != image_b.shape:
        return False
    return frame_difference(image_a, image_b) < threshold


def wait_for_settle(
    grab: Callable[[], np.ndarray],
    before: np.ndarray,
    *,
    min_wait: float = POST_TAP_MIN_WAIT,
    interval: float = POST_TAP_INTERVAL,
    timeout: float = POST_TAP_TIMEOUT,
    stable_frames: int = POST_TAP_STABLE_FRAMES,
    stable_threshold: float = POST_TAP_STABLE_THRESHOLD,
    change_threshold: float = SAME_SCREEN_PIXEL_THRESHOLD,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> TapEffect:
    """min_wait만큼 기다린 뒤 interval마다 캡처해, 연속 stable_frames번 거의 안
    바뀌면 멈췄다고 봅니다. timeout이 지나면 그 시점 화면으로 판정합니다."""

    started = clock()
    sleep(min_wait)
    previous = grab()
    frames = 1
    peak = frame_difference(before, previous)
    stable = 0
    settled = False

    while clock() - started < timeout:
        sleep(interval)
        current = grab()
        frames += 1
        peak = max(peak, frame_difference(before, current))
        if frame_difference(previous, current) < stable_threshold:
            stable += 1
            previous = current
            if stable >= stable_frames:
                settled = True
                break
        else:
            stable = 0
            previous = current

    final = frame_difference(before, previous)
    return TapEffect(
        changed=final >= change_threshold,
        final_change=final,
        peak_change=peak,
        settled=settled,
        waited=clock() - started,
        frames=frames,
    )
