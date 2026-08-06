"""프로젝트 설정과 안전한 환경변수 파싱을 담당합니다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")
PROJECT_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    """불명확한 값이 실제 행동을 켜지 않도록 엄격하게 파싱합니다."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name}={raw_value!r}는 올바른 불리언 값이 아닙니다. "
        "true 또는 false를 사용하세요."
    )


def _env_choice(name: str, default: T, allowed: set[T]) -> T:
    raw_value = os.getenv(name)
    value = default if raw_value is None else raw_value.strip()
    if value not in allowed:
        choices = ", ".join(sorted(str(item) for item in allowed))
        raise ValueError(f"{name}={value!r}는 올바르지 않습니다: {choices}")
    return value  # type: ignore[return-value]


# MSS의 0번은 모든 모니터를 합친 가상 화면이므로 행동 좌표에는 사용하지 않습니다.
MONITOR_INDEX = int(os.getenv("MONITOR_INDEX", "1"))

# 선택한 모니터 왼쪽 위를 기준으로 한 게임 보드 영역입니다.
BOARD_OFFSET = {
    "left": 370,
    "top": 660,
    "width": 590,
    "height": 580,
}

ROWS = 9
COLS = 10

# window는 실제 창 추적이 아니라 게임 UI가 들어오는 제한된 고정 영역입니다.
CAPTURE_MODE = _env_choice(
    "CAPTURE_MODE",
    "window",
    {"board", "window", "monitor"},
)
WINDOW_OFFSET = {
    "left": 200,
    "top": 150,
    "width": 590,
    "height": 580,
}

# DRY_RUN은 OpenAI 호출이 아니라 실제 마우스 행동만 차단합니다.
DRY_RUN = _env_bool("DRY_RUN", True)
GAME_WINDOW_TITLE = os.getenv("GAME_WINDOW_TITLE", "").strip()
STORE_FULL_CAPTURE = _env_bool("STORE_FULL_CAPTURE", False)
ACTION_PAUSE = 0.15
DRAG_DURATION = 0.20

# 자동 분석 및 행동 전후 검증 설정입니다.
CAPTURE_INTERVAL = 0.5
STABLE_THRESHOLD = 0.02
STABLE_FRAME_COUNT = 3
NEW_BOARD_THRESHOLD = 0.035
PRE_ACTION_STABILITY_DELAY = 0.25
PRE_ACTION_MAX_CHANGE = 0.02
POST_ACTION_WAIT = 1.0
ACTION_SUCCESS_THRESHOLD = 0.025

# API 비용과 장애 확산을 제한합니다.
API_COOLDOWN = 10.0
API_TIMEOUT = 45.0
MAX_API_CALLS = 250
MAX_CONSECUTIVE_ERRORS = 5
MIN_CONFIDENCE = 0.55

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
MEMORY_CONTEXT_LIMIT = 5

# 실행 위치와 무관하게 패키지 내부 output에만 저장합니다.
OUTPUT_DIR = PROJECT_DIR / "output"
CAPTURE_DIR = OUTPUT_DIR / "captures"
LOG_PATH = OUTPUT_DIR / "runs.jsonl"
MEMORY_PATH = OUTPUT_DIR / "memory.jsonl"
SESSION_LOG_PATH = OUTPUT_DIR / "sessions.jsonl"
AUTO_LOG_DIR = OUTPUT_DIR / "auto_logs"


def validate_live_action_settings() -> None:
    """실제 클릭 설정이 불완전하면 프로그램 시작 단계에서 중단합니다."""

    if not DRY_RUN and not GAME_WINDOW_TITLE:
        raise RuntimeError(
            "실제 클릭에는 GAME_WINDOW_TITLE이 필요합니다. "
            "게임 창 제목 일부를 설정하거나 DRY_RUN=true를 사용하세요."
        )
