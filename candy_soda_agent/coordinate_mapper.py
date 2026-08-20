"""보드 셀 좌표를 화면 절대 좌표로 변환합니다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


def cell_center(
    row: int,
    col: int,
    board_region: dict[str, int],
    rows: int,
    cols: int,
) -> Point:
    if rows <= 0 or cols <= 0:
        raise ValueError("rows와 cols는 양수여야 합니다.")
    if not 1 <= row <= rows or not 1 <= col <= cols:
        raise ValueError(f"올바르지 않은 셀입니다: ({row}, {col})")

    required_keys = {"left", "top", "width", "height"}
    missing_keys = required_keys.difference(board_region)
    if missing_keys:
        raise ValueError(f"보드 영역 키가 없습니다: {sorted(missing_keys)}")
    if board_region["width"] <= 0 or board_region["height"] <= 0:
        raise ValueError("보드 영역의 너비와 높이는 양수여야 합니다.")

    cell_width = board_region["width"] / cols
    cell_height = board_region["height"] / rows

    return Point(
        x=round(board_region["left"] + (col - 0.5) * cell_width),
        y=round(board_region["top"] + (row - 0.5) * cell_height),
    )
