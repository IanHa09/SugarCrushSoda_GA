"""대표 화면 전체와 HOG 특징으로 비교해 "이미 본 화면인지" 찾는 색인.

작동 원리
  1. 특징 벡터(hog_feature): 화면을 흑백 128x256으로 줄이고, 픽셀마다 밝기가 바뀌는
     방향(그래디언트)을 구해 16px 칸마다 방향 9칸 히스토그램을 만듭니다. 이웃한 2x2 칸을
     묶어 정규화하므로 화면이 어두워지거나 밝아져도 값이 거의 변하지 않습니다.
     결과는 길이 3780, 크기 1(L2 정규화)인 벡터입니다.
  2. 색인(ScreenIndex): 노드 대표 이미지들의 벡터를 (노드 수 x 3780) 행렬 한 장에 쌓아 둡니다.
  3. 비교(nearest): 새 화면 벡터와 행렬을 한 번 곱하면 모든 대표 화면과의 코사인 유사도가
     나옵니다. 거리 = 1 - 유사도이고, 가장 가까운 노드의 거리가 임계값 이하면 같은 화면입니다.

LLM이 붙인 화면 타입과 상관없이 비교하므로, 같은 화면을 모델이 다른 타입으로 불러도
같은 노드로 모입니다(navigation.graph.GraphStore.observe가 가장 먼저 씁니다).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import cv2
import numpy as np

HOG_SIZE = (128, 256)  # (가로, 세로). 세로 화면 비율에 맞춘 축소 크기
HOG_CELL = 16          # 칸 한 변(px)
HOG_BINS = 9           # 방향 칸 수(0~180도를 20도씩)


def hog_feature(image: np.ndarray) -> np.ndarray:
    """화면 한 장의 HOG 벡터(길이 3780, L2 정규화). 무늬가 없으면 0 벡터입니다."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.resize(gray, HOG_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0

    # 1) 그래디언트: 픽셀마다 밝기 변화의 세기와 방향(부호 없는 0~180도).
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=1)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=1)
    magnitude = np.hypot(gx, gy)
    angle = np.rad2deg(np.arctan2(gy, gx)) % 180.0
    bins = np.minimum((angle / (180.0 / HOG_BINS)).astype(np.int64), HOG_BINS - 1)

    # 2) 칸(cell)마다 방향 히스토그램: 같은 방향 칸에 세기를 더합니다.
    height, width = gray.shape
    rows, cols = height // HOG_CELL, width // HOG_CELL
    cell_y = np.broadcast_to((np.arange(height) // HOG_CELL)[:, None], (height, width))
    cell_x = np.broadcast_to((np.arange(width) // HOG_CELL)[None, :], (height, width))
    histogram = np.zeros((rows, cols, HOG_BINS), np.float32)
    np.add.at(histogram, (cell_y, cell_x, bins), magnitude)

    # 3) 블록 정규화: 이웃 2x2 칸을 이어 붙여 블록마다 길이 1로 맞춥니다(밝기 변화에 강해짐).
    blocks = np.concatenate(
        [histogram[:-1, :-1], histogram[1:, :-1], histogram[:-1, 1:], histogram[1:, 1:]],
        axis=2,
    )
    blocks /= np.linalg.norm(blocks, axis=2, keepdims=True) + 1e-6

    vector = blocks.ravel()
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 1e-6 else vector


class ScreenIndex:
    """노드 ID 목록과 HOG 특징 행렬(노드 수 x 3780)."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.features = np.zeros((0, 0), np.float32)

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, node_id: str, image: np.ndarray) -> None:
        """노드 대표 이미지를 색인에 넣습니다(이미 있으면 바꿉니다)."""
        feature = hog_feature(image)
        if node_id in self.ids:
            self.features[self.ids.index(node_id)] = feature
            return
        self.ids.append(node_id)
        self.features = feature[None, :] if not len(self.features) else np.vstack([self.features, feature])

    def nearest(self, image: np.ndarray) -> tuple[str | None, float]:
        """가장 가까운 노드와 거리(0=같음 ~ 1=전혀 다름). 비었거나 무늬가 없으면 (None, 1.0)."""
        feature = hog_feature(image)
        if not self.ids or not feature.any():
            return None, 1.0
        distances = 1.0 - self.features @ feature  # 코사인 거리(벡터 길이가 1이라 내적 = 유사도)
        best = int(np.argmin(distances))
        return self.ids[best], float(distances[best])

    @classmethod
    def build(
        cls,
        node_ids: Iterable[str],
        load_image: Callable[[str], np.ndarray | None],
    ) -> "ScreenIndex":
        """대표 이미지로 색인을 만듭니다(이미지가 없는 노드는 건너뜁니다)."""
        index = cls()
        for node_id in node_ids:
            image = load_image(node_id)
            if image is not None:
                index.add(node_id, image)
        return index
