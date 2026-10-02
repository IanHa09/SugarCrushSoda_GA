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
    GAME_PACKAGE,
    GLOBAL_ACTION_MAX_SPREAD_PX,
    GLOBAL_ACTION_MIN_SCREENS,
    HOME_RESET_MAX_BACK_TAPS,
    MONITOR_INDEX,
    ROOT_SCREEN_ID,
    SCREEN_FRAGMENTATION_WARN_COUNT,
    STUCK_BACK_KEY_MAX,
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
from tap_refine import find_exit_target, is_risky_exit_label, refine_tap_target
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
    # kind == "stop"일 때 종류: "converged" | "unreachable" | "cancelled" | "error"
    stop_kind: str | None = None


@dataclass
class AutodriveSummary:
    """run_autodrive 한 번(=세션 하나)의 결과. 반복 실행(--autodrive-repeat)이 씁니다."""

    stop_reason: str
    stop_kind: str  # "converged" | "unreachable" | "cancelled" | "error" | "max_steps" | "blocked"
    steps: int
    session_id: str

    @property
    def converged(self) -> bool:
        return self.stop_kind == "converged"


ESCAPE_STAGES = ("back", "exit_finder", "retry_exit", "restart")
# 탈출 기록에 남기는 outcome 이름입니다(갇힘 탈출일 때).
_ESCAPE_OUTCOMES = {
    "back": "stuck_back_key",
    "exit_finder": "stuck_exit_finder",
    "retry_exit": "stuck_retry_exit",
    "restart": "stuck_restart",
}
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
    # 탈출 사다리 상태(정상 탭이 실행되면 처음으로 돌아갑니다).
    escape_stage: int = 0
    escape_tries: int = 0
    escape_streak: int = 0
    exit_failed_points: list = field(default_factory=list)
    tried_exit_keys: set = field(default_factory=set)
    restarts: int = 0
    restart_available: bool = False

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
):
    """이번 스텝의 목표(goal)에 맞는 실제 버튼 후보를 오늘 관찰된 후보 중에서 찾습니다.
    반환값: (button | BACK_KEY_ACTION | None, action_key, used_home).
    reset에서 홈/뒤로 버튼이 안 보이면 None을 돌려주고, 호출한 쪽이 탈출 사다리를 씁니다."""

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

    # goal.kind == "reset": home을 먼저 찾고, 없으면 back 버튼.
    home_button = find_reset_button(candidates, prefer="home")
    if home_button is not None:
        return home_button, navigation_action_key(home_button), True
    back_button = find_reset_button(candidates, prefer="back")
    if back_button is not None:
        return back_button, navigation_action_key(back_button), False
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


def _synthetic_button(label: str, x: int, y: int, width: int, height: int) -> SurveyButtonCandidate:
    return SurveyButtonCandidate(
        label=label or "나가기",
        role="safe_navigation",
        center=NormalizedPoint(x=x / width, y=y / height),
        confidence=1.0,
        reason="탈출 사다리: 나가기 요소 찾기",
    )


def _exit_candidates(decision, tried: set) -> list:
    """현재 화면 후보 중 닫기·뒤로·확인류 버튼(아직 이번 탈출에서 안 눌러 본 것)."""
    buttons = []
    for button in decision.button_candidates:
        if button.center is None or button.role not in SAFE_BUTTON_ROLES:
            continue
        key = navigation_action_key(button)
        if key in tried or is_risky_exit_label(button.label):
            continue
        lowered = button.label.casefold()
        if canonical_action_label(button.label) in _EXIT_CANONICAL or any(
            token in lowered for token in _EXIT_TOKENS
        ):
            buttons.append(button)
    return sorted(buttons, key=lambda b: -b.confidence)


