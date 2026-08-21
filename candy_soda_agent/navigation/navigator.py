"""Survey 기록과 전환 그래프를 함께 만드는 자동 탐색기."""

import time
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
from storage import load_recent_survey_records
from survey import analyze_survey_screen, record_survey_screen
from survey_report import write_survey_report


def run_autodrive(
    client,
    max_steps: int = 40,
    *,
    allow_taps: bool = True,
) -> None:
    if CAPTURE_MODE == "board":
        raise ValueError(
            "--autodrive는 CAPTURE_MODE=window 또는 monitor가 필요합니다."
        )

    store = GraphStore()
    used_actions = store.explored_actions()
    session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    previous_node = None
    previous_action = None
    previous_action_key = None

    with mss.MSS() as sct:
        for step in range(1, max_steps + 1):
            bundle = capture_bundle(
                sct,
                CAPTURE_MODE,
                MONITOR_INDEX,
                BOARD_OFFSET,
                WINDOW_OFFSET,
            )

            recent_records = load_recent_survey_records(
                SURVEY_DEDUP_SCAN_LIMIT
            )
            decision = analyze_survey_screen(
                client,
                bundle.full_image,
                None,
                survey_context=recent_records,
            )

            if decision.screen_type == "loading":
                time.sleep(1.0)
                continue

            node = store.observe(decision, bundle.full_image)

            if previous_node:
                store.connect(
                    previous_node,
                    node.id,
                    previous_action or "unknown",
                    previous_action_key or "unknown",
                )

            button = choose_button(
                decision,
                node.id,
                used_actions,
            )

            executed = False
            if button is not None:
                action_id = navigation_action_id(node.id, button)
                used_actions.add(action_id)
                executed = execute_button_tap(
                    button,
                    bundle.full_region,
                    dry_run=DRY_RUN,
                    allow_taps=allow_taps,
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
                "dry_run": DRY_RUN,
                "allow_taps": allow_taps,
            }
            record_survey_screen(
                bundle,
                decision,
                session_id=session_id,
                step=step,
                mode="autodrive",
                action=action,
                recent_records=recent_records,
                graph=store.graph,
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
                print(
                    "[AUTODRIVE] 현재 화면의 안전한 이동 버튼을 "
                    "모두 탐색해 종료합니다."
                )
                break

            print(
                f"[AUTODRIVE] {node.screen_type} "
                f"-> {button.label!r}, executed={executed}"
            )

            if not executed:
                break

            previous_node = node.id
            previous_action = button.label
            previous_action_key = navigation_action_key(button)
            time.sleep(1.5)

    report_path = write_survey_report(
        load_recent_survey_records(10000),
        graph=store.graph,
    )
    print(f"[AUTODRIVE REPORT] {report_path}")
