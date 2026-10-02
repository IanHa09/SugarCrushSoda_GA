"""F8/F9/ESC 단축키와 실행 상태 Event를 관리합니다."""

from __future__ import annotations

import threading


# 키보드 콜백과 실행 루프가 상태를 공유할 수 있도록 Event를 사용합니다.
AUTO_RUNNING = threading.Event()
ANALYZE_ONCE = threading.Event()
EXIT_REQUESTED = threading.Event()
# 프로그램이 직접 ESC(에뮬레이터 뒤로 키)를 누르는 동안 켭니다. 이때 들어온 ESC는
# keyboard 훅이 사람이 누른 종료 요청으로 오해하지 않도록 무시합니다.
SYNTHETIC_KEY_ACTIVE = threading.Event()


def request_exit() -> None:
    """ESC 단축키 콜백. 프로그램이 보낸 ESC면 무시합니다."""

    if SYNTHETIC_KEY_ACTIVE.is_set():
        return
    EXIT_REQUESTED.set()


def toggle_auto_mode() -> None:
    """F9를 누를 때 자동 분석 상태를 전환합니다."""

    if AUTO_RUNNING.is_set():
        AUTO_RUNNING.clear()
        print("\n[AUTO STOP] 자동 분석을 중지했습니다.")
    else:
        AUTO_RUNNING.set()
        print("\n[AUTO START] 자동 분석을 시작했습니다.")


def setup_hotkeys(enabled: bool):
    """keyboard import가 macOS에서 segfault를 낼 수 있어 명시적으로만 켭니다."""

    if not enabled:
        return None

    import keyboard  # type: ignore

    keyboard.add_hotkey("f8", ANALYZE_ONCE.set)
    keyboard.add_hotkey("f9", toggle_auto_mode)
    keyboard.add_hotkey("esc", request_exit)
    return keyboard


def release_hotkeys(keyboard) -> None:
    """등록한 단축키를 해제합니다. 실패해도 종료를 막지 않습니다."""

    if keyboard is None:
        return
    try:
        keyboard.unhook_all_hotkeys()
    except Exception:
        pass
