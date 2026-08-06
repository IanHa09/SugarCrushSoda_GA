"""검증된 셀 교환을 실제 마우스 행동으로 변환합니다."""

from __future__ import annotations

from collections.abc import Callable

from config import (
    ACTION_PAUSE,
    COLS,
    DRAG_DURATION,
    DRY_RUN,
    GAME_WINDOW_TITLE,
    ROWS,
)
from coordinate_mapper import cell_center


def execute_decision(
    decision,
    board_region: dict[str, int] | None,
    *,
    dry_run: bool = DRY_RUN,
    expected_window_title: str = GAME_WINDOW_TITLE,
    cancel_check: Callable[[], str | None] | None = None,
) -> bool:
    if decision.action != "swap" or not decision.source or not decision.target:
        return False
    if board_region is None:
        raise ValueError("swap 실행에는 보드 영역이 필요합니다.")

    start = cell_center(
        decision.source.row,
        decision.source.col,
        board_region,
        ROWS,
        COLS,
    )
    end = cell_center(
        decision.target.row,
        decision.target.col,
        board_region,
        ROWS,
        COLS,
    )

    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)

    if dry_run:
        print(f"[DRY RUN] swap {start.x},{start.y} -> {end.x},{end.y}")
        return True

    if not expected_window_title:
        raise RuntimeError("실제 클릭에는 GAME_WINDOW_TITLE 설정이 필요합니다.")

    import pyautogui

    active_title = pyautogui.getActiveWindowTitle() or ""
    if expected_window_title.casefold() not in active_title.casefold():
        raise RuntimeError(
            "활성 창이 게임 창과 일치하지 않습니다: "
            f"expected={expected_window_title!r}, actual={active_title!r}"
        )
    board_right = board_region["left"] + board_region["width"]
    board_bottom = board_region["top"] + board_region["height"]
    for point in (start, end):
        if not (
            board_region["left"] <= point.x <= board_right
            and board_region["top"] <= point.y <= board_bottom
        ):
            raise RuntimeError("계산된 swap 좌표가 보드 영역을 벗어났습니다.")

    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = ACTION_PAUSE
    pyautogui.moveTo(start.x, start.y)

    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)

    pyautogui.dragTo(
        end.x,
        end.y,
        duration=DRAG_DURATION,
        button="left",
    )
    return True
