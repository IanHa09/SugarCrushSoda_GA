"""누르기 직전 버튼 위치를 한 번 더 확인합니다(1단계: 클릭 정확도).

조사 모델(gpt-4o-mini)이 화면 전체를 보고 준 좌표는 거칩니다. 실측
(run_2026-09-21_10-51-14)에서 버튼 좌표 78개 중 45개가 x=0.5였고, 탭 24번 중
13번은 화면을 전혀 바꾸지 못했습니다. 그래서 실제로 누를 버튼 하나에 대해서만:

  1. (zoom) 예상 위치 주변을 정사각형으로 잘라 확대하고, 가장자리에 열 글자(A, B,
     C...)와 행 숫자(1, 2, 3...)가 붙은 격자를 그려 위치 인식이 나은 모델
     (OPENAI_TAP_MODEL)에게 "이 버튼이 어느 칸, 어느 픽셀에 있나"를 묻습니다.
  2. (full) 확대본에서 못 찾았거나 확신이 낮으면 전체 화면에 같은 격자를 그려
     한 번 더 묻습니다. 첫 추정이 크게 빗나간 경우(실측: '!' 버튼이 450px 어긋남)를
     위한 것이고, 보통은 1번에서 끝나 탭 한 번에 API 1회가 추가됩니다.
  3. 두 번 다 못 찾으면 누르지 않습니다(없는 버튼을 누르면 엉뚱한 간선만 생깁니다).

모델 답은 격자 칸(cell)과 픽셀 좌표(x, y)를 둘 다 받아 서로 맞춰 봅니다
(resolve_location). 칸 이름은 가장자리 글자를 읽으면 되어 덜 틀리고, 픽셀은
더 정밀합니다.

API 호출이 실패하면(네트워크 등) 예전처럼 처음 좌표를 그대로 누르도록 error만
남깁니다. 탐색 전체를 멈추지 않기 위해서입니다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from config import (
    TAP_MODEL,
    TAP_REFINE_CROP_FRACTION,
    TAP_REFINE_FULL_FALLBACK,
    TAP_REFINE_MAX_OUTPUT_TOKENS,
    TAP_REFINE_MIN_CONFIDENCE,
)
from llm_request import request_structured_vision
from schemas import TapLocation


# 모델에 보낼 이미지의 긴 변(px). OpenAI 비전 high detail이 짧은 변을 768px로
# 맞추므로 그보다 조금 크게 둡니다.
DISPLAY_LONG_SIDE = 1024
# 잘라낸 영역을 이 배율 이상으로는 키우지 않습니다(너무 키우면 흐려집니다).
MAX_UPSCALE = 3.0
# 격자가 긴 변 기준으로 대략 이 개수의 칸으로 나뉘게 칸 크기를 정합니다.
TARGET_GRID_CELLS = 10
MIN_CELL_PX = 48

GRID_COLOR = (255, 255, 0)  # BGR 청록색
LABEL_TEXT_COLOR = (0, 255, 255)  # BGR 노란색
LABEL_OUTLINE_COLOR = (0, 0, 0)

SYSTEM_PROMPT = (
    "You locate one UI element precisely in a mobile game screenshot. "
    "Answer only about the requested element and never claim to find an element "
    "that is not visible."
)


@dataclass(frozen=True)
class GridView:
    """모델에 보낼 (잘라서 확대하고 격자를 그린) 이미지와 원본 좌표로 되돌리는 정보."""

    image: np.ndarray
    x0: int
    y0: int
    scale: float
    cell: int
    cols: int
    rows: int

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def to_source(self, x: float, y: float) -> tuple[int, int]:
        """표시 이미지 좌표를 원본(전체 캡처) 픽셀 좌표로 바꿉니다."""
        return round(self.x0 + x / self.scale), round(self.y0 + y / self.scale)


@dataclass
class TapRefineResult:
    """확대 재확인 결과. found=False이고 error가 없으면 '버튼을 못 찾음'입니다."""

    found: bool
    initial: tuple[int, int]
    x: int | None = None
    y: int | None = None
    used_pass: str | None = None
    passes: list[dict[str, Any]] = field(default_factory=list)
    api_calls: int = 0
    error: str | None = None

    @property
    def shift_px(self) -> float | None:
        if self.x is None or self.y is None:
            return None
        return round(math.hypot(self.x - self.initial[0], self.y - self.initial[1]), 1)

    def to_record(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "initial_px": list(self.initial),
            "refined_px": [self.x, self.y] if self.x is not None else None,
            "shift_px": self.shift_px,
            "used_pass": self.used_pass,
            "api_calls": self.api_calls,
            "error": self.error,
            "passes": self.passes,
        }


# -- 격자 ---------------------------------------------------------------------


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def parse_cell(cell: str, cols: int, rows: int) -> tuple[int, int] | None:
    """"C5" -> (열 2, 행 4). 격자 밖이거나 형식이 틀리면 None."""

    match = re.fullmatch(r"\s*([A-Za-z]+)\s*-?\s*(\d+)\s*", cell or "")
    if not match:
        return None
    letters, digits = match.group(1).upper(), match.group(2)
    col = 0
    for char in letters:
        col = col * 26 + (ord(char) - ord("A") + 1)
    col -= 1
    row = int(digits) - 1
    if not (0 <= col < cols and 0 <= row < rows):
        return None
    return col, row


def crop_box(
    width: int,
    height: int,
    center_x: int,
    center_y: int,
    side: int,
) -> tuple[int, int, int, int]:
    """(center_x, center_y)를 가운데로 하는 한 변 side의 사각형을 이미지 안으로 밀어
    넣습니다. 반환: (x0, y0, x1, y1), x1/y1은 끝(포함 안 함)."""

    box_w = min(side, width)
    box_h = min(side, height)
    x0 = min(max(center_x - box_w // 2, 0), width - box_w)
    y0 = min(max(center_y - box_h // 2, 0), height - box_h)
    return x0, y0, x0 + box_w, y0 + box_h


def _put_label(image: np.ndarray, text: str, origin: tuple[int, int], scale: float) -> None:
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                LABEL_OUTLINE_COLOR, 4, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                LABEL_TEXT_COLOR, 2, cv2.LINE_AA)


def build_grid_view(
    image: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    long_side: int = DISPLAY_LONG_SIDE,
) -> GridView:
    """box 영역을 잘라 긴 변이 long_side가 되게 키우고(최대 MAX_UPSCALE배) 격자를 그립니다."""

    x0, y0, x1, y1 = box
    crop = image[y0:y1, x0:x1]
    crop_h, crop_w = crop.shape[:2]
    scale = min(long_side / max(crop_w, crop_h), MAX_UPSCALE)
    display_w = max(1, round(crop_w * scale))
    display_h = max(1, round(crop_h * scale))
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    view = cv2.resize(crop, (display_w, display_h), interpolation=interpolation)

    cell = max(MIN_CELL_PX, round(max(display_w, display_h) / TARGET_GRID_CELLS))
    cols = math.ceil(display_w / cell)
    rows = math.ceil(display_h / cell)

    # 선은 반투명으로 그려 버튼 모양이 가려지지 않게 합니다.
    overlay = view.copy()
    for col in range(1, cols):
        x = col * cell
        cv2.line(overlay, (x, 0), (x, display_h - 1), GRID_COLOR, 1, cv2.LINE_AA)
    for row in range(1, rows):
        y = row * cell
        cv2.line(overlay, (0, y), (display_w - 1, y), GRID_COLOR, 1, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.55, view, 0.45, 0, dst=view)

    # 가장자리에만 글자를 붙여(체스판처럼) 칸 안쪽 버튼을 가리지 않습니다.
    font_scale = max(0.45, min(0.8, cell / 120))
    for col in range(cols):
        _put_label(view, column_name(col), (col * cell + 4, 18), font_scale)
    for row in range(rows):
        _put_label(view, str(row + 1), (3, row * cell + cell // 2 + 6), font_scale)

    return GridView(image=view, x0=x0, y0=y0, scale=scale, cell=cell, cols=cols, rows=rows)


def resolve_location(location: TapLocation, view: GridView) -> tuple[float, float] | None:
    """모델 답(칸 + 픽셀)을 표시 이미지 좌표 하나로 정합니다.

    - 픽셀이 이미지 안이고, 칸이 없거나 칸에서 한 칸 이내면 픽셀을 씁니다.
    - 픽셀이 칸과 크게 어긋나거나 이미지 밖이면 칸 중심을 씁니다.
    - 둘 다 못 쓰면 None."""

    if not location.found:
        return None
    cell = parse_cell(location.cell, view.cols, view.rows)
    pixel_ok = 0 <= location.x < view.width and 0 <= location.y < view.height

    if pixel_ok:
        if cell is None:
            return float(location.x), float(location.y)
        col, row = cell
        tolerance = view.cell
        left, top = col * view.cell - tolerance, row * view.cell - tolerance
        right, bottom = (col + 1) * view.cell + tolerance, (row + 1) * view.cell + tolerance
        if left <= location.x <= right and top <= location.y <= bottom:
            return float(location.x), float(location.y)

    if cell is not None:
        col, row = cell
        center_x = min((col + 0.5) * view.cell, view.width - 1)
        center_y = min((row + 0.5) * view.cell, view.height - 1)
        return center_x, center_y
    return None


# -- 모델 질의 ------------------------------------------------------------------


def _build_prompt(button, view: GridView, *, zoomed: bool) -> str:
    label = getattr(button, "label", "") or "(이름 없음)"
    role = getattr(button, "role", "unknown")
    reason = getattr(button, "reason", "") or "-"
    scope = (
        f"게임 화면의 일부를 {view.scale:.1f}배 확대한 것이다. 이전 추정 위치는 "
        "이미지 가운데 근처지만 틀렸을 수 있다."
        if zoomed
        else "게임 화면 전체다."
    )
    last_col = column_name(view.cols - 1)
    return f"""
