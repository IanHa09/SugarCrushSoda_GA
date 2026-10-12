"""Survey 기록과 전환 그래프를 함께 만드는 자동 탐색기.

2단계: 예전에는 현재 화면에서 안 눌러본 안전 버튼이 없으면 그 자리에서 멈췄습니다
(막다른 화면 = 탐색 종료). 이제는 프론티어(화면 전체에서 아직 안 눌러본 (화면,행동)
쌍)가 남아 있으면 그래프 경로를 따라 그 화면까지 이동한 뒤 계속합니다. 이동은
"로컬 재생"(간선을 그대로 재생)을 먼저 시도하고, 도착 화면이 기대와 다르면(팝업/광고
등으로 게임 UI가 결정적이지 않아 생김) 루트로 리셋한 뒤 다시 재생하는 "혼합" 전략을
씁니다(navigation.frontier.FrontierWalker). 전역 프론티어가 모두 소진되면 그때
탐색을 마치고 보고서를 씁니다.

클릭 정확도 개선(2026-10):
  - 누르기 직전 버튼 위치를 확대해 다시 확인합니다(tap_refine). 못 찾으면 누르지 않습니다.
  - 탭 뒤 화면이 멈출 때까지 기다리고(tap_effect), 다음 관찰이 직전과 픽셀로 같으면
    LLM 분류와 상관없이 같은 노드로 셉니다. 픽셀이 그대로인 탭은 빗나감(missed)으로
    보고 간선을 만들지 않으며, TAP_MISS_RETRIES번 다시 시도합니다.
  - 가는 길을 모르는 화면에 갇히면 Android 뒤로(ADB_PATH가 있으면 adb, 없으면 키보드
    BACK_KEY)로 빠져나옵니다.
    리셋 중 홈/뒤로 버튼이 안 보일 때, 그리고 뒤로 키로 생긴 간선을 재생할 때도 씁니다.
  - 이어서 하는 run이 루트가 아닌 화면에서 시작하면 먼저 루트로 돌아갑니다.

탈출 사다리·공통 UI(2026-10):
  - 갇혔거나(가는 길 모름) 루트로 돌아가는데 홈/뒤로 버튼이 안 보이면 Android 뒤로 →
    나가기 요소 찾기(X/닫기/홈 탭, 없으면 팝업 바깥) → 닫기류 버튼 다시 누르기 →
    게임 재시작 순서로 시도합니다(_plan_escape).
  - 여러 화면에 같은 자리로 보이는 버튼(네비게이션 바)은 공통 버튼으로 보고 전체에서
    한 번만 누르며, 눌러서 간 곳은 지름길로 경로 계획에 씁니다(ui_chrome.py).

게임 지식·플레이 정책(2026-10):
  - 시작할 때 GAME_TITLE로 knowledge/의 장르·UI 지식·화면 타입을 읽습니다(game_knowledge.py).
  - 화면 처리 방식은 screen_types.json의 policy를 따릅니다: wait(로딩)는 기다렸다 다시 보고,
    leave(플레이 화면)는 기록만 하고 바로 나옵니다.
  - PLAY_POLICY=avoid: 플레이 진입 버튼(play_entry_labels)은 원장에 play_entry로 닫아 누르지
    않습니다. 그래도 플레이 화면에 들어가면 "플레이 흐름"에 들어가, 나가기 버튼
    (play_exit_labels) → 탈출 사다리(플레이 힌트) 순서로 지도 같은 홈 화면까지 나옵니다.
    흐름 중에 처음 본 화면(나가기 확인창 등)은 프론티어로 등록하지 않습니다.

아는 화면 분석 재사용(2026-10):
  - 직전과 픽셀이 같거나 HOG로 확실히 같은 대표 화면(SCREEN_REUSE_MAX_DISTANCE)이면 LLM을
    부르지 않고 그 화면을 처음 분석한 결과(navigation/decisions/)를 다시 씁니다. 버튼 목록이
    고정되므로 같은 버튼이 이름만 바뀌어 새 프론티어로 잡히지 않고, 캡처도 새로 저장하지 않습니다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import mss
import numpy as np

from action_executor import (
    back_method,
    check_adb,
    check_game_package,
    execute_back_key,
    execute_button_tap,
    restart_game,
)
from capture import capture_bgr
from capture_modes import capture_bundle
from game_knowledge import GameProfile, active_profile, screen_policy, screen_type_ids
from config import (
    ADB_PATH,
    APP_RESTART_MAX,
    APP_RESTART_WAIT,
    AUTODRIVE_STEP_PAUSE,
    BACK_KEY,
    BOARD_OFFSET,
    CAPTURE_MODE,
    CHROME_DETECTION,
    CHROME_MASK_IDENTITY,
    CHROME_MIN_SCREENS,
    DRY_RUN,
    ESCAPE_EXIT_FINDER,
    ESCAPE_EXIT_FINDER_MAX,
    ESCAPE_MAX_STREAK,
    FRONTIER_MAX_MISSES,
    FRONTIER_MAX_REPLAN_ATTEMPTS,
    FRONTIER_RETRY_MAX,
    GAME_PACKAGE,
    GLOBAL_ACTION_MAX_SPREAD_PX,
    GLOBAL_ACTION_MIN_SCREENS,
    HOME_RESET_MAX_BACK_TAPS,
    MONITOR_INDEX,
    NAV_TAB_FINDER,
    NEW_SCREEN_EXIT_FINDER,
    PLAY_EXIT_MAX_STEPS,
    PLAY_POLICY,
    ROOT_SCREEN_ID,
    SCREEN_FRAGMENTATION_WARN_COUNT,
    STUCK_BACK_KEY_MAX,
    STUCK_CYCLE_STEPS,
    STUCK_OTHER_BUTTONS_MAX,
    STUCK_RESURVEY,
    SURVEY_DEDUP_SCAN_LIMIT,
    SURVEY_MIN_BUTTON_CONFIDENCE,
    TAP_MISS_RETRIES,
    TAP_REFINE,
    WINDOW_OFFSET,
)
from navigation.frontier import FrontierWalker, StepGoal, find_reset_button
from navigation.graph import GraphStore, resolve_root_screen_id, screen_type_node_counts
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore
from navigation.policy import (
    canonical_action_label,
    navigation_action_key,
    navigation_action_key_from_label,
)
from schemas import NormalizedPoint, SurveyButtonCandidate
from storage import load_all_survey_records, load_recent_survey_records
from survey import analyze_survey_screen, record_survey_screen
from survey_report import write_survey_report
from survey_utils import SAFE_BUTTON_ROLES, safe_button_candidates
from tap_effect import TapEffect, same_screen, wait_for_settle
from tap_refine import find_exit_target, find_nav_tabs, is_risky_exit_label, refine_tap_target
from ui_chrome import UiChromeStore


# 에뮬레이터 뒤로 키도 그래프에서는 "back" 행동 하나로 다룹니다. 라벨의 괄호는
# canonical_action_label이 지우므로 화면에 보이는 "뒤로"/"Back" 버튼과 같은 action_key가
# 됩니다. 그래서 뒤로 키로 생긴 간선은 뒤로 버튼으로도, 뒤로 키로도 재생할 수 있습니다.
BACK_KEY_LABEL = "뒤로(키)"
BACK_KEY_ACTION_KEY = navigation_action_key_from_label(BACK_KEY_LABEL)
# _choose_action_for_goal이 "버튼 대신 뒤로 키를 누르라"는 뜻으로 돌려주는 표식입니다.
BACK_KEY_ACTION = "BACK_KEY"
# 탭 결과 대기 중 캡처가 실패했을 때의 예전 방식 고정 대기(초)입니다.
_FALLBACK_POST_TAP_SLEEP = 1.5


def _autodrive_blockers(dry_run: bool, allow_taps: bool) -> list[str]:
    """화면 전환에 필요한 실제 클릭이 지금 막혀 있는 이유 목록을 반환합니다."""

    blockers = []
    if dry_run:
        blockers.append("DRY_RUN=true")
    if not allow_taps:
        blockers.append("SURVEY_ALLOW_TAPS=false 이고 --survey-taps 도 없음")
    return blockers


def _print_blocked_help() -> None:
    # 탐색이 막힌 상황에서 해결 방법을 안내합니다.
    print("[AUTODRIVE] 현재 화면 하나만 기록하려면 --survey-once 를 쓰세요.")
    print(
        "[AUTODRIVE] 실제로 탐색하려면 DRY_RUN=false 로 두고 "
        "SURVEY_ALLOW_TAPS=true 또는 --survey-taps 를 지정하세요."
    )


@dataclass
class _StepResult:
    """탐색 한 단계의 결과. kind가 "stop"이 아니면 다음 단계로 이어집니다."""

    kind: str  # "skip"(이 단계는 건너뜀) | "advance"(정상 진행) | "stop"(중단)
    node_id: str | None = None
    stop_reason: str | None = None
    # kind == "stop"일 때 종류: "converged" | "unreachable" | "cancelled" | "error" | "play_stuck"
    stop_kind: str | None = None


@dataclass
class AutodriveSummary:
    """run_autodrive 한 번(=세션 하나)의 결과. 반복 실행(--autodrive-repeat)이 씁니다."""

    stop_reason: str
    stop_kind: str  # "converged" | "unreachable" | "cancelled" | "error" | "max_steps" | "blocked" | "play_stuck"
    steps: int
    session_id: str

    @property
    def converged(self) -> bool:
        return self.stop_kind == "converged"


# resurvey(막힘 설명을 붙여 다시 분석)·other_buttons(이 화면의 다른 버튼)는 종료 대신 쓰는 단계입니다.
ESCAPE_STAGES = ("back", "exit_finder", "retry_exit", "resurvey", "other_buttons", "restart")
# 플레이 흐름에서 보드가 아닌 화면(나가기 확인창·일시정지 메뉴 등)의 순서. 여기서 Android
# 뒤로를 먼저 보내면 확인창이 닫혀 보드로 되돌아가므로, 나가기 요소 찾기를 먼저 씁니다.
PLAY_FLOW_POPUP_STAGES = ("exit_finder", "back", "retry_exit", "restart")
# 탈출 기록에 남기는 outcome 이름입니다(갇힘 탈출일 때).
_ESCAPE_OUTCOMES = {
    "back": "stuck_back_key",
    "exit_finder": "stuck_exit_finder",
    "retry_exit": "stuck_retry_exit",
    "resurvey": "stuck_resurvey",
    "other_buttons": "stuck_other_button",
    "restart": "stuck_restart",
}
# 다른 버튼 눌러보기의 순서: 나가기류(닫기·뒤로·홈) → 이동 → 구조 → 진행.
_OTHER_ROLE_ORDER = {"safe_navigation": 1, "structure": 2, "progression": 3}
# "다시 누르기" 단계에서 고르는 나가기류 버튼(canonical 라벨 또는 낱말).
_EXIT_CANONICAL = frozenset({"close", "back", "home"})
_EXIT_TOKENS = ("닫기", "확인", "나중에", "취소", "계속", "ok", "close", "later", "cancel")


@dataclass
class _Journey:
    """스텝 사이에 들고 다니는 상태: 직전에 실제로 탭을 실행했다면 그 정보를 담아
    다음 관찰 때 그래프 간선을 잇고 원장/프론티어 계획에 결과를 반영합니다."""

    node_id: str | None = None
    pending_action_label: str | None = None
    pending_action_key: str | None = None
    # "frontier" | "replay" | "reset" | "escape"(갇힘 탈출) | None(시도 없음)
    pending_kind: str | None = None
    pending_used_home: bool = False
    # 원장 항목의 화면 ID. 공통 버튼이면 GLOBAL_SCREEN_ID, 아니면 None(=node_id).
    pending_ledger_screen: str | None = None
    # replay 탭이 빗나갔을 때 원장 판정을 되돌리기 위해 mark_attempted 전 값을 보관합니다.
    pending_prev_verdict: str | None = None
    pending_prev_target: str | None = None
    # 탈출 사다리 단계(탈출 동작이었을 때)와, 화면이 바뀌면 간선을 이을지 여부.
    pending_escape_stage: str | None = None
    pending_connect: bool = True
    pending_point: tuple[int, int] | None = None
    # 직전 스텝 캡처. 이번 캡처와 픽셀로 같으면 같은 노드로 봅니다.
    prev_image: np.ndarray | None = None
    # 아는 화면이라 LLM 분석을 건너뛴 스텝 수(세션 끝에 출력).
    reused_steps: int = 0
    # 탈출 사다리 상태(정상 탭이 실행되면 처음으로 돌아갑니다).
    escape_stage: int = 0
    escape_tries: int = 0
    escape_streak: int = 0
    exit_failed_points: list = field(default_factory=list)
    tried_exit_keys: set = field(default_factory=set)
    restarts: int = 0
    restart_available: bool = False
    # 막힘 대응(세션 동안 유지): 다시 분석한 화면, 화면별로 "다른 버튼"으로 눌러 본 행동,
    # 마지막 진전(새 화면 또는 프론티어 탭) 이후 관찰한 화면들(순환 감지).
    resurveyed: set = field(default_factory=set)
    other_tried: dict = field(default_factory=dict)
    idle_nodes: list = field(default_factory=list)
    # 순환으로 막힌 화면들(비어 있으면 막힘 아님)과, 이번 세션에 화면별로 확대 확인에서 못 찾은 행동.
    stuck_cycle: set = field(default_factory=set)
    missing_keys: dict = field(default_factory=dict)

    def dead_keys(self, ledger: LedgerStore, node_id: str) -> set[str]:
        """원장의 dead_keys에 이번 세션에 리셋·탈출 중 못 찾은 행동을 더한 것."""
        return ledger.dead_keys(node_id) | self.missing_keys.get(node_id, set())

    def note_progress(self) -> None:
        """새 화면을 찾았거나 프론티어를 눌렀습니다. 순환 감지와 막힘 상태를 비웁니다."""
        self.idle_nodes.clear()
        self.stuck_cycle.clear()
    # 플레이 흐름: 플레이 화면(policy=leave)에 들어간 뒤 홈 화면으로 나올 때까지의 상태.
    play_flow: bool = False
    play_flow_steps: int = 0
    # 흐름이 시작되기 전부터 알던 화면. 여기로 돌아오면 빠져나온 것으로 봅니다.
    play_flow_known: set = field(default_factory=set)
    # 흐름 안에서 직전에 본 화면. 화면이 바뀌면 탈출 사다리를 처음 단계부터 다시 씁니다.
    play_flow_last_node: str | None = None

    def set_pending(
        self,
        kind: str,
        label: str,
        action_key: str,
        *,
        used_home: bool = False,
        ledger_screen: str | None = None,
        prev_verdict: str | None = None,
        prev_target: str | None = None,
        escape_stage: str | None = None,
        connect: bool = True,
        point: tuple[int, int] | None = None,
    ) -> None:
        self.pending_kind = kind
        self.pending_action_label = label
        self.pending_action_key = action_key
        self.pending_used_home = used_home
        self.pending_ledger_screen = ledger_screen
        self.pending_prev_verdict = prev_verdict
        self.pending_prev_target = prev_target
        self.pending_escape_stage = escape_stage
        self.pending_connect = connect
        self.pending_point = point

    def clear_pending(self) -> None:
        self.set_pending(None, None, None)  # type: ignore[arg-type]

    def reset_escape(self) -> None:
        self.escape_stage = 0
        self.escape_tries = 0
        self.escape_streak = 0
        self.exit_failed_points.clear()
        self.tried_exit_keys.clear()

    def start_play_flow(self, known_nodes) -> None:
        self.play_flow = True
        self.play_flow_steps = 0
        self.play_flow_known = set(known_nodes)
        self.play_flow_last_node = None
        self.reset_escape()
        self.note_progress()

    def end_play_flow(self) -> None:
        self.play_flow = False
        self.play_flow_steps = 0
        self.play_flow_known = set()
        self.play_flow_last_node = None
        self.reset_escape()
        self.note_progress()


def _finalize_previous_attempt(
    journey: _Journey,
    store: GraphStore,
    ledger: LedgerStore,
    walker: FrontierWalker,
    node,
    *,
    same_pixels: bool = False,
) -> str | None:
    """직전 스텝에서 실제로 탭(또는 뒤로/탈출 동작)을 실행했다면, 이번 관찰 결과로 그
    결과를 확정합니다.

    same_pixels: 이번 캡처가 직전 캡처와 픽셀로 같은지(=탭이 화면을 바꾸지 못함).
    그때는 간선을 만들지 않고, 프론티어 탭이면 빗나감(missed)으로 기록해 다시
    시도하게 합니다. 반환: 원장에 남긴 판정(없으면 None)."""

    if journey.node_id is None or journey.pending_kind is None:
        return None

    kind = journey.pending_kind
    action_key = journey.pending_action_key
    ledger_screen = journey.pending_ledger_screen or journey.node_id
    verdict: str | None = None

    if journey.pending_escape_stage is not None:
        if same_pixels:
            # 이 탈출 동작은 효과가 없었습니다. 같은 자리를 다시 고르지 않게 기억합니다.
            if journey.pending_escape_stage == "exit_finder" and journey.pending_point:
                journey.exit_failed_points.append(journey.pending_point)
        else:
            # 효과가 있었으니 이 단계를 계속 씁니다(실패 횟수만 0으로).
            journey.escape_tries = 0

    if same_pixels:
        # 눌렀는데 화면 픽셀이 그대로입니다. 간선은 만들지 않습니다(빗나간 탭이 만든
        # "같은 화면으로 돌아옴" 간선이 그래프를 어지럽히던 주원인이었습니다).
        if kind == "frontier" and action_key:
            verdict = ledger.record_miss(ledger_screen, action_key, TAP_MISS_RETRIES)
        elif kind == "replay" and action_key:
            # 이미 탐색한 간선을 재생하다 빗나갔습니다. 원래 판정을 되돌려 둡니다.
            if journey.pending_prev_verdict is not None:
                ledger.finalize(
                    ledger_screen,
                    action_key,
                    journey.pending_prev_verdict,
                    target_screen_id=journey.pending_prev_target,
                )
        if kind == "replay":
            walker.report_replay_step(executed=True, arrived_screen_id=node.id)
        elif kind == "reset":
            # 홈을 눌렀어도 화면이 그대로면 루트에 간 것이 아닙니다.
            walker.report_reset_step(executed=True, arrived_screen_id=node.id, used_home=False)
        journey.clear_pending()
        return verdict

    moved = node.id != journey.node_id
    if journey.pending_connect and (kind not in ("escape",) or moved):
        store.connect(
            journey.node_id,
            node.id,
            journey.pending_action_label or "unknown",
            action_key or "unknown",
        )

    if kind in ("frontier", "replay") and action_key:
        if not moved:
            verdict = "no_change"  # 화면은 바뀌었지만(픽셀) 같은 화면으로 판정됐습니다.
        elif node.visits == 1:
            verdict = "new_screen"  # 처음 보는 화면으로 넘어갔습니다.
        else:
            verdict = "known_screen"  # 이미 알던 다른 화면으로 넘어갔습니다.
        ledger.finalize(ledger_screen, action_key, verdict, target_screen_id=node.id)

    if kind == "replay":
        walker.report_replay_step(executed=True, arrived_screen_id=node.id)
    elif kind == "reset":
        walker.report_reset_step(
            executed=True,
            arrived_screen_id=node.id,
            used_home=journey.pending_used_home,
        )

    journey.clear_pending()
    return verdict


def _warn_if_fragmented(store: GraphStore, screen_type: str) -> None:
    """새 노드가 생겨 그 화면 타입의 노드 수가 기준에 막 도달했을 때만 한 번 알립니다.

    노드 수는 한 번에 1씩 늘어나므로 "같음" 비교로 타입당 한 번만 출력됩니다.
    끝까지 돌린 뒤 보고서를 열어 보기 전에, 탐색 도중에 쪼개짐을 알아채려는 용도입니다."""

    if SCREEN_FRAGMENTATION_WARN_COUNT <= 0:
        return
    count = screen_type_node_counts(store.graph).get(screen_type, 0)
    if count != SCREEN_FRAGMENTATION_WARN_COUNT:
        return
    print(
        f"[AUTODRIVE 진단] {screen_type} 화면 노드가 {count}개가 됐습니다. "
        "같은 화면이 쪼개지고 있는지 확인하세요"
        "(정말 다른 화면이면 정상입니다)."
    )


def _button_for_action_key(candidates, action_key: str):
    return next(
        (button for button in candidates if navigation_action_key(button) == action_key),
        None,
    )


def _choose_action_for_goal(
    goal: StepGoal,
    candidates,
    node_id: str,
    ledger: LedgerStore,
    *,
    back_key_enabled: bool = False,
    global_visible: set[str] | frozenset[str] = frozenset(),
    skip_keys: set[str] | frozenset[str] = frozenset(),
):
    """이번 스텝의 목표(goal)에 맞는 실제 버튼 후보를 오늘 관찰된 후보 중에서 찾습니다.
    반환값: (button | BACK_KEY_ACTION | None, action_key, used_home).
    reset에서 홈/뒤로 버튼이 안 보이면 None을 돌려주고, 호출한 쪽이 탈출 사다리를 씁니다.
    reset은 skip_keys(이 화면에서 이미 못 찾았거나 눌러도 그대로였던 버튼)를 건너뜁니다
    (실측: 없는 X를 5번 연속 다시 찾음)."""

    if goal.kind == "frontier":
        frontier_keys = set(ledger.frontier_at(node_id))
        frontier_keys |= set(ledger.frontier_at(GLOBAL_SCREEN_ID)) & set(global_visible)
        button = next(
            (b for b in candidates if navigation_action_key(b) in frontier_keys),
            None,
        )
        return button, (navigation_action_key(button) if button else ""), False

    if goal.kind == "replay":
        button = _button_for_action_key(candidates, goal.action_key)
        if button is None and back_key_enabled and goal.action_key == BACK_KEY_ACTION_KEY:
            # 뒤로 키(또는 뒤로 버튼)로 생긴 간선인데 뒤로 버튼이 안 보이면 키로 재생합니다.
            return BACK_KEY_ACTION, goal.action_key, False
        return button, goal.action_key, False

    # goal.kind == "reset": 닫기 → 홈 → 뒤로 순서. 닫기(X)가 보이면 대개 팝업이라 그 뒤의
    # 홈 탭은 가려져 눌리지 않습니다(실측: 레벨 시작 팝업). 홈일 때만 루트 도착으로 봅니다.
    usable = [button for button in candidates if navigation_action_key(button) not in skip_keys]
    for prefer in ("close", "home", "back"):
        button = find_reset_button(usable, prefer=prefer)
        if button is not None:
            return button, navigation_action_key(button), prefer == "home"
    return None, "", False


def _settle_after_action(sct, bundle) -> TapEffect | None:
    """탭/키 입력 뒤 화면이 멈출 때까지 기다립니다. 캡처가 실패하면 예전처럼 고정 대기."""

    try:
        return wait_for_settle(
            lambda: capture_bgr(sct, bundle.full_region),
            bundle.full_image,
        )
    except Exception as error:  # noqa: BLE001 - 대기 실패로 탐색을 멈추지 않습니다.
        print(f"[AUTODRIVE] 탭 뒤 화면 대기 실패({error}); {_FALLBACK_POST_TAP_SLEEP}초 고정 대기합니다.")
        time.sleep(_FALLBACK_POST_TAP_SLEEP)
        return None


def _effect_record(effect: TapEffect | None) -> dict | None:
    return effect.to_record() if effect is not None else None


@dataclass
class _EscapePlan:
    """탈출 사다리에서 이번 스텝에 할 동작 하나."""

    stage: str
    kind: str  # "back" | "tap" | "restart"
    label: str
    action_key: str = ""
    button: SurveyButtonCandidate | None = None
    connect: bool = True
    refine: bool = False
    point: tuple[int, int] | None = None
    record: dict = field(default_factory=dict)


def _synthetic_button(
    label: str, x: int, y: int, width: int, height: int,
    reason: str = "탈출 사다리: 나가기 요소 찾기",
) -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label or "나가기",
        role="safe_navigation",
        center=NormalizedPoint(x=x / width, y=y / height),
        confidence=1.0,
        reason=reason,
    )


def _add_exit_button(client, decision, image, node, profile: GameProfile):
    """처음 분석한 화면의 후보에 나가기 버튼(X·닫기·뒤로·홈 탭)을 더합니다.

    조사 모델은 작은 X나 탭바를 자주 빠뜨리는데(실측: 레벨 시작 팝업의 X, 상점의 탭바),
    분석을 재사용하면 빠진 버튼을 다시 찾을 기회가 없어 화면에서 못 나갑니다. 그래서 새
    화면마다 나가기 전용 찾기(find_exit_target)를 한 번 돌립니다. 홈 화면(나갈 곳 없음)과
    플레이 화면(플레이 흐름이 따로 처리)은 건너뛰고, 같은 버튼이 이미 후보에 있으면 그대로 둡니다."""

    if (
        not NEW_SCREEN_EXIT_FINDER
        or client is None
        or profile.is_home_type(node.screen_type)
        or screen_policy(node.screen_type) == "leave"
    ):
        return decision
    target = find_exit_target(client, image)
    if not target.found or target.kind != "button" or profile.is_play_entry(target.label):
        return decision
    key = navigation_action_key_from_label(target.label)
    if any(navigation_action_key(button) == key for button in decision.button_candidates):
        return decision
    height, width = image.shape[:2]
    button = _synthetic_button(
        target.label, target.x, target.y, width, height, reason="새 화면 나가기 찾기"
    )
    print(f"[EXIT] {node.id}: 나가기 버튼 {target.label!r}을(를) 후보에 더합니다.")
    # 맨 앞에 넣어 후보 개수 상한(MAX_BUTTON_LIMIT)에 잘리지 않게 합니다(누르는 순서는 역할 우선순위).
    return decision.model_copy(update={"button_candidates": [button, *decision.button_candidates]})


def _add_nav_tabs(client, decision, image, node, profile: GameProfile):
    """처음 분석한 화면의 탭바 후보(region=bottom_nav)를 탭바 전용 찾기 결과로 바꿉니다.
    지금 선택된 탭과 플레이 진입 탭은 빼고, 찾은 탭이 없으면 조사 결과를 그대로 둡니다."""

    if (
        not NAV_TAB_FINDER
        or client is None
        or profile.nav_bar_position not in ("bottom", "varies")
        or screen_policy(node.screen_type) == "leave"
    ):
        return decision
    height, width = image.shape[:2]
    tabs = [
        _synthetic_button(tab.label, tab.x, tab.y, width, height, reason="탭바 찾기").model_copy(
            update={"role": "structure", "region": "bottom_nav"}
        )
        for tab in find_nav_tabs(client, image)
        if not tab.selected and not profile.is_play_entry(tab.label)
    ]
    if not tabs:
        return decision
    keys = {navigation_action_key(tab) for tab in tabs}
    others = [
        button
        for button in decision.button_candidates
        if button.region != "bottom_nav" and navigation_action_key(button) not in keys
    ]
    print(f"[NAV] {node.id}: 탭바 탭 {len(tabs)}개: {', '.join(tab.label for tab in tabs)}")
    return decision.model_copy(update={"button_candidates": [*tabs, *others]})


def _exit_candidates(decision, tried: set, profile: GameProfile | None = None) -> list:
    """현재 화면 후보 중 닫기·뒤로·확인류 버튼(아직 이번 탈출에서 안 눌러 본 것).
    "계속 플레이"처럼 플레이 진입 버튼은 '계속'이 들어 있어도 고르지 않습니다."""
    buttons = []
    for button in decision.button_candidates:
        if button.center is None or button.role not in SAFE_BUTTON_ROLES:
            continue
        key = navigation_action_key(button)
        if key in tried or is_risky_exit_label(button.label):
            continue
        if profile is not None and profile.is_play_entry(button.label):
            continue
        lowered = button.label.casefold()
        if canonical_action_label(button.label) in _EXIT_CANONICAL or any(
            token in lowered for token in _EXIT_TOKENS
        ):
            buttons.append(button)
    return sorted(buttons, key=lambda b: -b.confidence)


def _other_buttons(decision, exclude: set, profile: GameProfile | None = None) -> list:
    """막혔을 때 눌러 볼 이 화면의 다른 버튼(안전 역할만, exclude의 행동 키는 뺌).
    나가기류(닫기·뒤로·홈) → 이동 → 구조 → 진행 순, 같은 순위면 확신도 높은 것부터."""
    buttons = []
    for button in decision.button_candidates:
        if button.center is None or button.role not in SAFE_BUTTON_ROLES:
            continue
        if navigation_action_key(button) in exclude or is_risky_exit_label(button.label):
            continue
        if profile is not None and profile.is_play_entry(button.label):
            continue
        buttons.append(button)

    def rank(button) -> tuple[int, float]:
        exit_like = canonical_action_label(button.label) in _EXIT_CANONICAL
        return (0 if exit_like else _OTHER_ROLE_ORDER.get(button.role, 9), -button.confidence)

    return sorted(buttons, key=rank)


def _keys_leading_to(ledger: LedgerStore, node_id: str, targets: set) -> set[str]:
    """이 화면(또는 공통 버튼)에서 눌렀을 때 targets 화면으로 갔던 행동 키."""
    return {
        entry.action_key
        for entry in ledger.ledger.entries.values()
        if entry.screen_id in (node_id, GLOBAL_SCREEN_ID) and entry.target_screen_id in targets
    }


def _stuck_hint(decision, ledger: LedgerStore, node_id: str) -> str:
    """막혀서 다시 분석할 때 조사 프롬프트에 붙일 상황 설명."""
    tried, missing = [], []
    for button in decision.button_candidates:
        key = navigation_action_key(button)
        entries = [ledger.entry(node_id, key), ledger.entry(GLOBAL_SCREEN_ID, key)]
        if any(entry is not None and entry.verdict == "not_found" for entry in entries):
            missing.append(button.label)
        elif any(entry is not None and entry.attempts > 0 for entry in entries):
            tried.append(button.label)
    lines = [
        "- 이 화면에서 나가는 길을 찾지 못해 다시 분석한다. 눌러 볼 수 있는 요소를 빠짐없이 넣는다: "
        "화면 가장자리의 작은 아이콘, 탭바·네비게이션 바의 탭, 뒤로·홈 버튼.",
        "- 화면 아래(또는 옆) 탭바가 어둡게 가려지지 않고 또렷하게 보이면 이 화면은 팝업이 아니라 "
        "그 탭의 화면이다. 그 탭바의 탭을 모두 넣는다.",
    ]
    if tried:
        lines.append(f"- 이미 눌러 본 버튼: {', '.join(tried)}. 다른 버튼을 먼저 찾는다.")
    if missing:
        lines.append(f"- 화면에 없다고 확인된 버튼: {', '.join(missing)}. 넣지 않는다.")
    return "\n".join(lines)


def _merge_candidates(old, fresh, dead: set):
    """다시 분석한 후보 중 새 것을 앞에 두고(후보 상한에 잘리지 않게) 예전 후보를 뒤에 붙입니다.
    화면에 없다고 확인된 행동(dead 중 not_found)은 양쪽에서 뺍니다. 반환: (합친 결과, 새 후보)."""
    kept = [button for button in old.button_candidates if navigation_action_key(button) not in dead]
    known = {navigation_action_key(button) for button in kept}
    added = []
    for button in fresh.button_candidates:
        key = navigation_action_key(button)
        if key in known or key in dead:
            continue
        known.add(key)
        added.append(button)
    return old.model_copy(update={"button_candidates": [*added, *kept]}), added


def _restart_tip(journey: _Journey) -> str:
    """뒤로·게임 재시작을 못 쓰는 설정이면 종료 메시지에 붙일 안내."""
    if journey.restart_available:
        return ""
    return "ADB_PATH를 설정하면 Android 뒤로와 게임 재시작도 탈출에 씁니다."


def _is_cycling(journey: _Journey) -> bool:
    """마지막 진전 이후 STUCK_CYCLE_STEPS번 넘게 화면 2개 이하만 오갔는지."""
    if STUCK_CYCLE_STEPS <= 0 or len(journey.idle_nodes) < STUCK_CYCLE_STEPS:
        return False
    return len(set(journey.idle_nodes[-STUCK_CYCLE_STEPS:])) <= 2


def _plan_escape(
    client,
    journey: _Journey,
    decision,
    image,
    *,
    stages: tuple[str, ...] = ESCAPE_STAGES,
    hint: str = "",
    profile: GameProfile | None = None,
    force_restart: bool = False,
    node_id: str | None = None,
    ledger: LedgerStore | None = None,
) -> _EscapePlan | None:
    """탈출 사다리에서 지금 할 동작을 고릅니다. 쓸 수 없는 단계는 건너뜁니다.
    반환이 None이면 사다리를 다 썼습니다.

    node_id·ledger: 지금 화면과 원장. 있으면 화면에 없거나 눌러도 그대로였던 버튼을 다시
    고르지 않고, 다시 분석·다른 버튼 눌러보기 단계를 씁니다.

    stages: 단계 순서(플레이 흐름의 팝업 화면은 PLAY_FLOW_POPUP_STAGES).
    hint: 나가기 요소 찾기에 넘길 상황 설명(플레이 흐름이면 play_exit_hint).
    force_restart: 바로 게임 재시작 단계로 갑니다(플레이 흐름이 너무 길어졌을 때)."""

    restart_stage = stages.index("restart")
    long_streak = journey.escape_streak >= ESCAPE_MAX_STREAK
    if (
        (long_streak or force_restart)
        and journey.restart_available
        and journey.restarts < APP_RESTART_MAX
        and journey.escape_stage < restart_stage
    ):
        reason = (
            f"플레이에서 나오는 데 {journey.play_flow_steps}스텝이 걸려"
            if force_restart
            else f"탈출 시도가 {journey.escape_streak}번 이어져"
        )
        print(f"[ESCAPE] {reason} 게임 재시작으로 넘어갑니다.")
        journey.escape_stage = restart_stage
        journey.escape_tries = 0

    height, width = image.shape[:2]
    dead = journey.dead_keys(ledger, node_id) if ledger is not None and node_id is not None else set()
    while journey.escape_stage < len(stages):
        stage = stages[journey.escape_stage]
        plan: _EscapePlan | None = None

        if stage == "back":
            if back_method(ADB_PATH, BACK_KEY) != "off" and journey.escape_tries < STUCK_BACK_KEY_MAX:
                plan = _EscapePlan(stage, "back", BACK_KEY_LABEL, BACK_KEY_ACTION_KEY)
        elif stage == "exit_finder":
            if (
                ESCAPE_EXIT_FINDER
                and client is not None
                and journey.escape_tries < ESCAPE_EXIT_FINDER_MAX
            ):
                exit_kwargs = {"failed_points": journey.exit_failed_points}
                if hint:
                    exit_kwargs["hint"] = hint
                target = find_exit_target(client, image, **exit_kwargs)
                if target.found and profile is not None and profile.is_play_entry(target.label):
                    # 나가려다 '플레이'를 누르면 플레이로 들어갑니다. 이 단계는 넘어갑니다.
                    print(f"[ESCAPE] 나가기 요소로 {target.label!r}(플레이 진입)이 골라져 쓰지 않습니다.")
                elif target.found:
                    label = target.label if target.kind == "button" else "팝업 바깥"
                    plan = _EscapePlan(
                        stage,
                        "tap",
                        label,
                        navigation_action_key_from_label(label),
                        button=_synthetic_button(label, target.x, target.y, width, height),
                        # 팝업 바깥 탭은 다시 재생할 버튼이 없어 그래프 간선으로 남기지 않습니다.
                        connect=target.kind == "button",
                        point=(target.x, target.y),
                        record={"exit": target.to_record()},
                    )
                else:
                    reason = target.error or f"kind={target.kind}"
                    print(f"[ESCAPE] 나가기 요소를 찾지 못했습니다({reason}).")
        elif stage == "retry_exit":
            candidates = _exit_candidates(decision, journey.tried_exit_keys | dead, profile)
            if candidates:
                button = candidates[0]
                key = navigation_action_key(button)
                journey.tried_exit_keys.add(key)
                plan = _EscapePlan(stage, "tap", button.label, key, button=button, refine=True)
        elif stage == "resurvey":
            if (
                STUCK_RESURVEY
                and client is not None
                and node_id is not None
                and ledger is not None
                and node_id not in journey.resurveyed
            ):
                journey.resurveyed.add(node_id)
                plan = _EscapePlan(stage, "resurvey", "다시 분석", connect=False)
        elif stage == "other_buttons":
            if node_id is not None and ledger is not None:
                tried = journey.other_tried.setdefault(node_id, set())
                # 오가던 화면으로 가는 버튼은 순환을 되풀이하므로 뺍니다.
                cycle = journey.stuck_cycle or set(journey.idle_nodes)
                loop = _keys_leading_to(ledger, node_id, cycle - {node_id})
                buttons = _other_buttons(decision, tried | dead | loop, profile)
                if buttons and len(tried) < STUCK_OTHER_BUTTONS_MAX:
                    button = buttons[0]
                    key = navigation_action_key(button)
                    tried.add(key)
                    plan = _EscapePlan(stage, "tap", button.label, key, button=button, refine=True)
        elif stage == "restart":
            if journey.restart_available and journey.restarts < APP_RESTART_MAX:
                plan = _EscapePlan(stage, "restart", "게임 재시작", connect=False)

        if plan is not None:
            return plan
        journey.escape_stage += 1
        journey.escape_tries = 0
    return None


# -- 플레이 정책 ---------------------------------------------------------------------


def _split_play_entries(candidates, profile: GameProfile) -> tuple[list, list]:
    """안전 후보를 (탐색할 것, 플레이 진입이라 누르지 않을 것)으로 나눕니다."""
    keep, play = [], []
    for button in candidates:
        (play if profile.is_play_entry(button.label) else keep).append(button)
    return keep, play


def _play_exit_button(decision, profile: GameProfile, tried: set):
    """플레이에서 나가는 버튼(play_exit_labels: 레벨 종료·그만하기 등) 중 아직 안 눌러 본 것.

    조사 모델이 이런 버튼을 unknown 역할로 적기도 해서 안전 역할만 보지 않고, 결제·광고·
    계정 역할과 플레이 진입 버튼만 뺍니다."""

    buttons = [
        button
        for button in decision.button_candidates
        if button.center is not None
        and not button.role.startswith("risky_")
        and not is_risky_exit_label(button.label)
        and not profile.is_play_entry(button.label)
        and profile.is_play_exit(button.label)
        and navigation_action_key(button) not in tried
    ]
    return max(buttons, key=lambda b: b.confidence, default=None)


def _play_flow_finished(node, journey: _Journey, profile: GameProfile, root_id: str | None) -> bool:
    """플레이 흐름에서 빠져나왔는지: 루트, 홈 타입(지도 등), 또는 흐름 전부터 알던 화면."""
    if screen_policy(node.screen_type) == "leave":
        return False
    if root_id is not None and node.id == root_id:
        return True
    if profile.is_home_type(node.screen_type):
        return True
    return node.id in journey.play_flow_known and not node.play_flow


def _is_play_node(store: GraphStore, node_id: str) -> bool:
    """플레이 화면(policy=leave)이거나 플레이에서 나오는 도중 처음 본 화면인지."""
    node = store.graph.nodes.get(node_id)
    return node is not None and (screen_policy(node.screen_type) == "leave" or node.play_flow)


def _try_play_handoff(node, decision) -> bool:
    """PLAY_POLICY=handoff에서 플레이 전용 코드(play_session 등)로 넘기는 자리입니다.

    아직 연결하지 않아 항상 False를 돌려주고, 호출한 쪽은 avoid처럼 플레이에서 나옵니다.
    연결할 때는 여기서 플레이 루프를 돌리고, 끝나면 True를 돌려 다음 관찰부터 이어가게 합니다."""
    return False


def _run_autodrive_step(
    client,
    sct,
    store: GraphStore,
    ledger: LedgerStore,
    walker: FrontierWalker,
    journey: _Journey,
    *,
    step: int,
    session_id: str,
    dry_run: bool,
    allow_taps: bool,
    cancel_check: Callable[[], str | None] | None,
    chrome: UiChromeStore | None = None,
    profile: GameProfile | None = None,
) -> _StepResult:
    """탐색 한 단계: 취소 확인 -> 캡처/분석 -> 그래프/원장 갱신 -> 프론티어 계획 ->
    (확대 재확인) -> 탭 -> 화면이 멈출 때까지 대기. 갇히면 탈출 사다리.
    플레이 화면에 들어가 있으면(플레이 흐름) 프론티어 대신 나오는 동작을 합니다."""

    if cancel_check:
        reason = cancel_check()
        if reason:
            return _StepResult("stop", stop_reason=reason, stop_kind="cancelled")

    profile = profile or active_profile()
    bundle = capture_bundle(
        sct, CAPTURE_MODE, MONITOR_INDEX, BOARD_OFFSET, WINDOW_OFFSET
    )
    recent_records = load_recent_survey_records(SURVEY_DEDUP_SCAN_LIMIT)

    # 직전 캡처와 픽셀이 같으면 LLM이 화면 타입을 다르게 불러도 같은 노드입니다.
    first_observation = journey.node_id is None
    same_pixels = (
        not first_observation
        and journey.node_id in store.graph.nodes
        and same_screen(journey.prev_image, bundle.full_image)
    )
    store.last_hog_match = (None, 1.0)
    # 확실히 아는 화면이면 LLM을 부르지 않고 그 화면을 처음 분석한 결과를 다시 씁니다.
    known = store.known_decision(bundle.full_image, journey.node_id if same_pixels else None)
    reused = known is not None
    if reused:
        node_id, decision = known
        node = store.revisit(node_id)
        journey.reused_steps += 1
        how = "직전과 같은 픽셀" if same_pixels else f"HOG {store.last_hog_match[1]:.3f}"
        print(f"[REUSE] 아는 화면 {node.id}({how}): 분석을 건너뜁니다.")
    else:
        decision = analyze_survey_screen(
            client, bundle.full_image, None, survey_context=recent_records, profile=profile
        )
        # 로딩처럼 policy=wait인 화면은 기다렸다 다시 봅니다(screen_types.json).
        if screen_policy(decision.screen_type) == "wait":
            time.sleep(1.0)
            return _StepResult("skip")
        if same_pixels:
            node = store.revisit(journey.node_id)
        else:
            # observe()는 먼저 대표 화면 전체와 HOG로 비교하고(타입 무관), 못 찾으면 지문·버튼으로 찾습니다.
            node = store.observe(decision, bundle.full_image)
            if node.visits == 1:
                _warn_if_fragmented(store, node.screen_type)
                _refresh_chrome_bands(chrome, store)
        if store.load_decision(node.id) is None:
            # 처음 분석한 화면: 나가기 버튼을 따로 찾아 더한 뒤 저장하고, 다음부터 다시 씁니다.
            decision = _add_nav_tabs(client, decision, bundle.full_image, node, profile)
            decision = _add_exit_button(client, decision, bundle.full_image, node, profile)
            store.save_decision(node.id, decision)
    previous_verdict = _finalize_previous_attempt(
        journey, store, ledger, walker, node, same_pixels=same_pixels
    )
    if previous_verdict == "missed":
        print("[AUTODRIVE] 직전 탭 뒤 화면이 그대로입니다(빗나감 의심). 다시 시도 후보로 둡니다.")
    journey.prev_image = bundle.full_image

    # 플레이 흐름: 플레이 화면(policy=leave)이나 예전 흐름에서 본 화면에 오면 시작하고,
    # 루트·홈 타입(지도)·흐름 전부터 알던 화면에 오면 끝냅니다.
    root_id = walker.root_screen_id
    leave_screen = screen_policy(node.screen_type) == "leave"
    if (leave_screen or node.play_flow) and not journey.play_flow:
        journey.start_play_flow(set(store.graph.nodes) - {node.id})
        print(
            f"[PLAY] 플레이 화면({node.screen_type})에 들어왔습니다. "
            f"기록만 하고 나옵니다(PLAY_POLICY={PLAY_POLICY})."
        )
        if PLAY_POLICY == "handoff" and not _try_play_handoff(node, decision):
            print("[PLAY] handoff: 플레이 전용 코드가 아직 연결되지 않아 avoid처럼 나옵니다.")
    elif journey.play_flow and _play_flow_finished(node, journey, profile, root_id):
        print(
            f"[PLAY] 플레이에서 빠져나왔습니다({node.screen_type}, "
            f"{journey.play_flow_steps}스텝). 탐색을 이어갑니다."
        )
        journey.end_play_flow()
    if journey.play_flow and node.visits == 1 and not leave_screen and not node.play_flow:
        # 나가는 도중 처음 본 화면(나가기 확인창 등). 프론티어로 등록하지 않고 표시해 둡니다.
        node.play_flow = True
        store.save()

    # 순환 감지(플레이 흐름 밖에서만): 새 화면은 진전입니다. 아니면 이번 화면을 쌓아 같은
    # 화면만 오가는지 봅니다. 막힌 화면들 밖으로 나오면 막힘을 풉니다.
    if not journey.play_flow:
        if node.visits == 1:
            journey.note_progress()
        elif journey.stuck_cycle and node.id not in journey.stuck_cycle:
            print(f"[STUCK] 오가던 화면에서 빠져나왔습니다({node.screen_type}). 탐색을 이어갑니다.")
            journey.note_progress()
            walker.abort()
        journey.idle_nodes.append(node.id)
        del journey.idle_nodes[:-50]

    # 시작 화면 정렬: 이어서 하는 run(또는 게임 재시작 직후)이 루트가 아닌 화면에서
    # 시작하면 먼저 루트로 갑니다(플레이 중이면 먼저 나온 뒤).
    if (
        first_observation
        and not journey.play_flow
        and root_id is not None
        and root_id in store.graph.nodes
        and node.id != root_id
    ):
        print(
            f"[AUTODRIVE] 시작 화면({node.screen_type})이 루트가 아닙니다. "
            "루트로 먼저 돌아간 뒤 탐색합니다."
        )
        walker.begin_reset_to_root()
    if not journey.play_flow:
        # 플레이 화면은 루트가 되지 않습니다.
        walker.note_root_if_first(node.id)

    height, width = bundle.full_image.shape[:2]
    candidates = safe_button_candidates(
        decision, min_confidence=SURVEY_MIN_BUTTON_CONFIDENCE
    )
    visible_keys: list[str] = []
    global_visible: set[str] = set()
    goal: StepGoal | None = None
    if not journey.play_flow:
        # 플레이 진입 버튼은 기록만 하고 프론티어에서 뺍니다(PLAY_POLICY).
        candidates, play_buttons = _split_play_entries(candidates, profile)
        for button in play_buttons:
            if ledger.close_play_entry(node.id, navigation_action_key(button), button.label):
                print(f"[PLAY] '{button.label}'은(는) 플레이 진입 버튼이라 누르지 않습니다(기록만).")

        # 이번 화면에서 본 안전 후보를 원장에 등록합니다(처음 보는 것만 프론티어로 추가).
        # 네비게이션 바 같은 공통 버튼은 화면별 대신 공통 항목 하나로 등록합니다.
        visible_keys = [navigation_action_key(candidate) for candidate in candidates]
        for candidate, key in zip(candidates, visible_keys):
            if chrome is not None:
                became_global = chrome.observe(
                    node.id, key, candidate.label,
                    candidate.center.x * width, candidate.center.y * height,
                    region=getattr(candidate, "region", "unknown"),
                    image_height=height,
                )
                if became_global:
                    closed, seeded = ledger.promote_to_global(key, candidate.label)
                    print(
                        f"[UI] '{candidate.label}'을(를) 공통 버튼(네비게이션)으로 봅니다. "
                        f"화면별 미시도 항목 {closed}개를 공통 항목으로 합쳤습니다"
                        + (" (이미 눌러 본 결과를 이어 씁니다)." if seeded else ".")
                    )
                if chrome.is_global(key):
                    global_visible.add(key)
                    ledger.seen(GLOBAL_SCREEN_ID, key, candidate.label)
                    continue
            ledger.seen(node.id, key, candidate.label)
        if chrome is not None:
            chrome.save()
        # 원장에는 있는데 이번 후보엔 없는 미시도 버튼을 세고, 연속으로 안 보이면 정리합니다.
        ledger.note_visit(
            node.id,
            [key for key in visible_keys if key not in global_visible],
            FRONTIER_MAX_MISSES,
        )

        goal = walker.decide(node.id, visible_keys, global_visible)
    journey.node_id = node.id

    def record(action: dict, *, selected: str | None, executed: bool, strategy: str) -> None:
        # 탭 여부와 상관없이 화면 증거는 survey 기록과 여정 로그에 남깁니다.
        record_survey_screen(
            bundle,
            decision,
            session_id=session_id,
            step=step,
            mode="autodrive",
            action=action,
            recent_records=recent_records,
            screen_id=node.id,
            # 분석을 건너뛴 아는 화면은 캡처를 새로 저장하지 않고 대표 이미지를 증거로 씁니다.
            image_path=str(store.image_path(node.id)) if reused else None,
        )
        store.log({
            "step": step,
            "session_id": session_id,
            "screen_id": node.id,
            "screen_type": node.screen_type,
            "selected_button": selected,
            "executed": executed,
            "strategy": strategy,
            "reused_analysis": reused,
            # 가장 가까운 대표 화면과 HOG 거리(임계값 조정용). 픽셀이 직전과 같으면 비교하지 않아 null.
            "hog_nearest": (
                {"screen_id": store.last_hog_match[0], "distance": round(store.last_hog_match[1], 4)}
                if store.last_hog_match[0] is not None
                else None
            ),
        })

    def record_without_tap(outcome: str, **extra) -> None:
        record(
            {"type": "record_only", "outcome": outcome, **extra},
            selected=None,
            executed=False,
            strategy="frontier_backtracking",
        )

    def refine_button(button) -> tuple[SurveyButtonCandidate | None, dict | None, bool]:
        """확대 재확인. (누를 버튼 또는 None, 기록, 못 찾았는지)."""
        if not TAP_REFINE or dry_run or not allow_taps:
            return button, None, False
        refine = refine_tap_target(client, bundle.full_image, button)
        record_data = refine.to_record()
        if refine.found:
            print(
                f"[TAP REFINE] {button.label!r}: {refine.initial} -> ({refine.x}, {refine.y}) "
                f"이동 {refine.shift_px}px ({refine.used_pass})"
            )
            return button.model_copy(update={
                "center": NormalizedPoint(x=refine.x / width, y=refine.y / height),
            }), record_data, False
        if refine.error is not None:
            print(f"[TAP REFINE] 확인 실패({refine.error}); 처음 좌표로 누릅니다.")
            return button, record_data, False
        print(f"[TAP REFINE] {button.label!r} 버튼을 화면에서 찾지 못해 누르지 않습니다.")
        return None, record_data, True

    def run_resurvey(goal_kind: str) -> _StepResult:
        """막힘 단계: 저장된 분석을 두고 막힘 설명을 붙여 이 화면을 다시 분석하고, 새로 찾은
        버튼을 이 화면의 저장된 분석에 더합니다. 다음 스텝에 원장 프론티어로 들어가 평소처럼 눌립니다."""
        try:
            fresh = analyze_survey_screen(
                client,
                bundle.full_image,
                None,
                survey_context=recent_records,
                profile=profile,
                stuck_hint=_stuck_hint(decision, ledger, node.id),
            )
        except Exception as error:  # noqa: BLE001 - 다시 분석이 실패해도 다음 단계로 넘어갑니다.
            print(f"[STUCK] 다시 분석 실패({error}); 다음 단계로 넘어갑니다.")
            return _StepResult("advance", node_id=node.id)
        missing = ledger.dead_keys(node.id, ("not_found",)) | journey.missing_keys.get(node.id, set())
        merged, added = _merge_candidates(decision, fresh, missing)
        store.save_decision(node.id, merged, overwrite=True)
        journey.escape_tries += 1
        journey.escape_streak += 1
        names = ", ".join(button.label for button in added) or "없음"
        print(f"[STUCK] {node.id}: 막혀서 다시 분석했습니다. 새 버튼 {len(added)}개: {names}")
        record(
            {
                "type": "escape",
                "stage": "resurvey",
                "goal_kind": goal_kind,
                "outcome": _ESCAPE_OUTCOMES["resurvey"] if goal_kind == "stuck" else f"{goal_kind}_resurvey",
                "executed": False,
                "added": [button.model_dump() for button in added],
                "escape_streak": journey.escape_streak,
            },
            selected="다시 분석",
            executed=False,
            strategy="escape:resurvey",
        )
        return _StepResult("advance", node_id=node.id)

    def run_escape(plan: _EscapePlan, goal_kind: str) -> _StepResult:
        """탈출 동작 하나를 실행하고 기록합니다. goal_kind: "stuck" | "reset"."""
        if plan.kind == "resurvey":
            return run_resurvey(goal_kind)
        tap_button = plan.button
        refine_record = None
        if plan.kind == "tap" and plan.refine and tap_button is not None:
            tap_button, refine_record, not_found = refine_button(tap_button)
            if not_found:
                journey.missing_keys.setdefault(node.id, set()).add(plan.action_key)
                record_without_tap(
                    "escape_target_not_found", stage=plan.stage, refine=refine_record
                )
                return _StepResult("advance", node_id=node.id)
        try:
            if plan.kind == "back":
                executed = execute_back_key(
                    key=BACK_KEY,
                    adb_path=ADB_PATH,
                    dry_run=dry_run,
                    allow_taps=allow_taps,
                    cancel_check=cancel_check,
                )
            elif plan.kind == "restart":
                executed = restart_game(
                    package=GAME_PACKAGE,
                    wait=APP_RESTART_WAIT,
                    adb_path=ADB_PATH,
                    dry_run=dry_run,
                    allow_taps=allow_taps,
                    cancel_check=cancel_check,
                )
            else:
                executed = execute_button_tap(
                    tap_button,
                    bundle.full_region,
                    dry_run=dry_run,
                    allow_taps=allow_taps,
                    cancel_check=cancel_check,
                )
        except RuntimeError as error:
            return _StepResult("stop", node_id=node.id, stop_reason=str(error), stop_kind="error")

        effect = (
            _settle_after_action(sct, bundle)
            if executed and plan.kind != "restart"
            else None
        )
        journey.escape_tries += 1
        journey.escape_streak += 1
        # 갇힘 탈출은 stuck_*, 루트 리셋은 reset_*, 플레이에서 나오기는 play_* 입니다.
        outcome = (
            _ESCAPE_OUTCOMES[plan.stage] if goal_kind == "stuck" else f"{goal_kind}_{plan.stage}"
        )
        if not executed:
            outcome = "escape_disabled"
        record(
            {
                "type": "escape",
                "stage": plan.stage,
                "goal_kind": goal_kind,
                "outcome": outcome,
                "executed": executed,
                "method": back_method(ADB_PATH, BACK_KEY) if plan.kind == "back" else plan.kind,
                "button": (
                    tap_button.model_dump()
                    if tap_button is not None
                    else {"label": plan.label, "role": "safe_navigation"}
                ),
                "refine": refine_record,
                "tap_effect": _effect_record(effect),
                "escape_streak": journey.escape_streak,
                **plan.record,
            },
            selected=plan.label,
            executed=executed,
            strategy=f"escape:{plan.stage}",
        )
        effect_text = (
            f", 화면변화={effect.final_change:.3f}{'' if effect.changed else ' (그대로)'}"
            if effect is not None
            else ""
        )
        print(f"[ESCAPE] {goal_kind}: {plan.stage} -> {plan.label!r}, executed={executed}{effect_text}")
        if not executed:
            return _StepResult(
                "stop",
                node_id=node.id,
                stop_reason=f"탈출 동작({plan.stage})을 실행하지 못했습니다.",
                stop_kind="error",
            )

        if plan.kind == "restart":
            # 어디서 다시 시작할지 모르니 직전 화면과 잇지 않고, 첫 관찰처럼 시작합니다.
            journey.restarts += 1
            journey.node_id = None
            journey.prev_image = None
            journey.clear_pending()
            journey.reset_escape()
            # 플레이 흐름은 다음 관찰이 정합니다(지도로 시작하면 끝, 보드면 이어서).
            journey.play_flow_steps = 0
            journey.play_flow_last_node = None
            walker.abort()
            return _StepResult("advance", node_id=node.id)

        # 리셋만 프론티어 계획(walker)에 결과를 알려야 합니다. 갇힘·플레이 탈출은 간선만 잇습니다.
        journey.set_pending(
            "reset" if goal_kind == "reset" else "escape",
            plan.label,
            plan.action_key,
            escape_stage=plan.stage,
            connect=plan.connect,
            point=plan.point,
        )
        return _StepResult("advance", node_id=node.id)

    def run_play_flow() -> _StepResult:
        """플레이 흐름 한 스텝: 나가기 버튼(play_exit_labels) → 탈출 사다리(플레이 힌트).

        보드에서는 Android 뒤로부터(보통 나가기 확인창이 뜸), 확인창·메뉴 같은 화면에서는
        나가기 요소 찾기부터 씁니다(뒤로를 먼저 보내면 확인창이 닫혀 보드로 돌아감).
        PLAY_EXIT_MAX_STEPS를 넘기면 게임 재시작, 그것도 못 하면 세션을 멈춥니다."""

        if journey.play_flow_last_node != node.id:
            # 다른 화면으로 왔습니다(직전 동작이 효과 있음). 이 화면 기준으로 처음 단계부터.
            journey.reset_escape()
            journey.play_flow_last_node = node.id
        journey.play_flow_steps += 1
        force_restart = journey.play_flow_steps > PLAY_EXIT_MAX_STEPS
        restart_left = journey.restart_available and journey.restarts < APP_RESTART_MAX
        stuck_reason = None
        if force_restart and not restart_left:
            stuck_reason = (
                f"플레이에서 {PLAY_EXIT_MAX_STEPS}스텝 안에 나오지 못했고 게임 재시작도 쓸 수 없습니다."
            )
        else:
            if not force_restart and not leave_screen:
                exit_button = _play_exit_button(decision, profile, journey.tried_exit_keys)
                if exit_button is not None:
                    key = navigation_action_key(exit_button)
                    journey.tried_exit_keys.add(key)
                    return run_escape(
                        _EscapePlan(
                            "exit_label", "tap", exit_button.label, key,
                            button=exit_button, refine=True,
                        ),
                        "play",
                    )
            plan = _plan_escape(
                client,
                journey,
                decision,
                bundle.full_image,
                stages=ESCAPE_STAGES if leave_screen else PLAY_FLOW_POPUP_STAGES,
                hint=profile.play_exit_hint,
                profile=profile,
                force_restart=force_restart,
            )
            if plan is not None:
                return run_escape(plan, "play")
            stuck_reason = "나가기 버튼과 탈출 사다리를 모두 썼지만 플레이에서 나오지 못했습니다."
        record_without_tap("play_exit_failed")
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=f"{stuck_reason} 게임을 지도 화면으로 돌려 놓고 다시 실행하세요.",
            stop_kind="play_stuck",
        )

    if journey.play_flow:
        return run_play_flow()

    back_key_enabled = back_method(ADB_PATH, BACK_KEY) != "off"

    about_to_explore = goal is not None and goal.kind == "frontier"
    if not journey.stuck_cycle and not about_to_explore and _is_cycling(journey):
        window = journey.idle_nodes[-STUCK_CYCLE_STEPS:]
        journey.stuck_cycle = set(window)
        print(
            f"[STUCK] {len(journey.idle_nodes)}스텝 동안 새 화면·프론티어 탭 없이 화면 "
            f"{len(journey.stuck_cycle)}개만 오갔습니다. 종료하지 않고 이 화면의 다른 버튼을 찾아 눌러봅니다."
        )
        resurvey_stage = ESCAPE_STAGES.index("resurvey")
        if journey.escape_stage < resurvey_stage:
            journey.escape_stage = resurvey_stage
            journey.escape_tries = 0
    if journey.stuck_cycle and not about_to_explore:
        # 막힌 동안에는 경로 재생·리셋 대신 다시 분석 → 다른 버튼 → 재시작 순으로 씁니다.
        # 다시 분석으로 새 버튼이 생기면 decide()가 frontier를 돌려줘 평소처럼 누릅니다.
        walker.abort()
        plan = _plan_escape(
            client, journey, decision, bundle.full_image,
            profile=profile, node_id=node.id, ledger=ledger,
        )
        if plan is not None:
            return run_escape(plan, "stuck")
        record_without_tap("stuck_cycle_exhausted")
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                "같은 화면 몇 개를 오가며 빠져나가지 못했습니다(다시 분석·다른 버튼 눌러보기까지 씀). "
                + _restart_tip(journey)
            ),
            stop_kind="unreachable",
        )

    if goal is None and walker.stop_reason == "converged":
        # "모두 확인"으로 끝내기 전에 실패로 닫힌 항목(화면 그대로·못 찾음·길 막힘)을
        # 한 번 더 엽니다. 항목마다 FRONTIER_RETRY_MAX번까지라 무한 반복하지 않습니다.
        reopened = walker.reopen_failed(node.id, FRONTIER_RETRY_MAX)
        if reopened:
            names = ", ".join(entry.action_label or entry.action_key for entry in reopened[:8])
            print(f"[RETRY] 실패로 닫힌 항목 {len(reopened)}개를 다시 시도합니다: {names}")
            goal = walker.decide(node.id, visible_keys, global_visible)

    if goal is None:
        if walker.stop_reason == "unreachable":
            # 루트에서는 탈출하지 않습니다(뒤로 = 게임 종료 팝업, 재시작해도 루트).
            if node.id != walker.root_screen_id:
                plan = _plan_escape(
                    client, journey, decision, bundle.full_image,
                    profile=profile, node_id=node.id, ledger=ledger,
                )
                if plan is not None:
                    return run_escape(plan, "stuck")
            record_without_tap("frontier_unreachable")
            return _StepResult(
                "stop",
                node_id=node.id,
                stop_reason=(
                    "남은 프론티어가 있지만 현재 화면에서 가는 경로를 아직 모릅니다"
                    + (
                        "(탈출 사다리를 모두 써도 못 빠져나옴). "
                        if node.id != walker.root_screen_id
                        else ". "
                    )
                    + "다시 실행하면 이어서 시도합니다. "
                    + _restart_tip(journey)
                ).strip(),
                stop_kind="unreachable",
            )
        record_without_tap("frontier_converged")
        failed = ledger.failed_count()
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                "전역 프론티어(아직 안 눌러본 화면·행동)를 모두 확인했습니다."
                + (
                    f" 재시도해도 화면이 안 바뀌었거나 못 찾은 항목 {failed}개는 보고서에 남겼습니다."
                    if failed
                    else ""
                )
            ),
            stop_kind="converged",
        )

    if goal.kind == "reobserve":
        # 미시도 버튼이 이번 후보에 안 보였습니다. 누르지 않고 다음 스텝에 다시 봅니다.
        record_without_tap("reobserve_missing_frontier")
        return _StepResult("advance", node_id=node.id)

    button, action_key, used_home = _choose_action_for_goal(
        goal,
        candidates,
        node.id,
        ledger,
        back_key_enabled=back_key_enabled,
        global_visible=global_visible,
        skip_keys=journey.dead_keys(ledger, node.id),
    )

    if button is None and goal.kind == "reset":
        # 루트로 돌아가야 하는데 홈/뒤로 버튼이 안 보입니다. 탈출 사다리를 씁니다.
        plan = _plan_escape(
            client, journey, decision, bundle.full_image,
            profile=profile, node_id=node.id, ledger=ledger,
        )
        if plan is not None:
            return run_escape(plan, "reset")

    if button is None:
        # 기대한 버튼이 이번 후보 목록에 안 보입니다(LLM 표현이 매번 조금씩 다를 수
        # 있음). replay/reset 중이었다면 혼합 전략의 실패로 보고해 다음 스텝에
        # 리셋/재계획이 이어지게 하고, 이번 스텝은 탭 없이 넘어갑니다.
        # (frontier는 decide()가 보이는 후보만 고르므로 정상 흐름에선 여기 오지 않습니다.)
        if goal.kind == "replay":
            walker.report_replay_step(executed=False, arrived_screen_id=None)
        elif goal.kind == "reset":
            walker.report_reset_step(executed=False, arrived_screen_id=None, used_home=False)
        record_without_tap("expected_button_not_visible", goal_kind=goal.kind)
        return _StepResult("advance", node_id=node.id)

    is_back_key = button is BACK_KEY_ACTION
    label = BACK_KEY_LABEL if is_back_key else button.label
    ledger_screen = GLOBAL_SCREEN_ID if action_key in global_visible else node.id
    tap_button = None if is_back_key else button
    refine_record = None

    if not is_back_key:
        # 1단계: 누르기 직전 확대 재확인.
        tap_button, refine_record, not_found = refine_button(button)
        if not_found:
            # 확대본과 전체 화면 어디서도 버튼을 못 찾았습니다. 없는 버튼을 누르면
            # 엉뚱한 화면으로 가서 틀린 간선이 생기므로 누르지 않습니다.
            if goal.kind == "frontier":
                ledger.close_not_found(ledger_screen, action_key)
            elif goal.kind == "replay":
                walker.report_replay_step(executed=False, arrived_screen_id=None)
            elif goal.kind == "reset":
                # 이번 세션에서 이 화면의 이 버튼은 다시 찾지 않습니다(다음 리셋은 홈·뒤로로).
                journey.missing_keys.setdefault(node.id, set()).add(action_key)
                walker.report_reset_step(
                    executed=False, arrived_screen_id=None, used_home=False
                )
            record(
                {
                    "type": "tap_button",
                    "outcome": "tap_target_not_found",
                    "button": button.model_dump(),
                    "executed": False,
                    "goal_kind": goal.kind,
                    "global_action": ledger_screen == GLOBAL_SCREEN_ID,
                    "refine": refine_record,
                },
                selected=label,
                executed=False,
                strategy=f"frontier_backtracking:{goal.kind}",
            )
            return _StepResult("advance", node_id=node.id)

    try:
        if is_back_key:
            executed = execute_back_key(
                key=BACK_KEY,
                adb_path=ADB_PATH,
                dry_run=dry_run,
                allow_taps=allow_taps,
                cancel_check=cancel_check,
            )
        else:
            executed = execute_button_tap(
                tap_button,
                bundle.full_region,
                dry_run=dry_run,
                allow_taps=allow_taps,
                cancel_check=cancel_check,
            )
    except RuntimeError as error:
        # 취소/포커스 오류로 예외가 나도 탐색을 죽이지 않고 지금까지 기록으로 멈춥니다.
        return _StepResult("stop", node_id=node.id, stop_reason=str(error), stop_kind="error")

    prev_entry = ledger.ledger.entries.get(f"{ledger_screen}:{action_key}")
    prev_verdict = prev_entry.verdict if prev_entry is not None else None
    prev_target = prev_entry.target_screen_id if prev_entry is not None else None
    if goal.kind in ("frontier", "replay") and executed:
        ledger.mark_attempted(ledger_screen, action_key, label)

    effect = _settle_after_action(sct, bundle) if executed else None

    action = {
        "type": "back_key" if is_back_key else "tap_button",
        "outcome": "tap_executed" if executed else "tap_dry_run_or_disabled",
        "button": (
            {
                "label": BACK_KEY_LABEL,
                "role": "safe_navigation",
                "method": back_method(ADB_PATH, BACK_KEY),
            }
            if is_back_key
            else tap_button.model_dump()
        ),
        "executed": executed,
        "dry_run": dry_run,
        "allow_taps": allow_taps,
        "goal_kind": goal.kind,
        "global_action": ledger_screen == GLOBAL_SCREEN_ID,
        "refine": refine_record,
        "tap_effect": _effect_record(effect),
    }
    record(
        action,
        selected=label,
        executed=executed,
        strategy=f"frontier_backtracking:{goal.kind}",
    )

    effect_text = (
        f", 화면변화={effect.final_change:.3f}{'' if effect.changed else ' (그대로)'}"
        if effect is not None
        else ""
    )
    global_text = " [공통]" if ledger_screen == GLOBAL_SCREEN_ID else ""
    print(
        f"[AUTODRIVE] {node.screen_type} -> {label!r}{global_text} ({goal.kind}), "
        f"executed={executed}{effect_text}"
    )

    if not executed:
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                f"버튼 {label!r} 탭이 실행되지 않아 "
                "화면 전환을 확인할 수 없습니다."
            ),
            stop_kind="error",
        )

    if goal.kind in ("frontier", "replay"):
        journey.reset_escape()
    if goal.kind == "frontier":
        journey.note_progress()
    journey.set_pending(
        goal.kind,
        label,
        action_key,
        used_home=used_home,
        ledger_screen=ledger_screen if ledger_screen == GLOBAL_SCREEN_ID else None,
        prev_verdict=prev_verdict,
        prev_target=prev_target,
    )
    return _StepResult("advance", node_id=node.id)


def _refresh_chrome_bands(chrome: UiChromeStore | None, store: GraphStore) -> None:
    """노드가 늘면 공통 띠(네비게이션 바 등)를 다시 찾고, 바뀌었으면 지문 비교에 반영합니다."""
    if chrome is None or not CHROME_DETECTION:
        return
    changed = chrome.update_bands(list(store.graph.nodes), store.load_image)
    if not changed:
        return
    bands = chrome.bands
    if bands.active:
        print(
            f"[UI] 공통 띠 감지: 위 {bands.top}px, 아래 {bands.bottom}px "
            f"(화면 {bands.support}개가 공유)"
        )
    if CHROME_MASK_IDENTITY:
        store.set_chrome_bands(bands)


def run_autodrive(
    client,
    max_steps: int = 40,
    *,
    allow_taps: bool = False,
    cancel_check: Callable[[], str | None] | None = None,
) -> AutodriveSummary:
    """프론티어가 소진될 때까지(또는 max_steps에 이를 때까지) 그래프를 갱신하며
    화면을 탐색하는 메인 루프입니다. 한 번 호출이 세션 하나입니다."""

    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if CAPTURE_MODE == "board":
        raise ValueError(
            "--autodrive는 CAPTURE_MODE=window 또는 monitor가 필요합니다."
        )

    # 클릭이 막혀 있으면 첫 화면만 기록하고 멈추므로, API 호출 전에 먼저 알리고 끝냅니다.
    blockers = _autodrive_blockers(DRY_RUN, allow_taps)
    if blockers:
        print(
            "[AUTODRIVE] 화면 전환에는 실제 클릭이 필요한데 지금은 클릭이 "
            f"꺼져 있어 탐색을 시작하지 않습니다: {', '.join(blockers)}"
        )
        _print_blocked_help()
        return AutodriveSummary(
            stop_reason="클릭이 꺼져 있어 시작하지 않았습니다.",
            stop_kind="blocked",
            steps=0,
            session_id=session_id,
        )

    # 게임 지식(장르·UI 관례·화면 타입)을 먼저 읽습니다. 파일 형식이 틀렸으면 API를 쓰기 전에 멈춥니다.
    profile = active_profile()
    print(f"[GAME] {profile.summary_line()}")
    if not profile.known:
        print(
            "[GAME] GAME_TITLE이 knowledge/games.txt에 없어 장르 공통(_default) 지식만 씁니다. "
            "games.txt에 '게임제목, 장르' 줄을 추가하세요."
        )
    print(f"[PLAY] PLAY_POLICY={PLAY_POLICY}: 플레이 진입 버튼은 누르지 않고, 플레이 화면에 들어가면 나옵니다.")

    # 뒤로 보내는 방법을 알리고, adb면 시작 전에 연결을 확인합니다(설정이 틀렸으면
    # 탐색 도중이 아니라 API를 쓰기 전에 멈춥니다).
    restart_available = False
    method = back_method(ADB_PATH, BACK_KEY)
    if method == "adb":
        adb_ok, adb_message = check_adb()
        if not adb_ok:
            print(f"[AUTODRIVE] {adb_message}")
            print(
                "[AUTODRIVE] .env의 ADB_PATH/ADB_SERIAL을 고치거나, adb 없이 돌리려면 "
                "ADB_PATH를 비우세요(그러면 BACK_KEY 키보드 방식이나 뒤로 기능 꺼짐)."
            )
            return AutodriveSummary(
                stop_reason=adb_message,
                stop_kind="blocked",
                steps=0,
                session_id=session_id,
            )
        print(f"[AUTODRIVE] 뒤로: adb ({adb_message})")
        if APP_RESTART_MAX > 0 and GAME_PACKAGE:
            restart_available, package_message = check_game_package(GAME_PACKAGE)
            if restart_available:
                print(f"[AUTODRIVE] 게임 재시작: 사용 ({package_message}, 세션당 최대 {APP_RESTART_MAX}번)")
            else:
                print(f"[AUTODRIVE] 게임 재시작: 끔 — {package_message}")
    elif method == "keyboard":
        print(f"[AUTODRIVE] 뒤로: 키보드 {BACK_KEY!r} (게임 재시작은 adb가 있어야 씁니다)")
    else:
        print("[AUTODRIVE] 뒤로: 꺼짐 (ADB_PATH와 BACK_KEY가 비어 있음)")

    store = GraphStore()
    # 이 run의 그래프가 지금 분류표에 없는 화면 타입(예전 id)으로 만들어졌으면 이어 쓰지 않습니다.
    # 같은 화면 판정이 같은 타입끼리만 비교하므로 예전 노드와 새 관찰이 이어지지 않습니다.
    legacy_types = sorted(
        {node.screen_type for node in store.graph.nodes.values()} - set(screen_type_ids())
    )
    if legacy_types:
        print(
            "[AUTODRIVE] 이 run의 그래프에 지금 화면 타입 분류표(knowledge/screen_types.json)에 없는 "
            f"타입이 있습니다: {', '.join(legacy_types)}"
        )
        print("[AUTODRIVE] 새 run으로 시작하세요: make autodrive-new (또는 --new-run).")
        return AutodriveSummary(
            stop_reason="예전 화면 타입으로 만든 run이라 이어 쓰지 않았습니다(--new-run 필요).",
            stop_kind="blocked",
            steps=0,
            session_id=session_id,
        )
    # 경로 계획(BFS)이 플레이 화면이나 플레이 흐름 화면을 지나가지 않게 합니다.
    store.avoid_node = lambda node_id: _is_play_node(store, node_id)
    ledger = LedgerStore()
    chrome = UiChromeStore(
        min_screens=CHROME_MIN_SCREENS,
        global_min_screens=GLOBAL_ACTION_MIN_SCREENS,
        max_spread_px=GLOBAL_ACTION_MAX_SPREAD_PX,
    )
    if CHROME_DETECTION:
        chrome.update_bands(list(store.graph.nodes), store.load_image)
        if chrome.bands.active:
            print(
                f"[UI] 공통 띠: 위 {chrome.bands.top}px, 아래 {chrome.bands.bottom}px "
                f"(화면 {chrome.bands.support}개가 공유)"
            )
            if CHROME_MASK_IDENTITY:
                store.set_chrome_bands(chrome.bands)
    # 이어서 하는 run이면 보고서와 같은 기준(ROOT_SCREEN_ID, 없으면 그래프의 첫 화면)으로
    # 루트를 정해 둡니다. 새 run이면 None이고 첫 관찰 화면이 루트가 됩니다.
    root_screen_id = (
        resolve_root_screen_id(store.graph, ROOT_SCREEN_ID)
        if store.graph.nodes
        else ROOT_SCREEN_ID
    )
    walker = FrontierWalker(
        store,
        ledger,
        max_back_taps=HOME_RESET_MAX_BACK_TAPS,
        max_replan_attempts=FRONTIER_MAX_REPLAN_ATTEMPTS,
        root_screen_id=root_screen_id,
        extra_edges=lambda: chrome.teleport_edges(ledger),
        global_screens=chrome.screens_showing,
    )
    journey = _Journey(restart_available=restart_available)

    stop_reason = f"최대 단계 수 {max_steps}회를 모두 실행했습니다."
    stop_kind = "max_steps"
    steps_run = 0

    with mss.MSS() as sct:
        for step in range(1, max_steps + 1):
            result = _run_autodrive_step(
                client,
                sct,
                store,
                ledger,
                walker,
                journey,
                step=step,
                session_id=session_id,
                dry_run=DRY_RUN,
                allow_taps=allow_taps,
                cancel_check=cancel_check,
                chrome=chrome,
                profile=profile,
            )

            if result.kind == "skip":
                continue
            steps_run = step
            if result.kind == "stop":
                stop_reason = result.stop_reason
                stop_kind = result.stop_kind or "error"
                break

            # 탭 뒤 대기는 _settle_after_action이 하므로 여기서는 짧게만 쉽니다.
            time.sleep(AUTODRIVE_STEP_PAUSE)

    print(f"[AUTODRIVE] 탐색 종료: {stop_reason}")
    if journey.reused_steps:
        print(f"[REUSE] 분석을 건너뛴 스텝: {journey.reused_steps}/{steps_run}")
    report_path = write_survey_report(
        load_all_survey_records(),
        graph=store.graph,
        ledger=ledger,
    )
    print(f"[AUTODRIVE REPORT] {report_path}")
    return AutodriveSummary(
        stop_reason=stop_reason or "",
        stop_kind=stop_kind,
        steps=steps_run,
        session_id=session_id,
    )
