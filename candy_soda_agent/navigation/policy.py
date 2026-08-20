# 결제 및 광고/로그인 버튼은 safe_button_candidates에 포함되지 않음.
# 탐색할 버튼이 없으면 Home, Back, Close 버튼으로 이동

from survey_utils import (
    button_key,
    normalize_text,
    safe_button_candidates,
)

HOME_TYPES = {"map_or_level_select"}
RETURN_WORDS = {
    "home", "map", "back", "close", "exit",
    "홈", "지도", "뒤로", "닫기", "나가기",
}

ROLE_PRIORITY = {
    "structure": 0,
    "progression": 1,
    "safe_navigation": 2,
}

def is_home(screen_type: str) -> bool:
    return screen_type in HOME_TYPES

def _is_return_button(button) -> bool:
    label = normalize_text(button.label)
    return any(word in label for word in RETURN_WORDS)

def choose_button(
        decision,
        node_id: str,
        used_actions: set[str],
        *,
        force_return: bool,
):
    candidates = safe_button_candidates(
        decision,
        min_confidence=0.60,
    )

    return_buttons = [
        button for button in candidates if _is_return_button(button)
    ]

    if not return_buttons:
        return_buttons = [
            button
            for button in candidates
            if button.role == "safe_navigation"
        ]

    if force_return:
        return None if is_home(decision.screen_type) else (
            return_buttons[0] if return_buttons else None
        )

    if force_return:
        return None if is_home(decision.screen_type) else (
            return_buttons[0] if return_buttons else None
        )

    fresh = [
        button
        for button in candidates
        if not _is_return_button(button)
        and (
            f"{node_id}:{button_key(button.role, button.label)}"
            not in used_actions
        )
    ]

    if fresh:
        return min(
            fresh,
            key=lambda button: (
                ROLE_PRIORITY.get(button.role, 99),
                -button.confidence,
            ),
        )

    if not is_home(decision.screen_type) and return_buttons:
        return return_buttons[0]

    return None