이 이미지는 {scope}
이미지 크기: 가로 {view.width}px, 세로 {view.height}px.
청록색 격자: 위쪽 가장자리 글자(A~{last_col})가 열, 왼쪽 가장자리 숫자(1~{view.rows})가 행이다. 칸 하나는 {view.cell}px이다.

찾을 버튼: "{label}" (역할: {role})
참고 설명: {reason}

할 일:
- 이 버튼(실제로 누를 수 있는 버튼/아이콘/탭)이 보이면 found=true로 하고,
  버튼 중심이 들어 있는 칸을 cell에 "C5"처럼 적고, 버튼 중심의 픽셀 좌표를 x, y에 적는다(이 이미지 기준).
- 버튼이 안 보이거나, 잘려서 중심이 이미지 밖이거나, 비슷하지만 다른 버튼만 보이면
  found=false, cell="", x=-1, y=-1로 둔다.
- seen_label에는 그 위치에 실제로 보이는 글자나 아이콘 모양을 짧게 적는다.
- confidence는 0~1로 적는다.
"""


def _ask(
    client,
    button,
    view: GridView,
    *,
    zoomed: bool,
    model: str,
    request: Callable[..., Any],
) -> TapLocation:
    return request(
        client,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        prompt=_build_prompt(button, view, zoomed=zoomed),
        full_image=view.image,
        board_image=None,
        board_note="",
        text_format=TapLocation,
        max_output_tokens=TAP_REFINE_MAX_OUTPUT_TOKENS,
        timing_label="TAP REFINE TIMING",
        missing_response_message="버튼 위치 확인 응답을 받지 못했습니다.",
        full_image_detail="high",
    )


def refine_tap_target(
    client,
    image: np.ndarray,
    button,
    *,
    model: str = TAP_MODEL,
    crop_fraction: float = TAP_REFINE_CROP_FRACTION,
    min_confidence: float = TAP_REFINE_MIN_CONFIDENCE,
    full_fallback: bool = TAP_REFINE_FULL_FALLBACK,
    request: Callable[..., Any] = request_structured_vision,
) -> TapRefineResult:
    """버튼의 center(0~1) 주변을 확대해 실제 위치를 다시 찾고, 원본 픽셀 좌표를 돌려줍니다."""

    height, width = image.shape[:2]
    center = button.center
    initial = (
        min(max(round(center.x * width), 0), width - 1),
        min(max(round(center.y * height), 0), height - 1),
    )
    result = TapRefineResult(found=False, initial=initial)

    side = max(1, round(crop_fraction * max(width, height)))
    plans: list[tuple[str, tuple[int, int, int, int]]] = [
        ("zoom", crop_box(width, height, initial[0], initial[1], side)),
    ]
    if full_fallback:
        plans.append(("full", (0, 0, width, height)))

    for pass_name, box in plans:
        view = build_grid_view(image, box)
        try:
            location = _ask(
                client, button, view, zoomed=(pass_name == "zoom"), model=model, request=request
            )
        except Exception as error:  # noqa: BLE001 - API 오류로 탐색 전체를 멈추지 않습니다.
            result.error = f"{type(error).__name__}: {error}"
            return result
        result.api_calls += 1

        resolved = resolve_location(location, view)
        source = view.to_source(*resolved) if resolved is not None else None
        if source is not None:
            source = (min(max(source[0], 0), width - 1), min(max(source[1], 0), height - 1))
        result.passes.append({
            "pass": pass_name,
            "box": list(box),
            "found": location.found,
            "cell": location.cell,
            "x": location.x,
            "y": location.y,
            "confidence": location.confidence,
            "seen_label": location.seen_label,
            "resolved_px": list(source) if source is not None else None,
        })

        if source is not None and location.confidence >= min_confidence:
            result.found = True
            result.x, result.y = source
            result.used_pass = pass_name
            return result

    return result
