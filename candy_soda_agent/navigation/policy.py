"""화면별 미탐색 안전 버튼 선택."""

import re

from survey_utils import (
    button_key,
    normalize_text,
    safe_button_candidates,
)

ROLE_PRIORITY = {
    "structure": 0,
    "progression": 1,
    "safe_navigation": 2,
}


_ACTION_ALIASES = (
    ("settings", ("settings", "setting", "gear", "cog", "설정")),
    ("home", ("home", "홈")),
    ("shop", ("shop", "store", "상점")),
    ("event", ("event", "events", "이벤트")),
    ("mission", ("mission", "missions", "미션")),
    ("map", ("map", "지도")),
    ("profile", ("profile", "프로필")),
    ("friends", ("friend", "friends", "친구")),
    ("back", ("back", "뒤로")),
    ("close", ("close", "닫기")),
)


def canonical_action_label(label: str) -> str:
    """버튼 문구와 숫자 차이 정규화."""

    stable = re.sub(r"\([^)]*\)", " ", label)
    stable = re.sub(r"\d+", "#", normalize_text(stable))
    for canonical, aliases in _ACTION_ALIASES:
        if any(alias in stable for alias in aliases):
            return canonical
    return stable


def navigation_action_key_from_label(label: str) -> str:
    return button_key("navigation", canonical_action_label(label))


def navigation_action_key(button) -> str:
    return navigation_action_key_from_label(button.label)


def navigation_action_id(node_id: str, button) -> str:
    return f"{node_id}:{navigation_action_key(button)}"


def choose_button(
    decision,
    node_id: str,
    used_actions: set[str],
):
    candidates = safe_button_candidates(
        decision,
        min_confidence=0.60,
    )

    fresh = [
        button
        for button in candidates
        if navigation_action_id(node_id, button) not in used_actions
    ]

    if fresh:
        return min(
            fresh,
            key=lambda button: (
                ROLE_PRIORITY.get(button.role, 99),
                -button.confidence,
            ),
        )

    return None
