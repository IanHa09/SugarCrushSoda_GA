"""화면별 미탐색 안전 버튼 선택."""

import re

from config import SURVEY_MIN_BUTTON_CONFIDENCE
from survey_utils import (
    button_key,
    normalize_text,
    safe_button_candidates,
)


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
    """정렬된 안전 버튼 후보 중 아직 안 눌러본 첫 번째를 고릅니다."""

    candidates = safe_button_candidates(
        decision,
        min_confidence=SURVEY_MIN_BUTTON_CONFIDENCE,
    )

    for button in candidates:
        if navigation_action_id(node_id, button) not in used_actions:
            return button

    return None
