"""프로젝트에서 자주 바꾸는 설정값만 모아 둔 파일입니다."""

from __future__ import annotations

import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path


def _enable_windows_dpi_awareness() -> None:
    """MSS 캡처와 PyAutoGUI가 동일한 물리 픽셀 좌표를 사용하게 합니다."""

    if sys.platform != "win32":
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_enable_windows_dpi_awareness()


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
    "left": 1854,
    "top": 357,
    "width": 581,
    "height": 719,
}

# 한 레벨의 바깥쪽 직사각형을 기준으로 행과 열 세기.
# 실제 선택한 Candy Crush Soda 레벨에 맞게 수정 필요 !
ROWS = 9        # 행
COLS = 9        # 열

# 보드 한 칸의 화면상 크기(픽셀). 레벨이 바뀌어도 거의 일정하며, 실측상 데스크톱 좌표로 약 60px입니다.
CELL_SIZE_PX = float(os.getenv("CELL_SIZE_PX", "65"))
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
    "left": 1761,
    "top": 44,
    "width": 757,
    "height": 1348,
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
# 탐색 프론티어 · 백트래킹 설정
# ------------------------------------------------------------

# 탐색 루트(홈) 화면 ID. 비워두면 이번 run에서 처음 관찰한 화면을 루트로 씁니다.
# (2단계) 루트 리셋이 어디로 "돌아왔다"고 판정할지, (3단계) 보고서 트리의 루트로 씁니다.
ROOT_SCREEN_ID = os.getenv("ROOT_SCREEN_ID", "").strip() or None

# 루트로 되돌아갈 때 "home" 버튼이 안 보이면 "back"을 최대 이 횟수까지 누릅니다.
# 그래도 루트에 못 돌아오면 그 목표는 포기(blocked)합니다.
HOME_RESET_MAX_BACK_TAPS = int(os.getenv("HOME_RESET_MAX_BACK_TAPS", "6"))
if HOME_RESET_MAX_BACK_TAPS < 0:
    raise ValueError("HOME_RESET_MAX_BACK_TAPS는 0 이상이어야 합니다.")

# 경로 재생이 예상과 다른 화면에 도착하면(로컬 재생 실패) 루트로 리셋한 뒤 다시
# 재생을 시도합니다. 같은 목표에 대해 이 횟수를 넘겨도 계속 실패하면 그 목표는
# blocked로 남기고 다음 프론티어로 넘어갑니다(무한 반복 방지).
FRONTIER_MAX_REPLAN_ATTEMPTS = int(os.getenv("FRONTIER_MAX_REPLAN_ATTEMPTS", "2"))
if FRONTIER_MAX_REPLAN_ATTEMPTS < 1:
    raise ValueError("FRONTIER_MAX_REPLAN_ATTEMPTS는 1 이상이어야 합니다.")

# 같은 화면 타입에서 정규화 버튼 목록이 이 개수 이하로만 다르면(공통 버튼이 1개 이상일 때)
# 기존 화면으로 봅니다. LLM이 버튼 하나를 빠뜨려 화면이 쪼개지는 것을 막습니다. 0이면 끕니다.
SCREEN_MATCH_MAX_BUTTON_DIFF = int(os.getenv("SCREEN_MATCH_MAX_BUTTON_DIFF", "1"))
if SCREEN_MATCH_MAX_BUTTON_DIFF < 0:
    raise ValueError("SCREEN_MATCH_MAX_BUTTON_DIFF는 0 이상이어야 합니다.")

# 원장에 남은 미시도 버튼이 그 화면을 이 횟수만큼 연속 관찰해도 후보에 안 보이면
# 프론티어에서 정리(not_visible)합니다. 그 전까지는 탐색을 멈추지 않고 다시 관찰합니다.
FRONTIER_MAX_MISSES = int(os.getenv("FRONTIER_MAX_MISSES", "2"))
if FRONTIER_MAX_MISSES < 1:
    raise ValueError("FRONTIER_MAX_MISSES는 1 이상이어야 합니다.")

