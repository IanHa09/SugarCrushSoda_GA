# 기존 자동 플레이 루프를 사용 X, 독립적인 실행기

import time

import mss

from action_executor import execute_button_tap
from capture_modes import capture_bundle
from config import (
    BOARD_OFFSET,
    CAPTURE_MODE,
    DRY_RUN,
    MONITOR_INDEX,
    WINDOW_OFFSET,
)
from navigation.graph import GraphStore
from navigation.policy import choose_button, is_home
from navigation.report import write_report
from survey import analyze_survey_screen
from survey_utils import button_key


def run_autodrive(client, max_steps: int = 40) -> None:
    if CAPTURE_MODE == "board":
        raise ValueError(
            "--autodrive는 CAPTURE_MODE=window 또는 monitor가 필요합니다."
        )

    store = GraphStore()
    used_actions: set[str] = set()

    previous_node = None
    previous_action = None

    with mss.MSS() as sct:
        for step in range(1, max_steps + 1):
            bundle = capture_bundle(
                sct,
                CAPTURE_MODE,
                MONITOR_INDEX,
                BOARD_OFFSET,
                WINDOW_OFFSET,
            )

            decision = analyze_survey_screen(
                client,
                bundle.full_image,
                None,
            )

            if decision.screen_type == "loading":
                time.sleep(1.0)
                continue

            node = store.observe(decision, bundle.full_image)

            if previous_node and previous_node != node.id:
                store.connect(
                    previous_node,
                    node.id,
                    previous_action or "unknown",
                )

            # 마지막 5단계는 새로운 탐색보다 홈 복귀를 우선합니다.
            force_return = step > max_steps - 5

            button = choose_button(
                decision,
                node.id,
                used_actions,
                force_return=force_return,
            )

            store.log({
                "step": step,
                "screen_id": node.id,
                "screen_type": node.screen_type,
                "selected_button": button.label if button else None,
                "force_return": force_return,
            })

            # 홈에서 더 탐색할 버튼이 없거나 복귀가 끝난 경우 종료합니다.
            if button is None:
                if is_home(node.screen_type):
                    print("[AUTODRIVE] 탐색을 마치고 홈에서 종료합니다.")
                else:
                    print("[AUTODRIVE] 안전한 이동 버튼이 없어 중단합니다.")
                break

            action_id = f"{node.id}:{button_key(button.role, button.label)}"
            used_actions.add(action_id)

            executed = execute_button_tap(
                button,
                bundle.full_region,
                dry_run=DRY_RUN,
                allow_taps=True,
            )

            print(
                f"[AUTODRIVE] {node.screen_type} "
                f"-> {button.label!r}, executed={executed}"
            )

            if not executed:
                break

            previous_node = node.id
            previous_action = button.label
            time.sleep(1.5)

    report_path = write_report(store.graph)
    print(f"[AUTODRIVE REPORT] {report_path}")