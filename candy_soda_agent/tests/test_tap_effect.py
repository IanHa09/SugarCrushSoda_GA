"""탭 뒤 화면 대기·변화 판정(tap_effect)과 bbox 중심 계산 테스트."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from schemas import SurveyButtonCandidate, SurveyDecision
from survey import apply_bbox_centers
from tap_effect import same_screen, wait_for_settle


def _frame(value: int) -> np.ndarray:
    return np.full((20, 20, 3), value, dtype=np.uint8)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class WaitForSettleTests(unittest.TestCase):
    def _run(self, frames, **kwargs):
        clock = FakeClock()
        queue = list(frames)
        last = queue[-1]

        def grab():
            return queue.pop(0) if queue else last

        return wait_for_settle(
            grab,
            _frame(0),
            min_wait=0.5,
            interval=0.25,
            timeout=5.0,
            stable_frames=2,
            stable_threshold=0.01,
            change_threshold=0.01,
            sleep=clock.sleep,
            clock=clock,
            **kwargs,
        )

    def test_transition_is_detected_after_animation_settles(self) -> None:
        # 애니메이션(값이 계속 바뀜) 뒤 새 화면(200)에서 멈춥니다.
        effect = self._run([_frame(50), _frame(120), _frame(200), _frame(200), _frame(200)])
        self.assertTrue(effect.changed)
        self.assertTrue(effect.settled)
        self.assertGreater(effect.final_change, 0.5)
        self.assertEqual(effect.frames, 5)

    def test_unchanged_screen_is_reported_as_not_changed(self) -> None:
        effect = self._run([_frame(0), _frame(0), _frame(0)])
        self.assertFalse(effect.changed)
        self.assertTrue(effect.settled)
        self.assertEqual(effect.final_change, 0.0)

    def test_timeout_when_screen_never_settles(self) -> None:
        frames = [_frame(value) for value in range(0, 250, 10)]
        effect = self._run(frames)
        self.assertFalse(effect.settled)
        self.assertLessEqual(effect.waited, 5.0 + 0.25)
        record = effect.to_record()
        self.assertIn("final_change", record)

    def test_same_screen(self) -> None:
        self.assertTrue(same_screen(_frame(10), _frame(10)))
        self.assertFalse(same_screen(_frame(10), _frame(200)))
        self.assertFalse(same_screen(None, _frame(10)))
        self.assertFalse(same_screen(_frame(10), np.zeros((5, 5, 3), dtype=np.uint8)))


class BboxCenterTests(unittest.TestCase):
    def test_center_is_recomputed_from_bbox(self) -> None:
        decision = SurveyDecision(button_candidates=[
            SurveyButtonCandidate(
                label="설정",
                role="structure",
                bbox={"x1": 680, "y1": 10, "x2": 740, "y2": 70},
                center={"x": 0.5, "y": 0.5},
                confidence=0.9,
            ),
        ])
        apply_bbox_centers(decision, 757, 1348)
        center = decision.button_candidates[0].center
        self.assertAlmostEqual(center.x, 710 / 757, places=3)
        self.assertAlmostEqual(center.y, 40 / 1348, places=3)

    def test_bbox_is_clamped_and_invalid_bbox_is_dropped(self) -> None:
        decision = SurveyDecision(button_candidates=[
            SurveyButtonCandidate(
                label="닫기", role="safe_navigation",
                bbox={"x1": 700, "y1": 0, "x2": 900, "y2": 40}, confidence=0.9,
            ),
            SurveyButtonCandidate(
                label="뒤집힘", role="structure",
                bbox={"x1": 300, "y1": 300, "x2": 200, "y2": 400},
                center={"x": 0.2, "y": 0.3}, confidence=0.9,
            ),
            SurveyButtonCandidate(label="bbox 없음", role="structure",
                                  center={"x": 0.1, "y": 0.9}, confidence=0.9),
        ])
        apply_bbox_centers(decision, 757, 1348)
        clamped, flipped, plain = decision.button_candidates
        self.assertEqual(clamped.bbox.x2, 756)
        self.assertAlmostEqual(clamped.center.x, (700 + 756) / 2 / 757, places=3)
        self.assertIsNone(flipped.bbox)
        self.assertEqual((flipped.center.x, flipped.center.y), (0.2, 0.3))
        self.assertEqual((plain.center.x, plain.center.y), (0.1, 0.9))


if __name__ == "__main__":
    unittest.main()
