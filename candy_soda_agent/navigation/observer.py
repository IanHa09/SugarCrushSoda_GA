"""LLM 표현 차이에 강한 탐색 화면 ID 생성."""

import hashlib
import re

from survey_utils import SAFE_BUTTON_ROLES, normalize_text


# 병렬 섹션용 단일 노드 화면
SINGLETON_SCREEN_TYPES = {
    "map_or_level_select",
    "settings_menu",
    "shop_or_currency",
    "booster_panel",
}


def _stable_text(value: str) -> str:
    return re.sub(r"\d+", "#", normalize_text(value))


def canonical_screen_id(screen_type: str) -> str:
    digest = hashlib.sha1(screen_type.encode()).hexdigest()[:12]
    return f"screen_{digest}"


def screen_id(decision) -> str:
    if decision.screen_type in SINGLETON_SCREEN_TYPES:
        return canonical_screen_id(decision.screen_type)

    texts = sorted({
        _stable_text(value)
        for value in decision.visible_text[:4]
        if _stable_text(value)
    })

    buttons = sorted({
        _stable_text(button.label)
        for button in decision.button_candidates
        if button.role in SAFE_BUTTON_ROLES and button.label
    })

    payload = "|".join([decision.screen_type, *texts, *buttons])
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"screen_{digest}"
