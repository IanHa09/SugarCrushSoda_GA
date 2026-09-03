"""Survey 기록과 전환 그래프를 함께 만드는 자동 탐색기."""

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
    MONITOR_INDEX,
    SURVEY_DEDUP_SCAN_LIMIT,
    WINDOW_OFFSET,
)
from navigation.graph import GraphStore
from navigation.policy import (
    choose_button,
    navigation_action_id,
    navigation_action_key,
)
from storage import load_all_survey_records, load_recent_survey_records
from survey import analyze_survey_screen, record_survey_screen
from survey_report import write_survey_report


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
    action_label: str | None = None
    action_key: str | None = None
    stop_reason: str | None = None


def _observe_and_connect(
    store: GraphStore,
    decision,
    image,
    *,
    previous_node: str | None,
    previous_action: str | None,
    previous_action_key: str | None,
):
    """화면을 그래프 노드로 기록하고, 있으면 이전 노드와 연결합니다."""

    node = store.observe(decision, image)
    if previous_node:
        store.connect(
            previous_node,
            node.id,
            previous_action or "unknown",
            previous_action_key or "unknown",
        )
    return node


def _choose_and_tap(
    decision,
    node_id: str,
    used_actions: set[str],
    full_region: dict[str, int],
    *,
    dry_run: bool,
    allow_taps: bool,
    cancel_check: Callable[[], str | None] | None,
):
    """안전한 미탐색 버튼을 고르고 탭을 시도해, 기록용 행동 정보를 만듭니다."""

    button = choose_button(decision, node_id, used_actions)

    executed = False
    if button is not None:
        action_id = navigation_action_id(node_id, button)
        used_actions.add(action_id)
        executed = execute_button_tap(
            button,
            full_region,
            dry_run=dry_run,
            allow_taps=allow_taps,
            cancel_check=cancel_check,
        )

    action = {
        "type": "tap_button" if button else "record_only",
        "outcome": (
            "tap_executed"
            if executed
            else "tap_dry_run_or_disabled"
            if button
            else "no_safe_action"
        ),
        "button": button.model_dump() if button else None,
        "executed": executed,
        "dry_run": dry_run,
        "allow_taps": allow_taps,
    }
    return button, executed, action


def _run_autodrive_step(
    client,
    sct,
    store: GraphStore,
    used_actions: set[str],
    *,
    step: int,
    session_id: str,
    dry_run: bool,
    allow_taps: bool,
    cancel_check: Callable[[], str | None] | None,
    previous_node: str | None,
    previous_action: str | None,
    previous_action_key: str | None,
) -> _StepResult:
    """탐색 한 단계: 취소 확인 -> 캡처/분석 -> 그래프 갱신 -> 버튼 실행/기록."""

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

    node = _observe_and_connect(
        store,
        decision,
        bundle.full_image,
        previous_node=previous_node,
        previous_action=previous_action,
        previous_action_key=previous_action_key,
    )

    try:
        button, executed, action = _choose_and_tap(
            decision,
            node.id,
            used_actions,
            bundle.full_region,
            dry_run=dry_run,
            allow_taps=allow_taps,
            cancel_check=cancel_check,
        )
    except RuntimeError as error:
        # 취소/포커스 오류로 예외가 나도 탐색을 죽이지 않고 지금까지 기록으로 멈춥니다.
        return _StepResult("stop", node_id=node.id, stop_reason=str(error))

    record_survey_screen(
        bundle,
        decision,
        session_id=session_id,
        step=step,
        mode="autodrive",
        action=action,
        recent_records=recent_records,
    )

    store.log({
        "step": step,
        "screen_id": node.id,
        "screen_type": node.screen_type,
        "selected_button": button.label if button else None,
        "executed": executed,
        "strategy": "parallel_frontier",
    })

    if button is None:
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                "현재 화면에서 아직 눌러보지 않은 안전한 이동 버튼이 없습니다."
            ),
        )

    print(f"[AUTODRIVE] {node.screen_type} -> {button.label!r}, executed={executed}")

    if not executed:
        return _StepResult(
            "stop",
            node_id=node.id,
            stop_reason=(
                f"버튼 {button.label!r} 탭이 실행되지 않아 "
                "화면 전환을 확인할 수 없습니다."
            ),
        )

    return _StepResult(
        "advance",
        node_id=node.id,
        action_label=button.label,
        action_key=navigation_action_key(button),
    )


def run_autodrive(
    client,
    max_steps: int = 40,
    *,
    allow_taps: bool = False,
    cancel_check: Callable[[], str | None] | None = None,
) -> None:
    """그래프를 갱신하며 화면을 연속 탐색하는 메인 루프입니다."""

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
    used_actions = store.explored_actions()
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    previous_node = None
    previous_action = None
    previous_action_key = None
    stop_reason = f"최대 단계 수 {max_steps}회를 모두 실행했습니다."

    with mss.MSS() as sct:
        for step in range(1, max_steps + 1):
            result = _run_autodrive_step(
                client,
                sct,
                store,
                used_actions,
                step=step,
                session_id=session_id,
                dry_run=DRY_RUN,
                allow_taps=allow_taps,
                cancel_check=cancel_check,
                previous_node=previous_node,
                previous_action=previous_action,
                previous_action_key=previous_action_key,
            )

            if result.kind == "skip":
                continue
            if result.kind == "stop":
                stop_reason = result.stop_reason
                break

            previous_node = result.node_id
            previous_action = result.action_label
            previous_action_key = result.action_key
            time.sleep(1.5)

    print(f"[AUTODRIVE] 탐색 종료: {stop_reason}")
    report_path = write_survey_report(
        load_all_survey_records(),
        graph=store.graph,
    )
    print(f"[AUTODRIVE REPORT] {report_path}")
