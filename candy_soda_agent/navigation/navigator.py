"""Survey 기록과 전환 그래프를 함께 만드는 자동 탐색기.

2단계: 예전에는 현재 화면에서 안 눌러본 안전 버튼이 없으면 그 자리에서 멈췄습니다
(막다른 화면 = 탐색 종료). 이제는 프론티어(화면 전체에서 아직 안 눌러본 (화면,행동)
쌍)가 남아 있으면 그래프 경로를 따라 그 화면까지 이동한 뒤 계속합니다. 이동은
"로컬 재생"(간선을 그대로 재생)을 먼저 시도하고, 도착 화면이 기대와 다르면(팝업/광고
등으로 게임 UI가 결정적이지 않아 생김) 루트로 리셋한 뒤 다시 재생하는 "혼합" 전략을
씁니다(navigation.frontier.FrontierWalker). 전역 프론티어가 모두 소진되면 그때
탐색을 마치고 보고서를 씁니다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import mss

from action_executor import execute_button_tap
from capture_modes import capture_bundle
from config import (
    BOARD_OFFSET,
    CAPTURE_MODE,
    DRY_RUN,
    FRONTIER_MAX_MISSES,
    FRONTIER_MAX_REPLAN_ATTEMPTS,
    HOME_RESET_MAX_BACK_TAPS,
    MONITOR_INDEX,
    ROOT_SCREEN_ID,
    SCREEN_FRAGMENTATION_WARN_COUNT,
    SURVEY_DEDUP_SCAN_LIMIT,
    SURVEY_MIN_BUTTON_CONFIDENCE,
    WINDOW_OFFSET,
)
from navigation.frontier import FrontierWalker, StepGoal, find_reset_button
from navigation.graph import GraphStore, screen_type_node_counts
from navigation.ledger import LedgerStore
from navigation.policy import navigation_action_key
from storage import load_all_survey_records, load_recent_survey_records
from survey import analyze_survey_screen, record_survey_screen
from survey_report import write_survey_report
from survey_utils import safe_button_candidates


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


@dataclass
class _Journey:
    """스텝 사이에 들고 다니는 상태: 직전에 실제로 탭을 실행했다면 그 정보를 담아
    다음 관찰 때 그래프 간선을 잇고 원장/프론티어 계획에 결과를 반영합니다."""

    node_id: str | None = None
    pending_action_label: str | None = None
    pending_action_key: str | None = None
    pending_kind: str | None = None  # "frontier" | "replay" | "reset" | None(시도 없음)
    pending_used_home: bool = False

    def clear_pending(self) -> None:
        self.pending_action_label = None
        self.pending_action_key = None
        self.pending_kind = None
        self.pending_used_home = False


def _finalize_previous_attempt(
    journey: _Journey,
    store: GraphStore,
    ledger: LedgerStore,
    walker: FrontierWalker,
    node,
) -> None:
    """직전 스텝에서 실제로 탭을 실행했다면, 이번 관찰 결과로 그 결과를 확정합니다."""

    if journey.node_id is None or journey.pending_kind is None:
        return

    store.connect(
        journey.node_id,
        node.id,
        journey.pending_action_label or "unknown",
        journey.pending_action_key or "unknown",
    )

    if journey.pending_kind in ("frontier", "replay") and journey.pending_action_key:
        if node.id == journey.node_id:
            verdict = "no_change"  # 눌렀지만 화면이 그대로입니다.
        elif node.visits == 1:
            verdict = "new_screen"  # 처음 보는 화면으로 넘어갔습니다.
        else:
            verdict = "known_screen"  # 이미 알던 다른 화면으로 넘어갔습니다.
        ledger.finalize(
            journey.node_id, journey.pending_action_key, verdict, target_screen_id=node.id
        )

    if journey.pending_kind == "replay":
        walker.report_replay_step(executed=True, arrived_screen_id=node.id)
    elif journey.pending_kind == "reset":
        walker.report_reset_step(
            executed=True,
            arrived_screen_id=node.id,
            used_home=journey.pending_used_home,
        )

    journey.clear_pending()


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
):
    """이번 스텝의 목표(goal)에 맞는 실제 버튼 후보를 오늘 관찰된 후보 중에서 찾습니다.
    반환값: (button | None, action_key, used_home)."""

    if goal.kind == "frontier":
        frontier_keys = set(ledger.frontier_at(node_id))
        button = next(
            (b for b in candidates if navigation_action_key(b) in frontier_keys),
            None,
        )
        return button, (navigation_action_key(button) if button else ""), False

    if goal.kind == "replay":
        button = _button_for_action_key(candidates, goal.action_key)
        return button, goal.action_key, False

    # goal.kind == "reset": home을 먼저 찾고, 없으면 back을 찾습니다.
    home_button = find_reset_button(candidates, prefer="home")
    if home_button is not None:
        return home_button, navigation_action_key(home_button), True
    back_button = find_reset_button(candidates, prefer="back")
    if back_button is not None:
        return back_button, navigation_action_key(back_button), False
    return None, "", False


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
    """탐색 한 단계: 취소 확인 -> 캡처/분석 -> 그래프/원장 갱신 -> 프론티어 계획 -> 탭."""

    if cancel_check:
        reason = cancel_check()
        if reason:
            return _StepResult("stop", stop_reason=reason)

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

    node = store.observe(decision, bundle.full_image)
    if node.visits == 1:
        _warn_if_fragmented(store, node.screen_type)
    _finalize_previous_attempt(journey, store, ledger, walker, node)
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

    def record_without_tap(outcome: str, **extra) -> None:
        # 탭 없이 끝나는 스텝도 화면 증거는 survey 기록과 여정 로그에 남깁니다.
        record_survey_screen(
            bundle,
            decision,
            session_id=session_id,
            step=step,
            mode="autodrive",
            action={"type": "record_only", "outcome": outcome, **extra},
            recent_records=recent_records,
            screen_id=node.id,
        )
        store.log({
            "step": step,
            "screen_id": node.id,
            "screen_type": node.screen_type,
            "selected_button": None,
            "executed": False,
            "strategy": "frontier_backtracking",
        })

    if goal is None:
        if walker.stop_reason == "unreachable":
            record_without_tap("frontier_unreachable")
            return _StepResult(
                "stop",
                node_id=node.id,
                stop_reason=(
                    "남은 프론티어가 있지만 현재 화면에서 가는 경로를 아직 모릅니다. "
                    "다시 실행하면 이어서 시도합니다."
                ),
            )
        record_without_tap("frontier_converged")
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason="전역 프론티어(아직 안 눌러본 화면·행동)를 모두 확인했습니다.",
        )

    if goal.kind == "reobserve":
        # 미시도 버튼이 이번 후보에 안 보였습니다. 누르지 않고 다음 스텝에 다시 봅니다.
        record_without_tap("reobserve_missing_frontier")
        return _StepResult("advance", node_id=node.id)

    button, action_key, used_home = _choose_action_for_goal(
        goal, candidates, node.id, ledger
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

    try:
        executed = execute_button_tap(
            button,
            bundle.full_region,
            dry_run=dry_run,
            allow_taps=allow_taps,
            cancel_check=cancel_check,
        )
    except RuntimeError as error:
        # 취소/포커스 오류로 예외가 나도 탐색을 죽이지 않고 지금까지 기록으로 멈춥니다.
        return _StepResult("stop", node_id=node.id, stop_reason=str(error))

    if goal.kind in ("frontier", "replay"):
        ledger.mark_attempted(node.id, action_key, button.label)

    action = {
        "type": "tap_button",
        "outcome": "tap_executed" if executed else "tap_dry_run_or_disabled",
        "button": button.model_dump(),
        "executed": executed,
        "dry_run": dry_run,
        "allow_taps": allow_taps,
        "goal_kind": goal.kind,
    }
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
        "screen_id": node.id,
        "screen_type": node.screen_type,
        "selected_button": button.label,
        "executed": executed,
        "strategy": f"frontier_backtracking:{goal.kind}",
    })

    print(f"[AUTODRIVE] {node.screen_type} -> {button.label!r} ({goal.kind}), executed={executed}")

    if not executed:
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                f"버튼 {button.label!r} 탭이 실행되지 않아 "
                "화면 전환을 확인할 수 없습니다."
            ),
        )

    journey.pending_action_label = button.label
    journey.pending_action_key = action_key
    journey.pending_kind = goal.kind
    journey.pending_used_home = used_home
    return _StepResult("advance", node_id=node.id)


def run_autodrive(
    client,
    max_steps: int = 40,
    *,
    allow_taps: bool = False,
    cancel_check: Callable[[], str | None] | None = None,
) -> None:
    """프론티어가 소진될 때까지(또는 max_steps에 이를 때까지) 그래프를 갱신하며
    화면을 탐색하는 메인 루프입니다."""

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
        return

    store = GraphStore()
    ledger = LedgerStore()
    walker = FrontierWalker(
        store,
        ledger,
        max_back_taps=HOME_RESET_MAX_BACK_TAPS,
        max_replan_attempts=FRONTIER_MAX_REPLAN_ATTEMPTS,
        root_screen_id=ROOT_SCREEN_ID,
    )
    journey = _Journey()
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    stop_reason = f"최대 단계 수 {max_steps}회를 모두 실행했습니다."

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
            if result.kind == "stop":
                stop_reason = result.stop_reason
                break

            time.sleep(1.5)

    print(f"[AUTODRIVE] 탐색 종료: {stop_reason}")
    report_path = write_survey_report(
        load_all_survey_records(),
        graph=store.graph,
        ledger=ledger,
    )
    print(f"[AUTODRIVE REPORT] {report_path}")
