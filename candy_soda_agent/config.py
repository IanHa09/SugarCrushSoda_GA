"""프로젝트에서 자주 바꾸는 설정값만 모아 둔 파일입니다."""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    """환경변수를 true/false 불리언으로 읽어옵니다."""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name}은 true 또는 false만 사용할 수 있습니다.")
    return normalized == "true"


# ------------------------------------------------------------
# 화면 캡처 설정
# ------------------------------------------------------------

# MSS에서 0번은 모든 모니터를 합친 가상 화면, 첫번째 1, 두번째 2 
MONITOR_INDEX = 1  # 실제 게임이 실행되는 모니터 번호로 수정 필요 !

# 선택한 모니터의 왼쪽 위를 기준으로 한 게임 보드 영역
# calibrate_region.py를 실행한 뒤 출력된 값으로 교체 필요 !

BOARD_OFFSET = {
    "left": 1137,
    "top": 288,
    "width": 540,
    "height": 544,
} 

# 한 레벨의 바깥쪽 직사각형을 기준으로 행과 열 세기.
# 실제 선택한 Candy Crush Soda 레벨에 맞게 수정 필요 !
ROWS = 9        # 행
COLS = 9        # 열

# 보드 한 칸의 화면상 크기(픽셀). 레벨이 바뀌어도 거의 일정하며, 실측상 데스크톱 좌표로 약 60px입니다.
CELL_SIZE_PX = float(os.getenv("CELL_SIZE_PX", "60"))
if CELL_SIZE_PX <= 0:
    raise ValueError("CELL_SIZE_PX는 0보다 커야 합니다.")
AUTO_GRID = _env_bool("AUTO_GRID", True)
GRID_MIN_ROWS = 4
GRID_MAX_ROWS = 12
GRID_MIN_COLS = 4
GRID_MAX_COLS = 12
GRID_MIN_CONFIDENCE = 0.45


CAPTURE_MODE = os.getenv("CAPTURE_MODE", "board")  # board | window | monitor

# window 모드에서 쓸 영역. 모니터 상대 좌표로 수정 필요 ! 

WINDOW_OFFSET = {
    "left": 1136,
    "top": 76,
    "width": 546,
    "height": 973,
}
# 안전모드 스위치: true면 API/마우스 조작 없이 로컬 실행, false면 실제로 API 호출 및 클릭까지 수행합니다.

DRY_RUN = _env_bool("DRY_RUN", True)

ACTION_PAUSE = 0.15                     # 마우스 동작 후 쉬는시간
DRAG_DURATION = 0.20                    # 드래그 천천히


# ------------------------------------------------------------
# 자동 분석 설정
# ------------------------------------------------------------

# 자동 모드에서 화면을 확인하는 간격(초)
CAPTURE_INTERVAL = 0.5

# 연속 프레임 차이가 이 값보다 작으면 화면이 안정되었다고 봅니다.
# 반짝임 때문에 분석이 시작되지 않으면 0.03~0.05 정도로 높여 보세요.
STABLE_THRESHOLD = 0.02

# 이 횟수만큼 연속으로 안정되어야 API에 이미지를 보냅니다.
STABLE_FRAME_COUNT = 3

# 마지막으로 분석한 보드와 이 값 이상 달라져야 새 보드로 간주
NEW_BOARD_THRESHOLD = 0.035

# API를 너무 자주 호출하지 않도록 두 요청 사이의 최소 간격
API_COOLDOWN = 10.0

# LLM이 swap을 골랐더라도 이 값보다 확신이 낮으면 검증 실패로 표시합니다.
# stable playing 상태에서 wait가 반복되지 않도록 중간 확신 후보도 허용합니다.
MIN_CONFIDENCE = 0.45

# 한 번의 프로그램 실행에서 허용할 최대 API 요청 수
MAX_API_CALLS = 250

# 실제 swap 뒤 보드가 수락/거부되었는지 관찰하는 설정입니다.
POST_ACTION_TIMEOUT = 4.0
POST_ACTION_INTERVAL = 0.15
POST_ACTION_MIN_WAIT = 0.75
POST_ACTION_STABLE_FRAMES = 2
ACTION_ACCEPTED_THRESHOLD = 0.025
ACTION_ATTEMPT_THRESHOLD = 0.010
ACTION_RETURNED_THRESHOLD = 0.008

# 같은 보드에서 실패한 수를 다시 추천하지 않도록 조회할 메모리 범위입니다.
MEMORY_SCAN_LIMIT = 100
MEMORY_CONTEXT_LIMIT = 5
BOARD_FINGERPRINT_MAX_DISTANCE = 0.05

