"""검증된 셀 교환을 실제 마우스 행동으로 변환합니다."""

from __future__ import annotations

from collections.abc import Callable
import subprocess
import sys
import time

from config import (
    ACTION_PAUSE,
    COLS,
    DRAG_DURATION,
    DRY_RUN,
    GAME_WINDOW_TITLE,
    ROWS,
    SURVEY_ALLOW_TAPS,
)
from coordinate_mapper import cell_center


def _application_matches(expected: str, actual: str) -> bool:
    return bool(expected and expected.casefold() in actual.casefold())


def active_application_name() -> str:
    """현재 전면 앱 이름을 운영체제에 맞는 방식으로 반환합니다."""

    if sys.platform == "darwin":
        try:
            import AppKit

            application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            return (application.localizedName() if application else "") or ""
        except ImportError as error:
            raise RuntimeError(
                "macOS 활성 앱 확인에는 pyobjc-framework-Cocoa가 필요합니다."
            ) from error

    import pyautogui

    get_title = getattr(pyautogui, "getActiveWindowTitle", None)
    if get_title is None:
        raise RuntimeError("현재 운영체제에서 활성 창 제목 확인을 지원하지 않습니다.")
    return get_title() or ""


def _wait_until_active(
    expected: str,
    *,
    timeout: float = 2.0,
    interval: float = 0.1,
) -> bool:
    """지정한 앱이 전면에 나타날 때까지 짧게 재시도합니다."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _application_matches(expected, active_application_name()):
            return True
        time.sleep(interval)
    return False


def _run_focus_command(command: list[str]) -> None:
    """외부 명령을 실행해 앱을 전면 전환하고 실패하면 예외를 냅니다."""

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"앱 활성화 명령 실패: {command!r}")


def activate_application(expected: str) -> None:
    """macOS에서 이름이 일치하는 앱을 전면으로 전환합니다."""

    if sys.platform != "darwin":
        raise RuntimeError("자동 앱 활성화는 현재 macOS에서만 지원합니다.")

    try:
        import AppKit
    except ImportError as error:
        raise RuntimeError(
            "macOS 앱 활성화에는 pyobjc-framework-Cocoa가 필요합니다."
        ) from error

    workspace = AppKit.NSWorkspace.sharedWorkspace()
    applications = [
        application
        for application in workspace.runningApplications()
        if _application_matches(expected, application.localizedName() or "")
    ]
    application = None
    if applications:
        regular_policy = AppKit.NSApplicationActivationPolicyRegular
        application = next(
            (
                candidate
                for candidate in applications
                if candidate.activationPolicy() == regular_policy
            ),
            applications[0],
        )
        options = (
            AppKit.NSApplicationActivateAllWindows
            | AppKit.NSApplicationActivateIgnoringOtherApps
        )
        application.activateWithOptions_(options)
        if _wait_until_active(expected):
            return

    bundle_id = application.bundleIdentifier() if application else None
    if bundle_id:
        _run_focus_command(["/usr/bin/open", "-b", bundle_id])
    else:
        _run_focus_command(["/usr/bin/open", "-a", expected])
    if _wait_until_active(expected):
        return

    escaped_name = expected.replace("\\", "\\\\").replace('"', '\\"')
    _run_focus_command(
        [
            "/usr/bin/osascript",
            "-e",
            f'tell application "{escaped_name}" to activate',
        ]
    )
    if not _wait_until_active(expected):
        actual = active_application_name()
        raise RuntimeError(
            "앱을 활성화했지만 전면 전환을 확인하지 못했습니다: "
            f"expected={expected!r}, actual={actual!r}"
        )


def execute_decision(
    decision,
    board_region: dict[str, int] | None,
    *,
    dry_run: bool = DRY_RUN,
    expected_window_title: str = GAME_WINDOW_TITLE,
    rows: int = ROWS,
    cols: int = COLS,
    cancel_check: Callable[[], str | None] | None = None,
) -> bool:
    """swap 결정을 검증하고 실제 드래그로 실행합니다."""

    if decision.action != "swap" or not decision.source or not decision.target:
        return False
    if board_region is None:
        raise ValueError("swap 실행에는 보드 영역이 필요합니다.")

    start = cell_center(
        decision.source.row,
        decision.source.col,
        board_region,
        rows,
        cols,
    )
    end = cell_center(
        decision.target.row,
        decision.target.col,
        board_region,
        rows,
        cols,
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

    active_title = active_application_name()
    if not _application_matches(expected_window_title, active_title):
        print(f"[FOCUS] {expected_window_title} 앱을 전면으로 전환합니다.")
        activate_application(expected_window_title)
        active_title = active_application_name()
    if not _application_matches(expected_window_title, active_title):
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


def execute_button_tap(
    button,
    full_region: dict[str, int] | None,
    *,
    dry_run: bool = DRY_RUN,
    allow_taps: bool = SURVEY_ALLOW_TAPS,
    expected_window_title: str = GAME_WINDOW_TITLE,
    cancel_check: Callable[[], str | None] | None = None,
) -> bool:
    """조사 모드에서 안전하다고 선별된 UI 버튼을 누릅니다."""

    center = getattr(button, "center", None)
    if center is None:
        return False
    if full_region is None:
        raise ValueError("버튼 탭 실행에는 전체 캡처 영역이 필요합니다.")

    x = full_region["left"] + round(center.x * full_region["width"])
    y = full_region["top"] + round(center.y * full_region["height"])
    label = getattr(button, "label", "")
    role = getattr(button, "role", "unknown")

    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)

    if dry_run or not allow_taps:
        print(f"[DRY RUN] survey tap {label!r}/{role} at {x},{y}")
        return False

    if not expected_window_title:
        raise RuntimeError("실제 클릭에는 GAME_WINDOW_TITLE 설정이 필요합니다.")

    import pyautogui

    active_title = active_application_name()
    if not _application_matches(expected_window_title, active_title):
        print(f"[FOCUS] {expected_window_title} 앱을 전면으로 전환합니다.")
        activate_application(expected_window_title)
        active_title = active_application_name()
    if not _application_matches(expected_window_title, active_title):
        raise RuntimeError(
            "활성 창이 게임 창과 일치하지 않습니다: "
            f"expected={expected_window_title!r}, actual={active_title!r}"
        )

    full_right = full_region["left"] + full_region["width"]
    full_bottom = full_region["top"] + full_region["height"]
    if not (
        full_region["left"] <= x <= full_right
        and full_region["top"] <= y <= full_bottom
    ):
        raise RuntimeError("계산된 버튼 좌표가 전체 캡처 영역을 벗어났습니다.")

    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = ACTION_PAUSE
    pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.click()
    return True
