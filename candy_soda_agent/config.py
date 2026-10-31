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
    "left": 1763,
    "top": 46,
    "width": 759,
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

# 같은 화면 타입에서 이미지 지문(dHash)의 거리가 이 값 이하면 같은 화면으로 봅니다.
# 화면 식별의 1차 기준입니다. LLM이 보고하는 버튼 목록은 같은 화면을 봐도 매번 달라서
# (실측: 같은 플레이 화면인데 버튼 목록이 8~10개 차이) 화면이 쪼개지는 주원인이었고,
# 이미지는 같은 화면이면 거의 같게 나옵니다. 실측 표본에서 0.03~0.10 구간이 결과가
# 같아(노드 12개) 가운데 값을 기본으로 둡니다. 0이면 지문 매칭을 끕니다.
SCREEN_FINGERPRINT_MAX_DISTANCE = float(
    os.getenv("SCREEN_FINGERPRINT_MAX_DISTANCE", "0.05")
)
if not 0.0 <= SCREEN_FINGERPRINT_MAX_DISTANCE <= 1.0:
    raise ValueError("SCREEN_FINGERPRINT_MAX_DISTANCE는 0.0~1.0이어야 합니다.")

# 같은 화면 판정의 1순위: 대표 화면 전체와 HOG 특징 거리(코사인 거리, 0~1)를 비교해 가장
# 가까운 노드가 이 값 이하면 화면 타입과 상관없이 같은 화면입니다(navigation/screen_index.py).
# 실측(run_2026-10-02_08-33-21, 캡처 31장): 같은 화면끼리 최대 0.071, 다른 화면끼리 최소 0.272
# (지도 vs 지도 위 하트 팝업). 그 사이 가운데쯤인 0.15를 기본으로 둡니다. 0이면 끕니다.
SCREEN_HOG_MAX_DISTANCE = float(os.getenv("SCREEN_HOG_MAX_DISTANCE", "0.15"))
if not 0.0 <= SCREEN_HOG_MAX_DISTANCE <= 1.0:
    raise ValueError("SCREEN_HOG_MAX_DISTANCE는 0.0~1.0이어야 합니다.")

# 아는 화면이면 LLM 분석을 건너뛰고 그 화면을 처음 분석한 결과(navigation/decisions/)를 다시
# 씁니다. HOG 거리가 이 값 이하일 때만 건너뛰므로 같은 화면 판정(위 0.15)보다 엄격하게 둡니다
# (같은 화면끼리 최대 0.071). 직전 캡처와 픽셀이 같을 때도 건너뜁니다. 0이면 끕니다(매번 분석).
SCREEN_REUSE_MAX_DISTANCE = float(os.getenv("SCREEN_REUSE_MAX_DISTANCE", "0.10"))
if not 0.0 <= SCREEN_REUSE_MAX_DISTANCE <= 1.0:
    raise ValueError("SCREEN_REUSE_MAX_DISTANCE는 0.0~1.0이어야 합니다.")

# 지문으로도 못 찾았을 때 쓰는 보조 기준입니다. 같은 화면 타입에서 정규화 버튼 목록이
# 이 개수 이하로만 다르면(공통 버튼이 1개 이상일 때) 기존 화면으로 봅니다. 0이면 끕니다.
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

# ------------------------------------------------------------
# 탭 정확도 · 탭 결과 확인 · 막힘 탈출 설정
# ------------------------------------------------------------