# 같은 화면 타입에 노드가 이 개수 이상 생기면 보고서와 실행 로그에 진단 경고를
# 남깁니다. 화면이 쪼개지고 있다는 신호일 수 있습니다(게임에 실제로 그만큼 다른
# 화면이 있을 수도 있어 경고일 뿐 자동 병합은 하지 않습니다). 0이면 끕니다.
SCREEN_FRAGMENTATION_WARN_COUNT = int(
    os.getenv("SCREEN_FRAGMENTATION_WARN_COUNT", "3")
)
if SCREEN_FRAGMENTATION_WARN_COUNT < 0:
    raise ValueError("SCREEN_FRAGMENTATION_WARN_COUNT는 0 이상이어야 합니다.")

# Mermaid는 노드가 많아지면 레이아웃이 무너지므로, 이 개수를 넘으면 보고서에서
# 다이어그램 생성을 건너뜁니다(0단계 파편화 진단에서 크게 나오면 낮추세요).
MERMAID_MAX_NODES = int(os.getenv("MERMAID_MAX_NODES", "50"))
if MERMAID_MAX_NODES < 0:
    raise ValueError("MERMAID_MAX_NODES는 0 이상이어야 합니다.")

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

def _resolve_run_id(game_root: Path) -> str:
    """탐색(survey/navigation) 로그가 속할 run_id를 정합니다.

    - RUN_ID 환경변수가 있으면 그대로 씁니다(재개하거나 강제로 새 run을 지정할 때).
    - 없으면 game_root/current_run.txt에 적힌 값을 재사용해, 여러 번 나눠 실행해도
      같은 탐색(같은 그래프·원장)을 이어갑니다.
    - 그 파일도 없으면(최초 실행) 새 run_id를 만들어 기록합니다.
    새 탐색을 일부러 새로 시작하려면 RUN_ID를 지정하거나 current_run.txt를 지우세요.
    """

    env_run_id = os.getenv("RUN_ID", "").strip()
    if env_run_id:
        return env_run_id

    pointer_path = game_root / "current_run.txt"
    if pointer_path.exists():
        existing = pointer_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    new_run_id = datetime.now().strftime("run_%Y-%m-%d_%H-%M-%S")
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(new_run_id, encoding="utf-8")
    return new_run_id


# 게임 식별자. 나중에 게임을 추가해도 로그가 안 섞이도록 출력 경로에 항상 포함합니다.
GAME_SLUG = os.getenv("GAME_SLUG", "candy_crush_soda")

OUTPUT_DIR = Path("output")

# 플레이 모드(실제 보드 플레이) 로그는 이번 리팩터 대상이 아니라 기존 위치 그대로 둡니다.
CAPTURE_DIR = OUTPUT_DIR / "captures"
LOG_PATH = OUTPUT_DIR / "runs.jsonl"
MEMORY_PATH = Path("memory.jsonl")
SESSION_LOG_PATH = OUTPUT_DIR / "sessions.jsonl"

# 조사·탐색(survey/navigation) 로그는 output/<game_slug>/runs/<run_id>/ 아래로 모읍니다.
# 두 번째 게임을 추가하거나 탐색을 새로 시작해도 이전 기록과 섞이지 않습니다.
GAME_ROOT_DIR = OUTPUT_DIR / GAME_SLUG
RUN_ID = _resolve_run_id(GAME_ROOT_DIR)
RUN_DIR = GAME_ROOT_DIR / "runs" / RUN_ID

SURVEY_LOG_PATH = RUN_DIR / "game_survey.jsonl"
SURVEY_REPORT_PATH = RUN_DIR / "game_survey_report.md"

NAVIGATION_DIR = RUN_DIR / "navigation"
NAVIGATION_GRAPH_PATH = NAVIGATION_DIR / "graph.json"
NAVIGATION_LEDGER_PATH = NAVIGATION_DIR / "ledger.json"
NAVIGATION_JOURNEY_PATH = NAVIGATION_DIR / "journeys.jsonl"
NAVIGATION_IMAGE_DIR = NAVIGATION_DIR / "representatives"
