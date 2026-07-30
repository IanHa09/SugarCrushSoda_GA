"""프로젝트에서 자주 바꾸는 설정값만 모아 둔 파일입니다."""

from __future__ import annotations

import os
from pathlib import Path


# ------------------------------------------------------------
# 화면 캡처 설정
# ------------------------------------------------------------

# MSS에서 0번은 모든 모니터를 합친 가상 화면입니다.
# 일반적으로 1번이 첫 번째 모니터, 2번이 두 번째 모니터입니다.
MONITOR_INDEX = 2

# 선택한 모니터의 왼쪽 위를 기준으로 한 게임 보드 영역입니다.
# calibrate_region.py를 실행한 뒤 출력된 값으로 교체하세요.
BOARD_OFFSET = {
    "left": 370,
    "top": 660,
    "width": 590,
    "height": 580,
}

# 한 레벨의 바깥쪽 직사각형을 기준으로 행과 열을 셉니다.
# 실제 선택한 Candy Crush Soda 레벨에 맞게 바꾸세요.
ROWS = 9
COLS = 10


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
MIN_CONFIDENCE = 0.55

# 한 번의 프로그램 실행에서 허용할 최대 API 요청 수
MAX_API_CALLS = 250

# ------------------------------------------------------------
# OpenAI 및 결과 저장 설정
# ------------------------------------------------------------

# PowerShell에서 OPENAI_MODEL을 지정하면 그 값을 우선 사용합니다.
# 예: $env:OPENAI_MODEL="gpt-4o-mini"
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

OUTPUT_DIR = Path("output")
CAPTURE_DIR = OUTPUT_DIR / "captures"
LOG_PATH = OUTPUT_DIR / "runs.jsonl"
