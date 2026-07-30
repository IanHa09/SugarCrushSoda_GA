"""프로젝트에서 자주 바꾸는 설정값."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# ------------------------------------------------------------
# 실시간 화면 캡처
# ------------------------------------------------------------

# MSS에서 0은 모든 모니터를 합친 영역입니다.
# 일반적으로 1은 주 모니터, 2는 보조 모니터입니다.
MONITOR_INDEX = 2

# 선택한 모니터의 왼쪽 위를 기준으로 한 게임 보드 영역입니다.
# calibrate_region.py의 출력으로 교체할 수 있습니다.
BOARD_OFFSET = {
    "left": 370,
    "top": 660,
    "width": 590,
    "height": 580,
}

# 실시간 점수 캡처를 추가할 때 사용할 예비 영역입니다.
# BOARD_OFFSET을 덮어쓰지 않도록 별도 이름을 사용합니다.
SCORE_OFFSET = {
    "left": 370,
    "top": 660,
    "width": 200,
    "height": 100,
}

# 현재 보드에 맞게 조정하세요. 제공된 학습 영상은 9×9입니다.
ROWS = 9
COLS = 9


# ------------------------------------------------------------
# 실시간 자동 분석
# ------------------------------------------------------------

CAPTURE_INTERVAL = 0.5
STABLE_THRESHOLD = 0.02
STABLE_FRAME_COUNT = 3
NEW_BOARD_THRESHOLD = 0.055

# 15초 간격이면 30분 동안 이론상 최대 약 120회입니다.
API_COOLDOWN = 15.0
MIN_CONFIDENCE = 0.55
MAX_API_CALLS = 120
MAX_TOTAL_TOKENS = 450_000


# ------------------------------------------------------------
# OpenAI 모델과 비용 추정
# ------------------------------------------------------------

MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4o-mini-2024-07-18",
)

IMAGE_DETAIL = os.getenv(
    "OPENAI_IMAGE_DETAIL",
    "low",
).lower()

VIDEO_IMAGE_DETAIL = os.getenv(
    "OPENAI_VIDEO_IMAGE_DETAIL",
    IMAGE_DETAIL,
).lower()

VALID_IMAGE_DETAILS = {
    "low",
    "high",
    "auto",
}

if IMAGE_DETAIL not in VALID_IMAGE_DETAILS:
    raise ValueError(
        "OPENAI_IMAGE_DETAIL은 low, high, auto 중 "
        "하나여야 합니다."
    )

if VIDEO_IMAGE_DETAIL not in VALID_IMAGE_DETAILS:
    raise ValueError(
        "OPENAI_VIDEO_IMAGE_DETAIL은 low, high, auto 중 "
        "하나여야 합니다."
    )

MAX_OUTPUT_TOKENS = 400
MAX_VIDEO_LABEL_OUTPUT_TOKENS = 400

# 기본 모델을 변경하면 가격도 함께 갱신하세요.
INPUT_USD_PER_MILLION_TOKENS = float(
    os.getenv(
        "OPENAI_INPUT_USD_PER_MILLION_TOKENS",
        "0.15",
    )
)
CACHED_INPUT_USD_PER_MILLION_TOKENS = float(
    os.getenv(
        "OPENAI_CACHED_INPUT_USD_PER_MILLION_TOKENS",
        "0.075",
    )
)
OUTPUT_USD_PER_MILLION_TOKENS = float(
    os.getenv(
        "OPENAI_OUTPUT_USD_PER_MILLION_TOKENS",
        "0.60",
    )
)


# ------------------------------------------------------------
# 결과 저장
# ------------------------------------------------------------

OUTPUT_DIR = BASE_DIR / "output"
CAPTURE_DIR = OUTPUT_DIR / "captures"
LOG_PATH = OUTPUT_DIR / "runs.jsonl"


# ------------------------------------------------------------
# 영상 학습 및 평가
# ------------------------------------------------------------

VIDEO_PATH = os.getenv(
    "CANDY_VIDEO_PATH",
    r"C:\Users\user\Videos\candy_crush_training.mp4",
)

# 제공된 1280×720 영상에서 첫 번째 플레이 구간의 9×9 보드.
# 레벨마다 보드 위치가 달라질 수 있으므로 긴 영상은 레벨별로
# --start/--end 구간을 나누고 calibrate_video.py로 재확인하세요.
VIDEO_BOARD_ROI = {
    "left": 449,
    "top": 16,
    "width": 690,
    "height": 691,
}

VIDEO_SCORE_ROI = {
    "left": 11,
    "top": 10,
    "width": 323,
    "height": 528,
}

# 영상 시작 화면은 로그인 화면이므로 첫 플레이가 보이는 시점 사용.
VIDEO_CALIBRATION_SECONDS = 30.0
VIDEO_DEFAULT_START_SECONDS = 20.0

VIDEO_SAMPLE_SECONDS = 0.25
# 제공된 1280×720 영상의 20~120초 구간에서 측정한 값입니다.
# 다른 해상도나 압축률의 영상에서는 다시 조정할 수 있습니다.
VIDEO_STABLE_THRESHOLD = 0.006
VIDEO_MOTION_THRESHOLD = 0.018
VIDEO_STABLE_SAMPLES = 3
VIDEO_MAX_EVENT_SECONDS = 8.0
VIDEO_EVENT_COOLDOWN_SECONDS = 0.8

VIDEO_EXPERIENCE_PATH = (
    OUTPUT_DIR / "video_experiences.jsonl"
)
VIDEO_EVALUATION_PATH = (
    OUTPUT_DIR / "video_evaluations.jsonl"
)
VIDEO_USAGE_PATH = OUTPUT_DIR / "video_usage.jsonl"
VIDEO_CAPTURE_DIR = OUTPUT_DIR / "video_captures"

VIDEO_LABEL_MIN_CONFIDENCE = 0.70
MAX_TRAIN_EVENTS = 40
MAX_EVALUATION_EVENTS = 15

# 유효 이벤트 수와 별도로 실제 요청·토큰을 제한합니다.
MAX_VIDEO_API_CALLS = 60
MAX_VIDEO_TOTAL_TOKENS = 500_000

MEMORY_TOP_STRATEGIES = 4
MEMORY_TOP_EXAMPLES = 2
MEMORY_MAX_CHARS = 900
