"""여러 화면에 공통으로 보이는 UI(네비게이션 바, 재화 바)를 찾고 활용합니다.

두 가지 신호를 씁니다.

1. 이미지(공통 띠): 그래프 노드 대표 이미지들을 행 단위로 비교해, 서로 다른 화면
   CHROME_MIN_SCREENS개 이상이 화면 위/아래에서 같은 줄무늬를 공유하면 그 높이만큼을
   공통 띠로 봅니다. 어두운 팝업 배경처럼 무늬가 없는 줄은 비교에서 뺍니다(실측:
   아래 150px 탭 바만 잡히고, 어두운 단색 배경끼리는 잡히지 않음).
2. 위치 + 이름(공통 버튼): 같은 action_key 버튼이 서로 다른 화면 여러 개에서 같은
   위치에 보이면 공통 버튼입니다. 공통 띠 안의 버튼은 2개 화면이면 충분합니다.
   닫기/뒤로/확인/다음처럼 화면마다 하는 일이 다른 버튼은 위치가 같아도 공통으로
   보지 않습니다(팝업 X 버튼은 늘 같은 자리에 있지만 돌아가는 곳이 다릅니다).

활용(navigator.py):
- 공통 버튼은 원장에서 화면별 항목 대신 GLOBAL_SCREEN_ID 항목 하나로 한 번만 눌러 봅니다.
- 눌러서 간 화면을 알면, 그 버튼이 보이는 모든 화면에서 쓸 수 있는 지름길 간선
  (teleport_edges)으로 경로 계획에 씁니다.
- 공통 띠는 "같은 화면인가" 지문 비교에서 빼서(GraphStore) 탭 강조·배지 차이로
  노드가 갈리지 않게 합니다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np

from navigation.ledger import GLOBAL_SCREEN_ID
from navigation.policy import canonical_action_label
from schemas import NavigationEdge

# 행 비교용으로 이미지를 이 폭으로 줄입니다(1348x757 -> 약 171x96).
PROFILE_WIDTH = 96
# 한 줄에서 밝기 차이가 이 값보다 작은 칸의 비율이 ROW_SIMILAR_FRACTION 이상이면 같은 줄.
PIXEL_TOLERANCE = 0.1
ROW_SIMILAR_FRACTION = 0.5
# 두 이미지 모두 그 줄의 밝기 표준편차가 이 값 이상이어야 비교합니다(무늬 없는 줄 제외).
ROW_MIN_TEXTURE = 0.05
# 띠 안에서 이만큼의 연속된 줄까지는 달라도 띠가 이어진다고 봅니다(탭 강조 등).
ROW_GAP_TOLERANCE = 2
# 전체가 이 값보다 덜 다르면 같은 화면(쪼개진 노드)이라 띠 근거로 쓰지 않습니다.
DISTINCT_SCREEN_MIN_DIFF = 0.02
MIN_BAND_FRACTION = 0.03
MAX_BAND_FRACTION = 0.25

# 화면마다 하는 일이 달라 공통 버튼이 될 수 없는 행동(canonical_action_label 기준).
NON_GLOBAL_CANONICAL = frozenset({"close", "back"})
NON_GLOBAL_TOKENS = ("확인", "계속", "다음", "닫기", "취소", "나중에", "ok", "next", "continue", "confirm", "cancel")


@dataclass
class ChromeBands:
    """공통 띠 높이(원본 픽셀). top/bottom이 0이면 그쪽 띠는 없습니다."""

    top: int = 0
    bottom: int = 0
    height: int = 0
    width: int = 0
    support: int = 0

    @property
    def active(self) -> bool:
        return self.height > 0 and (self.top > 0 or self.bottom > 0)

    def contains(self, y: float) -> bool:
        if not self.active:
            return False
        return y < self.top or y >= self.height - self.bottom

    def content(self, image: np.ndarray) -> np.ndarray:
        """공통 띠를 뺀 본문 영역. 크기가 다르거나 남는 게 없으면 원본을 돌려줍니다."""
        if not self.active or image.shape[0] != self.height:
            return image
        bottom_edge = self.height - self.bottom
        if bottom_edge - self.top < self.height * 0.2:
            return image
        return image[self.top:bottom_edge]


def _profile(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height = max(1, round(gray.shape[0] * PROFILE_WIDTH / gray.shape[1]))
    small = cv2.resize(gray, (PROFILE_WIDTH, height), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32) / 255.0


def _similar_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    similar = (np.abs(a - b) < PIXEL_TOLERANCE).mean(axis=1) >= ROW_SIMILAR_FRACTION
    textured = np.minimum(a.std(axis=1), b.std(axis=1)) >= ROW_MIN_TEXTURE
    return similar & textured


def _run_length(good: np.ndarray, *, from_bottom: bool) -> int:
    """가장자리에서 시작해 같은 줄이 이어지는 길이(사이 ROW_GAP_TOLERANCE줄 허용)."""
    sequence = good[::-1] if from_bottom else good
    last_good = 0
    gap = 0
    for index, value in enumerate(sequence):
        if value:
            last_good = index + 1
            gap = 0
        else:
            gap += 1
            if gap > ROW_GAP_TOLERANCE:
                break
    return last_good


def detect_chrome_bands(
    images: Iterable[np.ndarray],
    *,
    min_screens: int = 3,
) -> ChromeBands:
    """서로 다른 화면 min_screens개 이상이 공유하는 위/아래 띠 높이를 찾습니다."""

    images = [image for image in images if image is not None]
    if not images:
        return ChromeBands()
    height, width = images[0].shape[:2]
    profiles = [_profile(image) for image in images if image.shape[:2] == (height, width)]
    return detect_from_profiles(profiles, height=height, width=width, min_screens=min_screens)


def detect_from_profiles(
    profiles: list[np.ndarray],
    *,
    height: int,
    width: int,
    min_screens: int = 3,
) -> ChromeBands:
    """_profile()로 줄인 이미지들로 공통 띠를 찾습니다.

    화면 i마다 다른 화면 j와 가장자리에서부터 같은 줄이 몇 줄 이어지는지 재고, 그중
    (min_screens-1)번째로 긴 값을 "i를 포함해 min_screens개 화면이 공유하는 띠"로
    봅니다. 모든 i 중 가장 큰 값이 띠 높이입니다."""

    if len(profiles) < min_screens:
        return ChromeBands()
    rows = profiles[0].shape[0]
    count = len(profiles)
    whole = np.array([
        [float(np.abs(profiles[i] - profiles[j]).mean()) for j in range(count)]
        for i in range(count)
    ])

    def _side(from_bottom: bool) -> tuple[int, int]:
        best_rows, best_support = 0, 0
        for i in range(count):
            runs = sorted(
                (
                    _run_length(_similar_rows(profiles[i], profiles[j]), from_bottom=from_bottom)
                    for j in range(count)
                    if j != i and whole[i, j] >= DISTINCT_SCREEN_MIN_DIFF
                ),
                reverse=True,
            )
            if len(runs) < min_screens - 1:
                continue
            shared = runs[min_screens - 2]
            if shared > best_rows:
                best_rows = shared
                best_support = 1 + sum(1 for run in runs if run >= shared)
        return best_rows, best_support

    def _to_pixels(row_count: int) -> int:
        pixels = round(row_count * height / rows)
        if pixels < height * MIN_BAND_FRACTION:
            return 0
        return min(pixels, round(height * MAX_BAND_FRACTION))

    bottom_rows, bottom_support = _side(from_bottom=True)
    top_rows, top_support = _side(from_bottom=False)
    bands = ChromeBands(
        top=_to_pixels(top_rows),
        bottom=_to_pixels(bottom_rows),
        height=height,
        width=width,
    )
    bands.support = max(
        bottom_support if bands.bottom else 0,
        top_support if bands.top else 0,
    )
    return bands


def can_be_global(label: str) -> bool:
    """화면마다 하는 일이 같을 수 있는 버튼인지(닫기·뒤로·확인류는 제외)."""
    if canonical_action_label(label) in NON_GLOBAL_CANONICAL:
        return False
    lowered = label.casefold()
    return not any(token in lowered for token in NON_GLOBAL_TOKENS)


class UiChromeStore:
    """공통 띠와 버튼 위치 관찰을 run 폴더의 ui_chrome.json에 쌓습니다."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        min_screens: int = 3,
        global_min_screens: int = 3,
        max_spread_px: float = 30.0,
    ) -> None:
        if path is None:
            from config import NAVIGATION_UI_CHROME_PATH

            path = NAVIGATION_UI_CHROME_PATH
        self._path = path
        self.min_screens = min_screens
        self.global_min_screens = global_min_screens
        self.max_spread_px = max_spread_px
        self.bands = ChromeBands()
        self.actions: dict[str, dict[str, Any]] = {}
        self._bands_node_ids: list[str] = []
        self._profile_cache: dict[str, tuple[int, int, np.ndarray]] = {}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self.bands = ChromeBands(**data.get("bands", {}))
                self.actions = data.get("actions", {})
                self._bands_node_ids = list(data.get("bands_node_ids", []))
            except (json.JSONDecodeError, TypeError):
                pass

    # -- 공통 띠 -------------------------------------------------------------

    def update_bands(
        self,
        node_ids: list[str],
        load_image: Callable[[str], np.ndarray | None],
    ) -> bool:
        """노드 구성이 바뀌었으면 대표 이미지로 공통 띠를 다시 찾습니다. 바뀌면 True."""

        if sorted(node_ids) == sorted(self._bands_node_ids):
            return False
        # 노드 대표 이미지는 처음 저장된 뒤 바뀌지 않으므로 줄인 이미지를 캐시합니다.
        size: tuple[int, int] | None = None
        profiles = []
        for node_id in node_ids:
            cached = self._profile_cache.get(node_id)
            if cached is None:
                image = load_image(node_id)
                if image is None:
                    continue
                cached = (image.shape[0], image.shape[1], _profile(image))
                self._profile_cache[node_id] = cached
            height, width, profile = cached
            if size is None:
                size = (height, width)
            if (height, width) == size:
                profiles.append(profile)
        previous = (self.bands.top, self.bands.bottom)
        self._bands_node_ids = list(node_ids)
        if size is None:
            self.bands = ChromeBands()
        else:
            self.bands = detect_from_profiles(
                profiles, height=size[0], width=size[1], min_screens=self.min_screens
            )
        self.save()
        return (self.bands.top, self.bands.bottom) != previous

    # -- 공통 버튼 -----------------------------------------------------------

    def observe(
        self,
        node_id: str,
        action_key: str,
        label: str,
        x: float,
        y: float,
        *,
        region: str = "unknown",
        image_height: int = 0,
    ) -> bool:
        """버튼 위치(와 LLM이 본 영역)를 기록하고, 이번 관찰로 공통 버튼이 새로 됐으면 True."""

        before = self.is_global(action_key)
        entry = self.actions.setdefault(action_key, {"label": label, "positions": {}})
        entry["positions"][node_id] = [round(x, 1), round(y, 1)]
        entry.setdefault("regions", {})[node_id] = region
        if image_height:
            entry["height"] = image_height
        if label and not entry.get("label"):
            entry["label"] = label
        return self.is_global(action_key) and not before

    def is_global(self, action_key: str) -> bool:
        """공통 버튼 판정. 셋 중 하나면 공통입니다.

        1. 서로 다른 화면 global_min_screens개 이상에서 같은 자리(max_spread_px 이내)
        2. 이미지로 찾은 공통 띠 안에서 2개 화면 이상 같은 자리
        3. LLM이 2개 화면 이상에서 bottom_nav/top_bar라고 했고, 그 위치가 화면 맨 아래
           (높이의 75% 아래) 또는 맨 위(20% 위)에 있음. 조사 모델 좌표는 탭 하나 폭만큼
           어긋나기도 해서(실측 '홈' 탭 103px 차이) 위치만으로는 놓치는 경우를 보완합니다."""

        entry = self.actions.get(action_key)
        if not entry or not can_be_global(entry.get("label", "")):
            return False
        positions = list(entry["positions"].values())
        if not positions:
            return False
        center_x = median(p[0] for p in positions)
        center_y = median(p[1] for p in positions)
        clustered = [
            p for p in positions
            if abs(p[0] - center_x) <= self.max_spread_px
            and abs(p[1] - center_y) <= self.max_spread_px
        ]
        if len(clustered) >= self.global_min_screens:
            return True
        if len(clustered) >= 2 and self.bands.contains(center_y):
            return True

        height = entry.get("height") or self.bands.height
        regions = entry.get("regions") or {}
        if not height:
            return False
        nav_y = [
            entry["positions"][node][1]
            for node, region in regions.items()
            if node in entry["positions"]
            and (
                (region == "bottom_nav" and entry["positions"][node][1] >= height * 0.75)
                or (region == "top_bar" and entry["positions"][node][1] <= height * 0.20)
            )
        ]
        return len(nav_y) >= 2

    def global_keys(self) -> list[str]:
        return [key for key in self.actions if self.is_global(key)]

    def screens_showing(self, action_key: str) -> list[str]:
        entry = self.actions.get(action_key) or {}
        return list(entry.get("positions", {}))

    def teleport_edges(self, ledger) -> list[NavigationEdge]:
        """공통 버튼을 눌러 간 곳을 알면, 그 버튼이 보이는 모든 화면에서 그곳으로 가는
        지름길 간선을 만듭니다(그래프 파일에는 저장하지 않고 경로 계획에만 씁니다)."""

        edges: list[NavigationEdge] = []
        for key in self.global_keys():
            entry = ledger.ledger.entries.get(f"{GLOBAL_SCREEN_ID}:{key}")
            if entry is None or entry.verdict not in {"new_screen", "known_screen"}:
                continue
            target = entry.target_screen_id
            if not target:
                continue
            label = self.actions[key].get("label") or entry.action_label
            for source in self.screens_showing(key):
                if source != target:
                    edges.append(NavigationEdge(
                        source=source, target=target, action=label, action_key=key,
                    ))
        return edges

    def to_dict(self) -> dict[str, Any]:
        return {
            "bands": asdict(self.bands),
            "bands_node_ids": self._bands_node_ids,
            "actions": self.actions,
            "global_actions": self.global_keys(),
        }

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