# ------------------------------------------------------------
# 게임 구조 조사 설정
# ------------------------------------------------------------

# 조사 모드에서 안전 버튼을 실제로 누를지 결정합니다(DRY_RUN=false이고 true이거나 --survey-taps일 때만).
SURVEY_ALLOW_TAPS = _env_bool("SURVEY_ALLOW_TAPS", False)
# 탐색에서 이 확신도에 못 미치는 버튼 후보는 누르지 않습니다.
SURVEY_MIN_BUTTON_CONFIDENCE = float(
    os.getenv("SURVEY_MIN_BUTTON_CONFIDENCE", "0.60")
)
if not 0.0 <= SURVEY_MIN_BUTTON_CONFIDENCE <= 1.0:
    raise ValueError("SURVEY_MIN_BUTTON_CONFIDENCE는 0.0~1.0이어야 합니다.")

# 같은 화면/요소를 반복 수집하지 않도록 최근 조사 기록을 참조합니다.
SURVEY_DEDUP_SCAN_LIMIT = int(os.getenv("SURVEY_DEDUP_SCAN_LIMIT", "500"))
if SURVEY_DEDUP_SCAN_LIMIT < 0:
    raise ValueError("SURVEY_DEDUP_SCAN_LIMIT는 0 이상이어야 합니다.")

# 보고서 Evidence Index에 남길 최근 기록 수입니다(다른 목록은 전체 누적, 이 섹션만 제한).
SURVEY_REPORT_EVIDENCE_LIMIT = int(
    os.getenv("SURVEY_REPORT_EVIDENCE_LIMIT", "500")
)
if SURVEY_REPORT_EVIDENCE_LIMIT <= 0:
    raise ValueError("SURVEY_REPORT_EVIDENCE_LIMIT는 1 이상이어야 합니다.")

# 스키마 길이를 짧게 강제한 뒤 900으로 낮췄습니다. 파싱 실패가 잦아지면 다시 올리세요.
SURVEY_LLM_MAX_OUTPUT_TOKENS = int(
    os.getenv("SURVEY_LLM_MAX_OUTPUT_TOKENS", "900")
)
if SURVEY_LLM_MAX_OUTPUT_TOKENS <= 0:
    raise ValueError("SURVEY_LLM_MAX_OUTPUT_TOKENS는 1 이상이어야 합니다.")

# ------------------------------------------------------------
# OpenAI 및 결과 저장 설정
# ------------------------------------------------------------

# PowerShell에서 OPENAI_MODEL을 지정하면 그 값을 우선 사용합니다.
# 예: $env:OPENAI_MODEL="gpt-4o-mini"
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# 조사 모드는 관찰·분류만 하면 되어 느린 reasoning 모델 대신 빠른 전용 모델을 씁니다.
SURVEY_MODEL = os.getenv("OPENAI_SURVEY_MODEL", "gpt-4o-mini")
GAME_WINDOW_TITLE = os.getenv("GAME_WINDOW_TITLE", "")

# 전체 UI는 상태 확인용으로 저해상도 처리하고, 좌표 판단용 보드는 선명하게 유지합니다.
FULL_IMAGE_DETAIL = os.getenv("FULL_IMAGE_DETAIL", "low").strip().lower()
BOARD_IMAGE_DETAIL = os.getenv("BOARD_IMAGE_DETAIL", "high").strip().lower()
_valid_image_details = {"low", "high", "auto"}

for _name, _value in (
    ("FULL_IMAGE_DETAIL", FULL_IMAGE_DETAIL),
    ("BOARD_IMAGE_DETAIL", BOARD_IMAGE_DETAIL),
):
    if _value not in _valid_image_details:
        raise ValueError(
            f"{_name}은 low, high, auto 중 하나여야 합니다: {_value!r}"
        )

# 목록 항목 수를 줄인 뒤 600으로 낮췄습니다.
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "600"))
if LLM_MAX_OUTPUT_TOKENS <= 0:
    raise ValueError("LLM_MAX_OUTPUT_TOKENS는 1 이상이어야 합니다.")

OUTPUT_DIR = Path("output")
CAPTURE_DIR = OUTPUT_DIR / "captures"
LOG_PATH = OUTPUT_DIR / "runs.jsonl"
MEMORY_PATH = Path("memory.jsonl")
SESSION_LOG_PATH = OUTPUT_DIR / "sessions.jsonl"
SURVEY_LOG_PATH = OUTPUT_DIR / "game_survey.jsonl"
SURVEY_REPORT_PATH = OUTPUT_DIR / "game_survey_report.md"
