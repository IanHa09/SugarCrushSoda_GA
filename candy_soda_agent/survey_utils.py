"""게임 구조 조사 결과의 중복 키와 버튼 우선순위를 계산합니다."""

from __future__ import annotations

import hashlib
import re
from typing import Any


SAFE_BUTTON_ROLES = {"safe_navigation", "progression", "structure"}
BLOCKED_BUTTON_ROLES = {
    "risky_monetization",
    "risky_ad",
    "risky_account",
    "unknown",
}
BUTTON_ROLE_PRIORITY = {
    "progression": 0,
    "structure": 1,
    "safe_navigation": 2,
}
RATING_SIGNAL_FIELDS = [
    "monetization",
    "ads",
    "loot_or_random_reward",
    "violence",
    "fear_or_horror",
    "sexuality_or_nudity",
    "profanity",
    "alcohol_tobacco_drugs",
    "gambling",
    "user_generated_content",
    "social_or_chat",
    "personal_data_or_account",
    "location_or_device_permissions",
    "time_pressure_or_retention",
]


def normalize_text(value: str) -> str:
    """대소문자, 공백, 흔한 기호 차이 때문에 중복이 늘지 않게 정규화합니다."""

    lowered = value.casefold().strip()
    compact = re.sub(r"[^0-9a-z가-힣]+", " ", lowered)
    return re.sub(r"\s+", " ", compact).strip()


def stable_hash(parts: list[str]) -> str:
    payload = "|".join(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def element_key(category: str, name: str) -> str:
    return stable_hash(["element", normalize_text(category), normalize_text(name)])


def button_key(role: str, label: str) -> str:
    return stable_hash(["button", normalize_text(role), normalize_text(label)])


def _get_value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def screen_signature(decision: Any) -> str:
    """화면 타입과 핵심 텍스트/요소/버튼으로 같은 화면 여부를 대략 판단합니다."""

    parts = ["screen", normalize_text(str(_get_value(decision, "screen_type", "")))]
    visible_text = _get_value(decision, "visible_text", []) or []
    text_tokens = sorted({normalize_text(str(item)) for item in visible_text if item})
    parts.extend(text_tokens[:8])

    elements = _get_value(decision, "game_elements", []) or []
    for element in elements[:12]:
        category = str(_get_value(element, "category", ""))
        name = str(_get_value(element, "name", ""))
        parts.append(element_key(category, name))

    buttons = _get_value(decision, "button_candidates", []) or []
    for button in buttons[:8]:
        role = str(_get_value(button, "role", ""))
        label = str(_get_value(button, "label", ""))
        parts.append(button_key(role, label))

    return stable_hash(parts)


def collect_element_keys(decision: Any) -> list[str]:
    keys: list[str] = []
    for element in _get_value(decision, "game_elements", []) or []:
        category = str(_get_value(element, "category", ""))
        name = str(_get_value(element, "name", ""))
        key = element_key(category, name)
        if key not in keys:
            keys.append(key)
    return keys


def collect_button_keys(decision: Any) -> list[str]:
    keys: list[str] = []
    for button in _get_value(decision, "button_candidates", []) or []:
        role = str(_get_value(button, "role", ""))
        label = str(_get_value(button, "label", ""))
        key = button_key(role, label)
        if key not in keys:
            keys.append(key)
    return keys


def safe_button_candidates(
    decision: Any,
    *,
    min_confidence: float,
    blocked_button_keys: set[str] | None = None,
) -> list[Any]:
    blocked = blocked_button_keys or set()
    screen_type = str(_get_value(decision, "screen_type", ""))
    buttons = []
    for button in _get_value(decision, "button_candidates", []) or []:
        role = str(_get_value(button, "role", "unknown"))
        label = str(_get_value(button, "label", ""))
        normalized_label = normalize_text(label)
        confidence = float(_get_value(button, "confidence", 0.0) or 0.0)
        center = _get_value(button, "center", None)
        key = button_key(role, label)
        if role not in SAFE_BUTTON_ROLES:
            continue
        if role == "structure" and any(
            token in normalized_label
            for token in ("music", "sound", "mute")
        ):
            continue
        if screen_type == "shop_or_currency" and "shop" in normalized_label:
            continue
        if screen_type == "map_or_level_select" and normalized_label == "home":
            continue
        if key in blocked:
            continue
        if center is None:
            continue
        if confidence < min_confidence:
            continue
        buttons.append(button)
    return sorted(
        buttons,
        key=lambda button: (
            BUTTON_ROLE_PRIORITY.get(str(_get_value(button, "role", "")), 99),
            -float(_get_value(button, "confidence", 0.0) or 0.0),
        ),
    )


def choose_next_button(
    decision: Any,
    *,
    min_confidence: float,
    blocked_button_keys: set[str] | None = None,
) -> Any | None:
    candidates = safe_button_candidates(
        decision,
        min_confidence=min_confidence,
        blocked_button_keys=blocked_button_keys,
    )
    return candidates[0] if candidates else None
