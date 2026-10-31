"""화면별 미탐색 안전 버튼 선택."""

import re

from config import SURVEY_MIN_BUTTON_CONFIDENCE
from game_knowledge import label_rules
from survey_utils import (
    button_key,
    normalize_text,
    safe_button_candidates,
)


# 낱말 목록(UI 명사·별칭·꾸밈 낱말·꼬리)은 knowledge/label_rules.json에 있습니다.


def _strip_ui_suffix_tokens(value: str) -> str:
    """끝에 붙은 UI 명사를 지웁니다("Settings tab" -> "settings"). 라벨이 통째로 UI 명사면
    (예: "Menu") 그대로 둡니다. 뒤쪽만 지우므로 "Team Shop"의 "Shop"은 남습니다."""

    nouns = label_rules().ui_nouns
    tokens = value.split()
    while len(tokens) > 1 and tokens[-1] in nouns:
        tokens.pop()
    return " ".join(tokens)


# 별칭은 라벨의 모든 낱말이 별칭이거나 꾸밈 낱말일 때만 합칩니다: "친구 더 추가"·"친구 초대"는
# 탭바의 "친구"와 합치지 않습니다(실측: "친구 더 추가"가 이미 눌러 본 "친구" 탭으로 잡혀 안 눌림).


def _alias_canonical(stable: str) -> str | None:
    rules = label_rules()
    tokens = stable.split()
    for canonical, aliases in rules.action_aliases:

        def is_alias(token: str) -> bool:
            return token in aliases or any(
                token.startswith(alias) and token[len(alias):] in rules.alias_tails
                for alias in aliases
            )

        if any(is_alias(token) for token in tokens) and all(
            is_alias(token) or token in rules.alias_decorations for token in tokens
        ):
            return canonical
    return None


def canonical_action_label(label: str) -> str:
    """버튼 문구와 숫자 차이 정규화."""

    stable = re.sub(r"\([^)]*\)", " ", label)
    stable = re.sub(r"\d+", "#", normalize_text(stable))
    stable = _strip_ui_suffix_tokens(stable)
    if stable == "x":
        # X 아이콘("X", "X 버튼")은 닫기입니다. "box"처럼 x가 든 낱말은 건드리지 않습니다.
        return "close"
    return _alias_canonical(stable) or stable


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
