"""observe_action_result의 swap 수락/거부 판정을 검증합니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import play_session
from capture_modes import CaptureBundle


def _bundle(value: int) -> CaptureBundle:
    """단색 이미지로 채운 테스트용 CaptureBundle을 만듭니다."""

    image = np.full((80, 80, 3), value, dtype=np.uint8)
    region = {"left": 0, "top": 0, "width": 80, "height": 80}
    return CaptureBundle(image, image, region, region)


class ActionObservationTests(unittest.TestCase):
    def test_swap_that_returns_to_start_is_rejected(self) -> None:
        """보드가 원래 상태로 되돌아오면 swap이 거부로 판정되는지 확인합니다."""

        before = _bundle(0)
        frames = [_bundle(255), before, before, before]
        with (
            patch("play_session.capture_configured", side_effect=frames),
            patch("play_session.POST_ACTION_INTERVAL", 0.0),
            patch("play_session.POST_ACTION_MIN_WAIT", 0.0),
        ):
            observation, _ = play_session.observe_action_result(object(), before)

        self.assertEqual(observation.status, "rejected")

    def test_changed_final_board_is_accepted(self) -> None:
        """보드가 계속 바뀐 상태로 안정되면 swap이 수락으로 판정되는지 확인합니다."""

        before = _bundle(0)
        changed = _bundle(255)
        frames = [changed, changed, changed]
        with (
            patch("play_session.capture_configured", side_effect=frames),
            patch("play_session.POST_ACTION_INTERVAL", 0.0),
            patch("play_session.POST_ACTION_MIN_WAIT", 0.0),
        ):
            observation, _ = play_session.observe_action_result(object(), before)

        self.assertEqual(observation.status, "accepted")


if __name__ == "__main__":
    unittest.main()