def _plan_escape(client, journey: _Journey, decision, image) -> _EscapePlan | None:
    """탈출 사다리에서 지금 할 동작을 고릅니다. 쓸 수 없는 단계는 건너뜁니다.
    반환이 None이면 사다리를 다 썼습니다."""

    restart_stage = ESCAPE_STAGES.index("restart")
    if (
        journey.escape_streak >= ESCAPE_MAX_STREAK
        and journey.restart_available
        and journey.restarts < APP_RESTART_MAX
        and journey.escape_stage < restart_stage
    ):
        print(f"[ESCAPE] 탈출 시도가 {journey.escape_streak}번 이어져 게임 재시작으로 넘어갑니다.")
        journey.escape_stage = restart_stage
        journey.escape_tries = 0

    height, width = image.shape[:2]
    while journey.escape_stage < len(ESCAPE_STAGES):
        stage = ESCAPE_STAGES[journey.escape_stage]
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
                target = find_exit_target(
                    client, image, failed_points=journey.exit_failed_points
                )
                if target.found:
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
            candidates = _exit_candidates(decision, journey.tried_exit_keys)
            if candidates:
                button = candidates[0]
                key = navigation_action_key(button)
                journey.tried_exit_keys.add(key)
                plan = _EscapePlan(stage, "tap", button.label, key, button=button, refine=True)
        elif stage == "restart":
            if journey.restart_available and journey.restarts < APP_RESTART_MAX:
                plan = _EscapePlan(stage, "restart", "게임 재시작", connect=False)

        if plan is not None:
            return plan
        journey.escape_stage += 1
        journey.escape_tries = 0
    return None


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
) -> _StepResult:
    """탐색 한 단계: 취소 확인 -> 캡처/분석 -> 그래프/원장 갱신 -> 프론티어 계획 ->
    (확대 재확인) -> 탭 -> 화면이 멈출 때까지 대기. 갇히면 탈출 사다리."""

    if cancel_check:
        reason = cancel_check()
        if reason:
            return _StepResult("stop", stop_reason=reason, stop_kind="cancelled")

    bundle = capture_bundle(
        sct, CAPTURE_MODE, MONITOR_INDEX, BOARD_OFFSET, WINDOW_OFFSET
    )
    recent_records = load_recent_survey_records(SURVEY_DEDUP_SCAN_LIMIT)
    decision = analyze_survey_screen(
        client, bundle.full_image, None, survey_context=recent_records
    )

    if decision.screen_type == "loading":
        time.sleep(1.0)
        return _StepResult("skip")

    # 직전 캡처와 픽셀이 같으면 LLM이 화면 타입을 다르게 불러도 같은 노드입니다.
    first_observation = journey.node_id is None
    same_pixels = (
        not first_observation
        and journey.node_id in store.graph.nodes
        and same_screen(journey.prev_image, bundle.full_image)
    )
    if same_pixels:
        node = store.revisit(journey.node_id)
    else:
        node = store.observe(decision, bundle.full_image)
        if node.visits == 1:
            _warn_if_fragmented(store, node.screen_type)
            _refresh_chrome_bands(chrome, store)
    previous_verdict = _finalize_previous_attempt(
        journey, store, ledger, walker, node, same_pixels=same_pixels
    )
    if previous_verdict == "missed":
        print("[AUTODRIVE] 직전 탭 뒤 화면이 그대로입니다(빗나감 의심). 다시 시도 후보로 둡니다.")
    journey.prev_image = bundle.full_image

    # 시작 화면 정렬: 이어서 하는 run(또는 게임 재시작 직후)이 루트가 아닌 화면에서
    # 시작하면 먼저 루트로 갑니다.
    root_id = walker.root_screen_id
    if (
        first_observation
        and root_id is not None
        and root_id in store.graph.nodes
        and node.id != root_id
    ):
        print(
            f"[AUTODRIVE] 시작 화면({node.screen_type})이 루트가 아닙니다. "
            "루트로 먼저 돌아간 뒤 탐색합니다."
        )
        walker.begin_reset_to_root()
    walker.note_root_if_first(node.id)

    # 이번 화면에서 본 안전 후보를 원장에 등록합니다(처음 보는 것만 프론티어로 추가).
    # 네비게이션 바 같은 공통 버튼은 화면별 대신 공통 항목 하나로 등록합니다.
    height, width = bundle.full_image.shape[:2]
    candidates = safe_button_candidates(
        decision, min_confidence=SURVEY_MIN_BUTTON_CONFIDENCE
    )
    visible_keys = [navigation_action_key(candidate) for candidate in candidates]
    global_visible: set[str] = set()
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
        )
        store.log({
            "step": step,
            "session_id": session_id,
            "screen_id": node.id,
            "screen_type": node.screen_type,
            "selected_button": selected,
            "executed": executed,
            "strategy": strategy,
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

    def run_escape(plan: _EscapePlan, goal_kind: str) -> _StepResult:
        """탈출 동작 하나를 실행하고 기록합니다. goal_kind: "stuck" | "reset"."""
        tap_button = plan.button
        refine_record = None
        if plan.kind == "tap" and plan.refine and tap_button is not None:
            tap_button, refine_record, not_found = refine_button(tap_button)
            if not_found:
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
        outcome = (
            _ESCAPE_OUTCOMES[plan.stage] if goal_kind == "stuck" else f"reset_{plan.stage}"
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
            walker.abort()
            return _StepResult("advance", node_id=node.id)

        journey.set_pending(
            "escape" if goal_kind == "stuck" else "reset",
            plan.label,
            plan.action_key,
            escape_stage=plan.stage,
            connect=plan.connect,
            point=plan.point,
        )
        return _StepResult("advance", node_id=node.id)

    back_key_enabled = back_method(ADB_PATH, BACK_KEY) != "off"

    if goal is None:
        if walker.stop_reason == "unreachable":
            # 루트에서는 탈출하지 않습니다(뒤로 = 게임 종료 팝업, 재시작해도 루트).
            if node.id != walker.root_screen_id:
                plan = _plan_escape(client, journey, decision, bundle.full_image)
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
                    + "다시 실행하면 이어서 시도합니다."
                ),
                stop_kind="unreachable",
            )
        record_without_tap("frontier_converged")
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason="전역 프론티어(아직 안 눌러본 화면·행동)를 모두 확인했습니다.",
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
    )

    if button is None and goal.kind == "reset":
        # 루트로 돌아가야 하는데 홈/뒤로 버튼이 안 보입니다. 탈출 사다리를 씁니다.
        plan = _plan_escape(client, journey, decision, bundle.full_image)
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