def _env_float(name: str, default: str, low: float, high: float) -> float:
    """환경변수를 실수로 읽고 범위를 검사합니다."""

    value = float(os.getenv(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name}는 {low}~{high} 범위여야 합니다: {value}")
    return value


def _env_int(name: str, default: str, low: int) -> int:
    """환경변수를 정수로 읽고 하한을 검사합니다."""

    value = int(os.getenv(name, default))
    if value < low:
        raise ValueError(f"{name}는 {low} 이상이어야 합니다: {value}")
    return value


# [1단계] 누르기 직전 확대 재확인. 조사 모델(gpt-4o-mini)이 준 좌표 주변을 잘라
# 행/열 격자를 그린 뒤, 위치 인식이 나은 모델(OPENAI_TAP_MODEL)에게 버튼 위치를
# 다시 묻습니다. 실측에서 조사 모델 좌표는 58%가 x=0.5였고 탭 24번 중 13번이
# 화면을 바꾸지 못했습니다. false면 예전처럼 조사 모델 좌표를 그대로 누릅니다.
TAP_REFINE = _env_bool("TAP_REFINE", True)
TAP_MODEL = os.getenv("OPENAI_TAP_MODEL", "gpt-4.1-mini")
# 잘라낼 정사각형 한 변 = 화면 긴 변 x 이 비율(757x1348 창이면 약 540px).
TAP_REFINE_CROP_FRACTION = _env_float("TAP_REFINE_CROP_FRACTION", "0.40", 0.1, 1.0)
# 확대본에서 못 찾았거나 확신이 이 값보다 낮으면 전체 화면으로 한 번 더 찾습니다.
TAP_REFINE_MIN_CONFIDENCE = _env_float("TAP_REFINE_MIN_CONFIDENCE", "0.5", 0.0, 1.0)
# 위 전체 화면 재탐색은 기본으로 끕니다. 실측(run_2026-10-12_08-39-35)에서 4번 모두 화면을
# 못 바꿨고 좌표를 334~648px 옮겼습니다(확대본 재확인은 6번 중 2번 성공). 끄면 확대본에서 못
# 찾은 버튼은 누르지 않고 not_found로 닫으며, 나중에 재시도(FRONTIER_RETRY_MAX)로 다시 봅니다.
TAP_REFINE_FULL_FALLBACK = _env_bool("TAP_REFINE_FULL_FALLBACK", False)
TAP_REFINE_MAX_OUTPUT_TOKENS = _env_int("TAP_REFINE_MAX_OUTPUT_TOKENS", "300", 1)

# [2단계] 탭 뒤 화면이 멈출 때까지 기다린 다음 다음 관찰로 넘어갑니다(예전: 고정 1.5초).
POST_TAP_MIN_WAIT = _env_float("POST_TAP_MIN_WAIT", "0.6", 0.0, 10.0)
POST_TAP_INTERVAL = _env_float("POST_TAP_INTERVAL", "0.25", 0.05, 5.0)
POST_TAP_TIMEOUT = _env_float("POST_TAP_TIMEOUT", "5.0", 0.5, 60.0)
POST_TAP_STABLE_FRAMES = _env_int("POST_TAP_STABLE_FRAMES", "2", 1)
# 연속 프레임 차이가 이 값보다 작으면 "멈췄다"고 봅니다.
POST_TAP_STABLE_THRESHOLD = _env_float("POST_TAP_STABLE_THRESHOLD", "0.01", 0.0, 1.0)
# 두 캡처의 픽셀 차이(capture.frame_difference)가 이 값보다 작으면 같은 화면입니다.
# 실측(run_2026-09-21_10-51-14): 같은 화면끼리 0.0000~0.0038(맵 애니메이션 포함),
# 실제 전환은 0.054 이상이었습니다. 탭 효과 판정과 "같은 픽셀 = 같은 노드" 판정에 씁니다.
SAME_SCREEN_PIXEL_THRESHOLD = _env_float("SAME_SCREEN_PIXEL_THRESHOLD", "0.01", 0.0, 1.0)
# 눌렀는데 화면이 그대로면(빗나감 의심) 이 횟수만큼 다시 시도한 뒤 no_change로 닫습니다.
TAP_MISS_RETRIES = _env_int("TAP_MISS_RETRIES", "1", 0)
# 남은 프론티어가 없어 "다 확인했다"로 끝나기 직전에, 실패로 닫힌 항목(no_change·not_found·
# blocked)을 다시 엽니다. 항목마다 이 횟수까지(run 전체 기준)만 다시 열고, 0이면 끕니다.
FRONTIER_RETRY_MAX = _env_int("FRONTIER_RETRY_MAX", "1", 0)

# [3단계] 막혔을 때(프론티어가 남았는데 가는 길을 모름) Android 뒤로를 보냅니다.
# 리셋 중 홈/뒤로 버튼이 안 보일 때도 씁니다. 보내는 방법은 둘입니다.
#   1) ADB_PATH가 있으면 adb로 `input keyevent 4`(Android 뒤로)를 보냅니다(권장).
#      키보드 매핑과 상관없이 동작합니다. LDPlayer는 설치 폴더에 adb.exe가 있습니다.
#      예) ADB_PATH=C:\LDPlayer\LDPlayer14\adb.exe  (.env에서는 따옴표 없이 적으세요)
#      에뮬레이터가 여러 개 떠 있으면 ADB_SERIAL에 `adb devices`의 이름을 적습니다.
#   2) ADB_PATH가 비어 있으면 키보드 키 BACK_KEY를 누릅니다. 실측(run_2026-10-02_08-33-21)
#      에서 LDPlayer는 ESC로 화면이 바뀐 적이 73번 중 1번뿐이라 기본값을 껐습니다.
# 둘 다 비어 있으면 뒤로 기능을 끕니다.
ADB_PATH = os.getenv("ADB_PATH", "").strip().strip('"').strip("'")
ADB_SERIAL = os.getenv("ADB_SERIAL", "").strip()
BACK_KEY = os.getenv("BACK_KEY", "").strip().lower()
# [탈출 사다리] 가는 길을 모르는 화면에 갇혔거나, 루트로 돌아가는데 홈/뒤로 버튼이 안
# 보일 때 아래 순서로 시도합니다. 한 단계가 화면을 바꾸면 그 단계를 계속 쓰고, 화면이
# 그대로인 시도가 단계별 한도만큼 쌓이면 다음 단계로 넘어갑니다.
#   1) Android 뒤로(adb/키보드)           : 화면이 그대로인 시도 STUCK_BACK_KEY_MAX번까지
#   2) 나가기 요소 찾기(OPENAI_TAP_MODEL)  : X/닫기/확인/홈 탭, 없으면 팝업 바깥 어두운 곳
#                                           ESCAPE_EXIT_FINDER_MAX번까지
#   3) 예전에 닫힌 닫기·뒤로·확인 버튼 다시 누르기 : 버튼마다 1번
#   4) 다시 분석(STUCK_RESURVEY)            : 저장된 분석을 두고 "막힘" 설명을 붙여 이 화면을
#                                           한 번 더 분석, 새로 찾은 버튼은 프론티어로 누름(화면당 세션 1번)
#   5) 다른 버튼 눌러보기                   : 이 화면의 안전 버튼 중 화면에 없음·화면 그대로·
#                                           오가던 화면으로 가는 것을 빼고 차례로(화면당 STUCK_OTHER_BUTTONS_MAX개)
#   6) 게임 재시작(adb force-stop 후 실행)  : 세션당 APP_RESTART_MAX번까지
# 탈출 시도가 연속 ESCAPE_MAX_STREAK번 쌓이면(정상 탭 없이) 바로 6)으로 갑니다.
STUCK_BACK_KEY_MAX = _env_int("STUCK_BACK_KEY_MAX", "2", 0)
ESCAPE_EXIT_FINDER = _env_bool("ESCAPE_EXIT_FINDER", True)
ESCAPE_EXIT_FINDER_MAX = _env_int("ESCAPE_EXIT_FINDER_MAX", "2", 0)
ESCAPE_MAX_STREAK = _env_int("ESCAPE_MAX_STREAK", "8", 1)
STUCK_RESURVEY = _env_bool("STUCK_RESURVEY", True)
STUCK_OTHER_BUTTONS_MAX = _env_int("STUCK_OTHER_BUTTONS_MAX", "4", 0)
# [순환 감지] 새 화면도, 프론티어 탭도 없이 STUCK_CYCLE_STEPS번 연속 관찰했는데 그동안 본 화면이
# 2개 이하면(실측 run_2026-10-12: 친구 화면 <-> 안내 팝업 14스텝 왕복) 막힌 것으로 보고 위 4)부터
# 씁니다. 0이면 끕니다.
STUCK_CYCLE_STEPS = _env_int("STUCK_CYCLE_STEPS", "6", 0)
# 새 화면을 처음 분석할 때 위 2)의 나가기 요소 찾기를 한 번 더 돌려, 찾은 X·닫기·뒤로·홈 탭을
# 그 화면 버튼 후보에 더합니다(홈 화면·플레이 화면 제외, 새 화면당 1번). 조사 모델이 작은 X나
# 탭바를 자주 빠뜨려(실측: 레벨 시작 팝업의 X, 상점의 탭바) 화면에서 못 나가던 것을 막습니다.
NEW_SCREEN_EXIT_FINDER = _env_bool("NEW_SCREEN_EXIT_FINDER", True)
# 새 화면을 처음 분석할 때 화면 아래 띠만 잘라 탭바 탭을 따로 찾고(OPENAI_TAP_MODEL, 고해상도),
# 조사 모델이 적은 탭바 후보(region=bottom_nav)를 그 결과로 바꿉니다. 지금 선택된 탭은 뺍니다.
# 실측(run_2026-10-12): 조사 모델이 글자 없는 탭(팀·활동)을 빠뜨리고 좌표를 왼쪽으로 몰아 적음.
# 게임 지식의 탭바 위치가 bottom/varies일 때만 돌고, 새 화면당 호출 1번입니다.
NAV_TAB_FINDER = _env_bool("NAV_TAB_FINDER", True)
# 게임 재시작에 쓰는 Android 패키지 이름. autodrive 시작 때 adb로 설치 여부를 확인하고,
# 없으면 재시작 단계만 끕니다(확인: adb shell pm list packages king).
GAME_PACKAGE = os.getenv("GAME_PACKAGE", "com.king.candycrushsodasaga").strip()
APP_RESTART_MAX = _env_int("APP_RESTART_MAX", "2", 0)
# 재시작 뒤 첫 화면이 뜰 때까지 기다리는 시간(초). 로딩 화면이면 탐색이 알아서 더 기다립니다.
APP_RESTART_WAIT = _env_float("APP_RESTART_WAIT", "20", 0.0, 300.0)

# [공통 UI] 여러 화면에 똑같이 보이는 위/아래 띠(네비게이션 바, 재화 바)를 화면 이미지로
# 찾습니다. 대표 이미지들끼리 행 단위로 비교해, 서로 다른 화면 CHROME_MIN_SCREENS개 이상이
# 같은 줄무늬를 공유하면 그 띠를 공통 UI로 봅니다(실측: 아래 150px 탭 바).
CHROME_DETECTION = _env_bool("CHROME_DETECTION", True)
CHROME_MIN_SCREENS = _env_int("CHROME_MIN_SCREENS", "3", 2)
# 찾은 띠는 "같은 화면인가" 지문 비교에서 빼서, 탭 강조·배지 숫자 차이로 노드가 갈리지 않게 합니다.
CHROME_MASK_IDENTITY = _env_bool("CHROME_MASK_IDENTITY", True)
# 같은 이름의 버튼이 서로 다른 화면 이 개수 이상에서 같은 위치(GLOBAL_ACTION_MAX_SPREAD_PX
# 이내)에 보이면 공통 버튼(네비게이션)으로 봅니다. 공통 띠 안의 버튼은 2개 화면이면 충분하고,
# 조사 모델이 2개 화면에서 bottom_nav/top_bar라고 한 화면 맨 아래/위 버튼도 공통으로 봅니다.
# 공통 버튼은 화면마다가 아니라 전체에서 한 번만 눌러 보고, 그 결과(어디로 가는지)는 그 버튼이
# 보이는 모든 화면에서 쓸 수 있는 지름길로 경로 계획에 씁니다.
GLOBAL_ACTION_MIN_SCREENS = _env_int("GLOBAL_ACTION_MIN_SCREENS", "3", 2)
GLOBAL_ACTION_MAX_SPREAD_PX = _env_float("GLOBAL_ACTION_MAX_SPREAD_PX", "30", 1.0, 500.0)
# 탭 뒤에는 위 POST_TAP_* 대기가 있으므로 스텝 사이 고정 대기는 짧게 둡니다.
AUTODRIVE_STEP_PAUSE = _env_float("AUTODRIVE_STEP_PAUSE", "0.5", 0.0, 30.0)

# [게임 지식] 지금 조사하는 게임. knowledge/games.txt에서 이 제목으로 장르를 찾고,
# knowledge/ui_knowledge.json의 장르·게임 UI 지식과 knowledge/screen_types.json의 화면
# 타입을 조사 프롬프트에 씁니다(game_knowledge.py). games.txt에 없으면 장르 _default로 갑니다.
GAME_TITLE = os.getenv("GAME_TITLE", "Candy Crush Soda Saga").strip() or "Candy Crush Soda Saga"

# [플레이 정책] autodrive는 화면 조사·구조도 생성용이라 기본은 플레이에 들어가지 않습니다.
#   avoid  : 플레이 진입 버튼(ui_knowledge.json의 play_entry_labels)은 기록만 하고 누르지 않고,
#            그래도 플레이 화면(screen_types.json에서 policy=leave)에 들어가면 바로 나옵니다.
#   handoff: 나중에 플레이 전용 코드로 넘길 자리입니다. 아직 연결 전이라 avoid와 같게 동작합니다.
PLAY_POLICY = os.getenv("PLAY_POLICY", "avoid").strip().lower() or "avoid"
if PLAY_POLICY not in {"avoid", "handoff"}:
    raise ValueError(f"PLAY_POLICY는 avoid 또는 handoff만 쓸 수 있습니다: {PLAY_POLICY!r}")
# 플레이 화면에서 나오는 흐름(탈출 동작 포함)이 이 스텝 수를 넘기면 게임 재시작으로 넘어갑니다.
PLAY_EXIT_MAX_STEPS = _env_int("PLAY_EXIT_MAX_STEPS", "8", 1)

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
    # main.py --new-run이 켜는 값입니다. 새 run_id를 만들어 current_run.txt에도
    # 적어 두므로, 다음 실행부터는 옵션 없이 이 새 run을 이어갑니다.
    start_new = os.getenv("NEW_RUN", "").strip().lower() in {"1", "true"}
    if pointer_path.exists() and not start_new:
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
SURVEY_METRICS_MD_PATH = RUN_DIR / "metrics.md"
SURVEY_METRICS_CSV_PATH = RUN_DIR / "metrics.csv"

NAVIGATION_DIR = RUN_DIR / "navigation"
NAVIGATION_GRAPH_PATH = NAVIGATION_DIR / "graph.json"
NAVIGATION_LEDGER_PATH = NAVIGATION_DIR / "ledger.json"
NAVIGATION_JOURNEY_PATH = NAVIGATION_DIR / "journeys.jsonl"
NAVIGATION_IMAGE_DIR = NAVIGATION_DIR / "representatives"
NAVIGATION_UI_CHROME_PATH = NAVIGATION_DIR / "ui_chrome.json"
