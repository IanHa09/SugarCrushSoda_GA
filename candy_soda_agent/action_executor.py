"""검증된 셀 교환을 실제 마우스 행동으로 변환합니다."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
import sys
import time

from config import (
    ACTION_PAUSE,
    ADB_PATH,
    ADB_SERIAL,
    BACK_KEY,
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


def _activate_windows(expected: str) -> None:
    """Windows에서 제목이 일치하는 창을 전면으로 전환합니다."""

    try:
        import pygetwindow
    except ImportError as error:
        raise RuntimeError(
            "Windows 앱 활성화에는 pygetwindow가 필요합니다."
        ) from error

    candidates = [
        window
        for window in pygetwindow.getAllWindows()
        if _application_matches(expected, window.title or "")
    ]
    if not candidates:
        raise RuntimeError(
            f"제목에 {expected!r}가 포함된 창을 찾지 못했습니다."
        )

    # 최소화되지 않은 창을 우선 고르고, 없으면 첫 후보를 복원해서 씁니다.
    window = next(
        (candidate for candidate in candidates if not candidate.isMinimized),
        candidates[0],
    )
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
    except Exception:
        # Windows가 SetForegroundWindow를 거부하면 최소화 후 복원으로 우회합니다.
        try:
            window.minimize()
            window.restore()
        except Exception as error:
            raise RuntimeError(
                f"창 전면 전환에 실패했습니다: expected={expected!r}"
            ) from error


def activate_application(expected: str) -> None:
    """이름이 일치하는 앱/창을 전면으로 전환합니다(macOS·Windows)."""

    if sys.platform == "win32":
        _activate_windows(expected)
        if _wait_until_active(expected):
            return
        actual = active_application_name()
        raise RuntimeError(
            "앱을 활성화했지만 전면 전환을 확인하지 못했습니다: "
            f"expected={expected!r}, actual={actual!r}"
        )

    if sys.platform != "darwin":
        raise RuntimeError(
            "자동 앱 활성화는 macOS와 Windows에서만 지원합니다."
        )

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


def _ensure_game_window_active(expected_window_title: str) -> None:
    """게임 창이 전면이 아니면 전면으로 올리고, 그래도 아니면 예외를 냅니다."""

    if not expected_window_title:
        raise RuntimeError("실제 클릭에는 GAME_WINDOW_TITLE 설정이 필요합니다.")
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


ANDROID_KEYCODE_BACK = "4"
ADB_TIMEOUT_SECONDS = 20


def _adb_executable(adb_path: str) -> str | None:
    """ADB_PATH가 파일이면 그대로, 아니면 PATH에서 찾습니다(예: "adb")."""
    if not adb_path:
        return None
    if Path(adb_path).is_file():
        return adb_path
    return shutil.which(adb_path)


def _adb_command(adb_path: str, serial: str, *args: str) -> list[str]:
    executable = _adb_executable(adb_path) or adb_path
    command = [executable]
    if serial:
        command += ["-s", serial]
    return command + list(args)


def _run_adb(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=ADB_TIMEOUT_SECONDS,
    )


def back_method(adb_path: str = ADB_PATH, key: str = BACK_KEY) -> str:
    """뒤로를 보낼 방법: "adb" | "keyboard" | "off"."""
    if adb_path:
        return "adb"
    if key:
        return "keyboard"
    return "off"


def check_adb(adb_path: str = ADB_PATH, serial: str = ADB_SERIAL) -> tuple[bool, str]:
    """adb로 에뮬레이터에 연결되는지 탐색 시작 전에 확인합니다(adb get-state).

    반환: (성공 여부, 사람이 읽을 설명). 설정이 틀렸으면 탐색 중간이 아니라 시작
    전에 멈춰서 API 호출을 아낍니다."""

    if _adb_executable(adb_path) is None:
        return False, (
            f"ADB_PATH에서 adb를 찾지 못했습니다: {adb_path!r} "
            "(LDPlayer 설치 폴더의 adb.exe 전체 경로를 따옴표 없이 적으세요)"
        )
    try:
        result = _run_adb(_adb_command(adb_path, serial, "get-state"))
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"adb 실행 실패: {error}"
    state = (result.stdout or "").strip()
    if result.returncode != 0 or state != "device":
        detail = (result.stderr or result.stdout or "").strip() or f"state={state!r}"
        hint = ""
        if "more than one" in detail:
            hint = " (에뮬레이터가 여러 개입니다. `adb devices`로 이름을 보고 ADB_SERIAL에 적으세요)"
        elif "no devices" in detail or "not found" in detail:
            hint = " (LDPlayer 설정 > 기타 설정에서 ADB 디버깅을 켰는지 확인하세요)"
        return False, f"adb 연결 실패: {detail}{hint}"
    target = serial or "기본 기기"
    return True, f"{target} 연결됨"


def check_game_package(
    package: str,
    adb_path: str = ADB_PATH,
    serial: str = ADB_SERIAL,
) -> tuple[bool, str]:
    """게임 재시작에 쓸 패키지가 에뮬레이터에 설치돼 있는지 확인합니다."""

    if not package:
        return False, "GAME_PACKAGE가 비어 있습니다."
    try:
        result = _run_adb(_adb_command(adb_path, serial, "shell", "pm", "list", "packages", package))
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"adb 실행 실패: {error}"
    installed = {
        line.strip().removeprefix("package:")
        for line in (result.stdout or "").splitlines()
        if line.strip().startswith("package:")
    }
    if package in installed:
        return True, package
    hint = ""
    try:
        similar = _run_adb(_adb_command(adb_path, serial, "shell", "pm", "list", "packages", "king"))
        names = [
            line.strip().removeprefix("package:")
            for line in (similar.stdout or "").splitlines()
            if line.strip().startswith("package:")
        ]
        if names:
            hint = f" (설치된 king 패키지: {', '.join(names[:5])} — 맞는 이름을 GAME_PACKAGE에 적으세요)"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False, f"패키지 {package!r}를 찾지 못했습니다{hint}"


def restart_game(
    *,
    package: str,
    wait: float,
    adb_path: str = ADB_PATH,
    adb_serial: str = ADB_SERIAL,
    dry_run: bool = DRY_RUN,
    allow_taps: bool = SURVEY_ALLOW_TAPS,
    cancel_check: Callable[[], str | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """adb로 게임을 강제 종료한 뒤 다시 실행합니다(탈출 사다리의 마지막 단계).

    어떤 화면에 갇혀 있어도 처음 화면으로 돌아옵니다. 실행 뒤 wait초 기다립니다."""

    if not adb_path or not package:
        return False
    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)
    if dry_run or not allow_taps:
        print(f"[DRY RUN] restart {package}")
        return False

    steps = (
        ("shell", "am", "force-stop", package),
        ("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"),
    )
    for index, args in enumerate(steps):
        try:
            result = _run_adb(_adb_command(adb_path, adb_serial, *args))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"게임 재시작 실패({args[1]}): {error}") from error
        output = f"{result.stdout or ''}{result.stderr or ''}"
        if result.returncode != 0 or "aborted" in output.casefold():
            raise RuntimeError(f"게임 재시작 실패({args[1]}): {output.strip()}")
        if index == 0:
            sleep(1.0)
    sleep(wait)
    return True


def execute_back_key(
    *,
    key: str = BACK_KEY,
    adb_path: str = ADB_PATH,
    adb_serial: str = ADB_SERIAL,
    dry_run: bool = DRY_RUN,
    allow_taps: bool = SURVEY_ALLOW_TAPS,
    expected_window_title: str = GAME_WINDOW_TITLE,
    cancel_check: Callable[[], str | None] | None = None,
) -> bool:
    """Android 뒤로를 보냅니다(3단계: 막힘 탈출/리셋). 좌표를 쓰지 않습니다.

    - adb_path가 있으면 `adb shell input keyevent 4`를 보냅니다. 게임 창이 전면일
      필요가 없고 키보드 매핑과 상관없이 동작합니다.
    - 없으면 키보드 key를 누릅니다(--hotkeys의 ESC 종료와 겹치지 않도록 누르는 동안
      SYNTHETIC_KEY_ACTIVE를 켭니다).
    반환: 실제로 보냈으면 True."""

    method = back_method(adb_path, key)
    if method == "off":
        return False
    if cancel_check:
        reason = cancel_check()
        if reason:
            raise RuntimeError(reason)
    if dry_run or not allow_taps:
        print(f"[DRY RUN] back ({method})")
        return False

    if method == "adb":
        command = _adb_command(
            adb_path, adb_serial, "shell", "input", "keyevent", ANDROID_KEYCODE_BACK
        )
        try:
            result = _run_adb(command)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"adb 뒤로 키 실행 실패: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"adb 뒤로 키 실패(code={result.returncode}): {detail}")
        return True

    _ensure_game_window_active(expected_window_title)

    import pyautogui

    from hotkeys import SYNTHETIC_KEY_ACTIVE

    SYNTHETIC_KEY_ACTIVE.set()
    try:
        pyautogui.press(key)
        # keyboard 훅 스레드가 이 키 이벤트를 처리할 시간을 줍니다.
        time.sleep(0.3)
    finally:
        SYNTHETIC_KEY_ACTIVE.clear()
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

    _ensure_game_window_active(expected_window_title)

    import pyautogui

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
