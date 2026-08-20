from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import main
from capture_modes import CaptureBundle


def _bundle(value: int) -> CaptureBundle:
    image = np.full((80, 80, 3), value, dtype=np.uint8)
    region = {"left": 0, "top": 0, "width": 80, "height": 80}
    return CaptureBundle(image, image, region, region)


class ActionObservationTests(unittest.TestCase):
    def test_swap_that_returns_to_start_is_rejected(self) -> None:
        before = _bundle(0)
        frames = [_bundle(255), before, before, before]
        with (
            patch("main._capture_current", side_effect=frames),
            patch("main.POST_ACTION_INTERVAL", 0.0),
            patch("main.POST_ACTION_MIN_WAIT", 0.0),
        ):
            observation, _ = main.observe_action_result(object(), before)

        self.assertEqual(observation.status, "rejected")

    def test_changed_final_board_is_accepted(self) -> None:
        before = _bundle(0)
        changed = _bundle(255)
        frames = [changed, changed, changed]
        with (
            patch("main._capture_current", side_effect=frames),
            patch("main.POST_ACTION_INTERVAL", 0.0),
            patch("main.POST_ACTION_MIN_WAIT", 0.0),
        ):
            observation, _ = main.observe_action_result(object(), before)

        self.assertEqual(observation.status, "accepted")


if __name__ == "__main__":
    unittest.main()
