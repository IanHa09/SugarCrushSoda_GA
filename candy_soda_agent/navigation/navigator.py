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
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import mss
import numpy as np

from action_executor import back_method, check_adb, execute_back_key, execute_button_tap
from capture import capture_bgr
from capture_modes import capture_bundle
from config import (
    ADB_PATH,
    AUTODRIVE_STEP_PAUSE,
    BACK_KEY,
    BOARD_OFFSET,
    CAPTURE_MODE,
    DRY_RUN,
    FRONTIER_MAX_MISSES,
    FRONTIER_MAX_REPLAN_ATTEMPTS,
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
from navigation.ledger import LedgerStore
from navigation.policy import navigation_action_key, navigation_action_key_from_label
from schemas import NormalizedPoint
from storage import load_all_survey_records, load_recent_survey_records
from survey import analyze_survey_screen, record_survey_screen
from survey_report import write_survey_report
from survey_utils import safe_button_candidates
from tap_effect import TapEffect, same_screen, wait_for_settle
from tap_refine import refine_tap_target


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


@dataclass
class _Journey:
    """스텝 사이에 들고 다니는 상태: 직전에 실제로 탭을 실행했다면 그 정보를 담아
    다음 관찰 때 그래프 간선을 잇고 원장/프론티어 계획에 결과를 반영합니다."""

    node_id: str | None = None
    pending_action_label: str | None = None
    pending_action_key: str | None = None
    # "frontier" | "replay" | "reset" | "escape"(막혀서 뒤로 키) | None(시도 없음)
    pending_kind: str | None = None
    pending_used_home: bool = False
    # replay 탭이 빗나갔을 때 원장 판정을 되돌리기 위해 mark_attempted 전 값을 보관합니다.
    pending_prev_verdict: str | None = None
    pending_prev_target: str | None = None
    # 직전 스텝 캡처. 이번 캡처와 픽셀로 같으면 같은 노드로 봅니다.
    prev_image: np.ndarray | None = None
    # 막힘 탈출로 뒤로 키를 연속으로 누른 횟수(탭이 실행되면 0으로).
    escape_presses: int = 0

    def set_pending(
        self,
        kind: str,
        label: str,
        action_key: str,
        *,
        used_home: bool = False,
        prev_verdict: str | None = None,
        prev_target: str | None = None,
    ) -> None:
        self.pending_kind = kind
        self.pending_action_label = label
        self.pending_action_key = action_key
        self.pending_used_home = used_home
        self.pending_prev_verdict = prev_verdict
        self.pending_prev_target = prev_target

    def clear_pending(self) -> None:
        self.pending_action_label = None
        self.pending_action_key = None
        self.pending_kind = None
        self.pending_used_home = False
        self.pending_prev_verdict = None
        self.pending_prev_target = None


def _finalize_previous_attempt(
    journey: _Journey,
    store: GraphStore,
    ledger: LedgerStore,
    walker: FrontierWalker,
    node,
    *,
    same_pixels: bool = False,
) -> str | None:
    """직전 스텝에서 실제로 탭(또는 뒤로 키)을 실행했다면, 이번 관찰 결과로 그 결과를 확정합니다.

    same_pixels: 이번 캡처가 직전 캡처와 픽셀로 같은지(=탭이 화면을 바꾸지 못함).
    그때는 간선을 만들지 않고, 프론티어 탭이면 빗나감(missed)으로 기록해 다시
    시도하게 합니다. 반환: 원장에 남긴 판정(없으면 None)."""

    if journey.node_id is None or journey.pending_kind is None:
        return None

    kind = journey.pending_kind
    action_key = journey.pending_action_key
    verdict: str | None = None

    if same_pixels:
        # 눌렀는데 화면 픽셀이 그대로입니다. 간선은 만들지 않습니다(빗나간 탭이 만든
        # "같은 화면으로 돌아옴" 간선이 그래프를 어지럽히던 주원인이었습니다).
        if kind == "frontier" and action_key:
            verdict = ledger.record_miss(journey.node_id, action_key, TAP_MISS_RETRIES)
        elif kind == "replay" and action_key:
            # 이미 탐색한 간선을 재생하다 빗나갔습니다. 원래 판정을 되돌려 둡니다.
            if journey.pending_prev_verdict is not None:
                ledger.finalize(
                    journey.node_id,
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

    if kind != "escape" or node.id != journey.node_id:
        store.connect(
            journey.node_id,
            node.id,
            journey.pending_action_label or "unknown",
            action_key or "unknown",
        )

    if kind in ("frontier", "replay") and action_key:
        if node.id == journey.node_id:
            verdict = "no_change"  # 화면은 바뀌었지만(픽셀) 같은 화면으로 판정됐습니다.
        elif node.visits == 1:
            verdict = "new_screen"  # 처음 보는 화면으로 넘어갔습니다.
        else:
            verdict = "known_screen"  # 이미 알던 다른 화면으로 넘어갔습니다.
        ledger.finalize(journey.node_id, action_key, verdict, target_screen_id=node.id)

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
):
    """이번 스텝의 목표(goal)에 맞는 실제 버튼 후보를 오늘 관찰된 후보 중에서 찾습니다.
    반환값: (button | BACK_KEY_ACTION | None, action_key, used_home)."""

    if goal.kind == "frontier":
        frontier_keys = set(ledger.frontier_at(node_id))
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

    # goal.kind == "reset": home을 먼저 찾고, 없으면 back 버튼, 그것도 없으면 뒤로 키.
    home_button = find_reset_button(candidates, prefer="home")
    if home_button is not None:
        return home_button, navigation_action_key(home_button), True
    back_button = find_reset_button(candidates, prefer="back")
    if back_button is not None:
        return back_button, navigation_action_key(back_button), False
    if back_key_enabled:
        return BACK_KEY_ACTION, BACK_KEY_ACTION_KEY, False
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
) -> _StepResult:
    """탐색 한 단계: 취소 확인 -> 캡처/분석 -> 그래프/원장 갱신 -> 프론티어 계획 ->
    (확대 재확인) -> 탭 -> 화면이 멈출 때까지 대기."""

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
    previous_verdict = _finalize_previous_attempt(
        journey, store, ledger, walker, node, same_pixels=same_pixels
    )
    if previous_verdict == "missed":
        print("[AUTODRIVE] 직전 탭 뒤 화면이 그대로입니다(빗나감 의심). 다시 시도 후보로 둡니다.")
    journey.prev_image = bundle.full_image

    # 시작 화면 정렬: 이어서 하는 run이 루트가 아닌 화면에서 시작하면 먼저 루트로 갑니다.
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
    candidates = safe_button_candidates(
        decision, min_confidence=SURVEY_MIN_BUTTON_CONFIDENCE
    )
    visible_keys = [navigation_action_key(candidate) for candidate in candidates]
    for candidate, key in zip(candidates, visible_keys):
        ledger.seen(node.id, key, candidate.label)
    # 원장에는 있는데 이번 후보엔 없는 미시도 버튼을 세고, 연속으로 안 보이면 정리합니다.
    ledger.note_visit(node.id, visible_keys, FRONTIER_MAX_MISSES)

    goal = walker.decide(node.id, visible_keys)
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

    back_key_enabled = back_method(ADB_PATH, BACK_KEY) != "off"

    if goal is None:
        if walker.stop_reason == "unreachable":
            can_escape = (
                back_key_enabled
                and journey.escape_presses < STUCK_BACK_KEY_MAX
                and node.id != walker.root_screen_id
            )
            if can_escape:
                # 3단계: 이 화면에서 프론티어로 가는 길을 모릅니다. 좌표가 필요 없는
                # 뒤로 키로 빠져나와 다음 스텝에서 다시 계획합니다.
                try:
                    pressed = execute_back_key(
                        key=BACK_KEY,
                        adb_path=ADB_PATH,
                        dry_run=dry_run,
                        allow_taps=allow_taps,
                        cancel_check=cancel_check,
                    )
                except RuntimeError as error:
                    return _StepResult(
                        "stop", node_id=node.id, stop_reason=str(error), stop_kind="error"
                    )
                effect = _settle_after_action(sct, bundle) if pressed else None
                journey.escape_presses += 1
                record(
                    {
                        "type": "back_key",
                        "outcome": "stuck_back_key" if pressed else "back_key_disabled",
                        "executed": pressed,
                        "method": back_method(ADB_PATH, BACK_KEY),
                        "escape_presses": journey.escape_presses,
                        "tap_effect": _effect_record(effect),
                    },
                    selected=BACK_KEY_LABEL,
                    executed=pressed,
                    strategy="frontier_backtracking:escape",
                )
                if pressed:
                    print(
                        f"[AUTODRIVE] 가는 길을 모르는 화면이라 뒤로({back_method(ADB_PATH, BACK_KEY)})를 보냈습니다 "
                        f"({journey.escape_presses}/{STUCK_BACK_KEY_MAX})."
                    )
                    journey.set_pending("escape", BACK_KEY_LABEL, BACK_KEY_ACTION_KEY)
                    return _StepResult("advance", node_id=node.id)
            else:
                record_without_tap("frontier_unreachable")
            return _StepResult(
                "stop",
                node_id=node.id,
                stop_reason=(
                    "남은 프론티어가 있지만 현재 화면에서 가는 경로를 아직 모릅니다"
                    + (
                        f"(뒤로 키 {journey.escape_presses}번으로도 못 빠져나옴). "
                        if back_key_enabled and node.id != walker.root_screen_id
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
        goal, candidates, node.id, ledger, back_key_enabled=back_key_enabled
    )

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
    tap_button = None if is_back_key else button
    refine_record = None

    if not is_back_key and TAP_REFINE and not dry_run and allow_taps:
        # 1단계: 누르기 직전 확대 재확인.
        refine = refine_tap_target(client, bundle.full_image, button)
        refine_record = refine.to_record()
        if refine.found:
            height, width = bundle.full_image.shape[:2]
            tap_button = button.model_copy(update={
                "center": NormalizedPoint(x=refine.x / width, y=refine.y / height),
            })
            print(
                f"[TAP REFINE] {label!r}: {refine.initial} -> ({refine.x}, {refine.y}) "
                f"이동 {refine.shift_px}px ({refine.used_pass})"
            )
        elif refine.error is not None:
            print(f"[TAP REFINE] 확인 실패({refine.error}); 처음 좌표로 누릅니다.")
        else:
            # 확대본과 전체 화면 어디서도 버튼을 못 찾았습니다. 없는 버튼을 누르면
            # 엉뚱한 화면으로 가서 틀린 간선이 생기므로 누르지 않습니다.
            print(f"[TAP REFINE] {label!r} 버튼을 화면에서 찾지 못해 누르지 않습니다.")
            if goal.kind == "frontier":
                ledger.close_not_found(node.id, action_key)
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

    prev_entry = ledger.ledger.entries.get(f"{node.id}:{action_key}")
    prev_verdict = prev_entry.verdict if prev_entry is not None else None
    prev_target = prev_entry.target_screen_id if prev_entry is not None else None
    if goal.kind in ("frontier", "replay") and executed:
        ledger.mark_attempted(node.id, action_key, label)

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
    print(
        f"[AUTODRIVE] {node.screen_type} -> {label!r} ({goal.kind}), "
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

    journey.escape_presses = 0
    journey.set_pending(
        goal.kind,
        label,
        action_key,
        used_home=used_home,
        prev_verdict=prev_verdict,
        prev_target=prev_target,
    )
    return _StepResult("advance", node_id=node.id)


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
    elif method == "keyboard":
        print(f"[AUTODRIVE] 뒤로: 키보드 {BACK_KEY!r}")
    else:
        print("[AUTODRIVE] 뒤로: 꺼짐 (ADB_PATH와 BACK_KEY가 비어 있음)")

    store = GraphStore()
    ledger = LedgerStore()
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
    )
    journey = _Journey()

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
