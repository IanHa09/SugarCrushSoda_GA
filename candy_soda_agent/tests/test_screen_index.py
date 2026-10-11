"""대표 화면 전체와 HOG 거리로 같은 화면을 찾는 색인 테스트."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.graph import GraphStore
from navigation.screen_index import ScreenIndex, hog_feature
from schemas import SurveyDecision
from ui_chrome import ChromeBands


def _screen(kind: str, height: int = 400, width: int = 240) -> np.ndarray:
    """모양이 다른 시험용 게임 화면(지도 / 상점 목록)."""
    image = np.zeros((height, width, 3), np.uint8)
    image[:] = (60, 120, 180)
    if kind == "map":
        for i, y in enumerate(range(40, height - 40, 60)):
            cv2.circle(image, (60 + (i % 2) * 120, y), 22, (40, 200, 240), -1)
            cv2.line(image, (60, y), (180, y + 60), (255, 255, 255), 3)
    else:
        for y in range(30, height - 30, 70):
            cv2.rectangle(image, (20, y), (220, y + 55), (230, 230, 230), -1)
            cv2.rectangle(image, (150, y + 15), (210, y + 40), (40, 180, 60), -1)
    return image


def _popup_over(background: np.ndarray) -> np.ndarray:
    """배경을 어둡게 덮고 가운데 작은 창을 띄운 화면."""
    image = (background * 0.4).astype(np.uint8)
    cv2.rectangle(image, (40, 120), (200, 280), (245, 240, 230), -1)
    cv2.rectangle(image, (80, 240), (160, 265), (40, 180, 60), -1)
    return image


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - float(hog_feature(a) @ hog_feature(b))


class HogFeatureTests(unittest.TestCase):
    def test_vector_length_and_norm(self) -> None:
        feature = hog_feature(_screen("map"))
        self.assertEqual(feature.shape, (3780,))  # (16-1) x (8-1) 블록 x 4칸 x 9방향
        self.assertAlmostEqual(float(np.linalg.norm(feature)), 1.0, places=4)
        self.assertFalse(hog_feature(np.full((50, 50, 3), 7, np.uint8)).any())  # 무늬 없음

    def test_brightness_and_small_noise_barely_change_it(self) -> None:
        screen = _screen("map")
        darker = (screen * 0.5).astype(np.uint8)
        noisy = np.clip(
            screen.astype(int) + np.random.default_rng(0).integers(-6, 7, screen.shape), 0, 255
        ).astype(np.uint8)
        self.assertLess(_distance(screen, darker), 0.02)
        self.assertLess(_distance(screen, noisy), 0.15)

    def test_different_layouts_and_popups_are_far(self) -> None:
        self.assertGreater(_distance(_screen("map"), _screen("shop")), 0.5)
        self.assertGreater(_distance(_screen("map"), _popup_over(_screen("map"))), 0.3)


class ScreenIndexTests(unittest.TestCase):
    def test_nearest_add_and_replace(self) -> None:
        index = ScreenIndex()
        self.assertEqual(index.nearest(_screen("map")), (None, 1.0))
        index.add("map", _screen("map"))
        index.add("shop", _screen("shop"))
        node_id, distance = index.nearest(_screen("shop"))
        self.assertEqual(node_id, "shop")
        self.assertAlmostEqual(distance, 0.0, places=4)
        index.add("shop", _screen("map"))  # 같은 노드는 바꿔 넣음
        self.assertEqual(len(index), 2)

    def test_build_skips_missing_images(self) -> None:
        images = {"a": _screen("map"), "b": None}
        index = ScreenIndex.build(["a", "b"], images.get)
        self.assertEqual(index.ids, ["a"])


@contextmanager
def _store(hog: float = 0.15):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "navigation"
        with (
            patch("navigation.graph.ROOT", root),
            patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
            patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
            patch("navigation.graph.IMAGE_DIR", root / "representatives"),
            patch("navigation.graph.SCREEN_HOG_MAX_DISTANCE", hog),
            patch("navigation.graph.SCREEN_MATCH_MAX_BUTTON_DIFF", 0),
        ):
            yield GraphStore()


class GraphHogMatchTests(unittest.TestCase):
    def test_same_screen_with_different_llm_type_is_one_node(self) -> None:
        # 실측: 픽셀까지 같은 안내 팝업을 모델이 menu / popup으로 다르게 불러 노드가 갈렸던 사례.
        with _store() as store:
            first = store.observe(SurveyDecision(screen_type="menu"), _screen("map"))
            second = store.observe(SurveyDecision(screen_type="popup"), _screen("map"))
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.visits, 2)
            self.assertEqual(store.last_hog_match[0], first.id)

    def test_popup_over_a_screen_and_other_screens_stay_apart(self) -> None:
        with _store() as store:
            home = store.observe(SurveyDecision(screen_type="home"), _screen("map"))
            popup = store.observe(SurveyDecision(screen_type="popup"), _popup_over(_screen("map")))
            shop = store.observe(SurveyDecision(screen_type="shop"), _screen("shop"))
            self.assertEqual(len({home.id, popup.id, shop.id}), 3)
            # 이미 본 화면은 다시 와도 그 노드로 갑니다(직전 화면이 아니라 대표 화면 전체와 비교).
            again = store.observe(SurveyDecision(screen_type="unknown"), _screen("map"))
            self.assertEqual(again.id, home.id)

    def test_index_is_rebuilt_from_saved_representatives(self) -> None:
        with _store() as store:
            node = store.observe(SurveyDecision(screen_type="home"), _screen("map"))
            fresh = GraphStore()  # 다음 세션: 색인은 representatives/ 이미지로 다시 만듭니다.
            self.assertEqual(fresh.observe(SurveyDecision(screen_type="menu"), _screen("map")).id, node.id)

    def test_shared_bars_are_left_out_of_the_comparison(self) -> None:
        first, second = _screen("map"), _screen("map")
        first[350:] = (200, 50, 50)
        second[350:] = (50, 200, 50)
        with _store() as store:
            store.set_chrome_bands(ChromeBands(bottom=50, height=400, width=240))
            store.observe(SurveyDecision(screen_type="home"), first)
            store.observe(SurveyDecision(screen_type="home"), second)
            self.assertAlmostEqual(store.last_hog_match[1], 0.0, places=4)

    def test_zero_threshold_turns_it_off(self) -> None:
        with _store(hog=0.0) as store:
            first = store.observe(SurveyDecision(screen_type="menu"), _screen("map"))
            second = store.observe(SurveyDecision(screen_type="popup"), _screen("map"))
            self.assertNotEqual(first.id, second.id)  # 지문 단계는 타입이 다르면 비교하지 않음


if __name__ == "__main__":
    unittest.main()
