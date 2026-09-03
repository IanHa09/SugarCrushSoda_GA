"""행동 실행 직전의 중지 상태와 화면 최신성을 검사합니다."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event


def cancellation_reason(
    mode: str,
    exit_requested: Event,
    auto_running: Event,
) -> str | None:
    """종료 요청이나 자동 모드 중지로 행동을 취소해야 하면 그 사유를 반환합니다."""

    if exit_requested.is_set():
        return "종료 요청이 들어와 행동을 취소했습니다."
    if mode == "auto" and not auto_running.is_set():
        return "자동 모드가 중지되어 행동을 취소했습니다."
    return None


def validate_fresh_board(
    analysis_change: float,
    stability_change: float,
    max_change: float,
) -> tuple[bool, str]:
    """LLM 분석 이후와 행동 직전 사이 보드 변화량이 허용치 이내인지 검사합니다."""

    if analysis_change > max_change:
        return False, (
            "LLM 분석 후 보드가 변경되었습니다: "
            f"change={analysis_change:.4f}, limit={max_change:.4f}"
        )
    if stability_change > max_change:
        return False, (
            "행동 직전 보드가 안정적이지 않습니다: "
            f"change={stability_change:.4f}, limit={max_change:.4f}"
        )
    return True, "최신 보드와 안정성 검증 통과"


def make_cancel_check(
    mode: str,
    exit_requested: Event,
    auto_running: Event,
) -> Callable[[], str | None]:
    """cancellation_reason을 인자 없이 호출할 수 있게 감싼 콜백을 만듭니다."""

    return lambda: cancellation_reason(mode, exit_requested, auto_running)
