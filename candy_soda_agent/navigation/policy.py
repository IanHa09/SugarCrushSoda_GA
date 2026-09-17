"""화면별 미탐색 안전 버튼 선택."""

import re

from config import SURVEY_MIN_BUTTON_CONFIDENCE
from survey_utils import (
    button_key,
    normalize_text,
    safe_button_candidates,
)


# 같은 버튼인데 LLM이 "Teams (bottom tab)" / "Teams tab (bottom nav)"처럼 UI 명사를
# 붙였다 뗐다 해서 action_key가 갈라지는 것을 막습니다. 뒤쪽 토큰만 지우므로
# "Settings"와 "Settings tab"은 합쳐지지만 "Team Shop"의 "Shop"은 남습니다.
_UI_SUFFIX_TOKENS = frozenset({
    "tab", "tabs", "button", "buttons", "btn", "icon", "icons",
    "menu", "bar", "panel", "탭", "버튼", "아이콘", "메뉴",
})


def _strip_ui_suffix_tokens(value: str) -> str:
    """끝에 붙은 UI 명사를 지웁니다. 라벨이 통째로 UI 명사면(예: "Menu") 그대로 둡니다."""

    tokens = value.split()
    while len(tokens) > 1 and tokens[-1] in _UI_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


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
    stable = _strip_ui_suffix_tokens(stable)
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
