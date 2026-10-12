"""누르기 직전 확대 재확인(tap_refine) 테스트."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from schemas import SurveyButtonCandidate, TapLocation
from tap_refine import (
    GridView,
    build_grid_view,
    column_name,
    crop_box,
    parse_cell,
    refine_tap_target,
    resolve_location,
)


def _button(x: float = 0.5, y: float = 0.7) -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label="레벨 10",
        role="progression",
        center={"x": x, "y": y},
        confidence=0.9,
        reason="레벨 10 시작",
    )


class FakeRequest:
    """request_structured_vision 대신 미리 정한 답을 차례로 돌려줍니다."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, client, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class GridTests(unittest.TestCase):
    def test_column_names(self) -> None:
        self.assertEqual(column_name(0), "A")
        self.assertEqual(column_name(25), "Z")
        self.assertEqual(column_name(26), "AA")

    def test_parse_cell(self) -> None:
        self.assertEqual(parse_cell("C5", cols=10, rows=10), (2, 4))
        self.assertEqual(parse_cell(" c-5 ", cols=10, rows=10), (2, 4))
        self.assertIsNone(parse_cell("K1", cols=10, rows=10))  # 열 범위 밖
        self.assertIsNone(parse_cell("A0", cols=10, rows=10))
        self.assertIsNone(parse_cell("", cols=10, rows=10))
        self.assertIsNone(parse_cell("5C", cols=10, rows=10))

    def test_crop_box_is_pushed_inside_the_image(self) -> None:
        self.assertEqual(crop_box(757, 1348, 10, 10, 540), (0, 0, 540, 540))
        self.assertEqual(crop_box(757, 1348, 750, 1340, 540), (217, 808, 757, 1348))
        # 한 변이 이미지보다 크면 이미지 크기로 줄입니다.
        self.assertEqual(crop_box(300, 1348, 150, 600, 540), (0, 330, 300, 870))

    def test_grid_view_upscales_crop_and_maps_back(self) -> None:
        image = np.zeros((1348, 757, 3), dtype=np.uint8)
        view = build_grid_view(image, (100, 600, 612, 1112))
        self.assertEqual(max(view.width, view.height), 1024)
        self.assertAlmostEqual(view.scale, 2.0)
        self.assertEqual(view.to_source(0, 0), (100, 600))
        self.assertEqual(view.to_source(1024, 1024), (612, 1112))
        self.assertGreaterEqual(view.cols, 8)

    def test_grid_view_never_upscales_more_than_three_times(self) -> None:
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        view = build_grid_view(image, (0, 0, 100, 100))
        self.assertAlmostEqual(view.scale, 3.0)


class ResolveLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = GridView(
            image=np.zeros((1000, 1000, 3), dtype=np.uint8),
            x0=0, y0=0, scale=1.0, cell=100, cols=10, rows=10,
        )

    def test_pixel_used_when_it_agrees_with_cell(self) -> None:
        location = TapLocation(found=True, cell="C5", x=250, y=450, confidence=0.9)
        self.assertEqual(resolve_location(location, self.view), (250.0, 450.0))

    def test_cell_center_used_when_pixel_disagrees(self) -> None:
        location = TapLocation(found=True, cell="C5", x=900, y=100, confidence=0.9)
        self.assertEqual(resolve_location(location, self.view), (250.0, 450.0))

    def test_pixel_used_when_cell_is_missing(self) -> None:
        location = TapLocation(found=True, cell="", x=10, y=20, confidence=0.9)
        self.assertEqual(resolve_location(location, self.view), (10.0, 20.0))

    def test_none_when_nothing_usable_or_not_found(self) -> None:
        self.assertIsNone(resolve_location(
            TapLocation(found=True, cell="zz", x=-1, y=-1), self.view
        ))
        self.assertIsNone(resolve_location(
            TapLocation(found=False, cell="C5", x=250, y=450), self.view
        ))


class RefineTapTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = np.zeros((1348, 757, 3), dtype=np.uint8)

    def test_zoom_pass_moves_tap_to_refined_point(self) -> None:
        # 예상 위치 (378, 944) 주변 540px 정사각형 -> 확대 배율 1024/540.
        request = FakeRequest([TapLocation(found=True, cell="", x=512, y=700, confidence=0.9)])
        result = refine_tap_target(
            object(), self.image, _button(), crop_fraction=0.4, request=request
        )
        self.assertTrue(result.found)
        self.assertEqual(result.used_pass, "zoom")
        self.assertEqual(result.api_calls, 1)
        self.assertEqual(result.initial, (378, 944))
        # 잘라낸 영역 왼쪽 위는 (108, 674), 배율은 1024/539.
        box = result.passes[0]["box"]
        scale = 1024 / (box[2] - box[0])
        self.assertEqual(result.x, round(box[0] + 512 / scale))
        self.assertEqual(result.y, round(box[1] + 700 / scale))
        self.assertEqual(request.calls[0]["full_image_detail"], "high")
        self.assertIn("레벨 10", request.calls[0]["prompt"])

    def test_falls_back_to_full_screen_when_zoom_misses(self) -> None:
        request = FakeRequest([
            TapLocation(found=False),
            TapLocation(found=True, cell="", x=100, y=900, confidence=0.8),
        ])
        result = refine_tap_target(
            object(), self.image, _button(), full_fallback=True, request=request
        )
        self.assertTrue(result.found)
        self.assertEqual(result.used_pass, "full")
        self.assertEqual(result.api_calls, 2)
        self.assertEqual(result.passes[1]["box"], [0, 0, 757, 1348])

    def test_low_confidence_zoom_answer_triggers_full_pass(self) -> None:
        request = FakeRequest([
            TapLocation(found=True, cell="", x=512, y=512, confidence=0.2),
            TapLocation(found=False),
        ])
        result = refine_tap_target(
            object(), self.image, _button(), min_confidence=0.5, full_fallback=True,
            request=request,
        )
        self.assertFalse(result.found)
        self.assertIsNone(result.error)
        self.assertEqual(result.api_calls, 2)

    def test_no_full_pass_when_disabled(self) -> None:
        request = FakeRequest([TapLocation(found=False)])
        result = refine_tap_target(
            object(), self.image, _button(), full_fallback=False, request=request
        )
        self.assertFalse(result.found)
        self.assertEqual(result.api_calls, 1)

    def test_api_error_is_reported_not_raised(self) -> None:
        request = FakeRequest([RuntimeError("network down")])
        result = refine_tap_target(object(), self.image, _button(), request=request)
        self.assertFalse(result.found)
        self.assertIn("network down", result.error)
        record = result.to_record()
        self.assertEqual(record["initial_px"], [378, 944])
        self.assertIsNone(record["refined_px"])


if __name__ == "__main__":
    unittest.main()
