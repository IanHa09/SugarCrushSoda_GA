"""게임 구조 조사 결과의 중복 키와 버튼 우선순위를 계산합니다."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any


SAFE_BUTTON_ROLES = {"safe_navigation", "progression", "structure"}

# 이름에서 "무엇인지"가 아니라 "어떻게 생겼는지"만 말하는 낱말입니다. LLM이 같은
# 요소를 "부스터", "부스터 아이콘", "부스터 선택 버튼"처럼 매번 다르게 불러서 한
# 요소가 여러 항목으로 쪼개지는데, 이 낱말을 떼면 같은 이름으로 모입니다.
GENERIC_NAME_TOKENS = frozenset({
    "버튼", "아이콘", "표시", "창", "팝업", "슬롯", "영역", "목록", "텍스트",
    "화면", "요소", "항목", "섹션", "태그", "레이아웃", "배경", "이미지", "뷰", "및",
    "button", "buttons", "icon", "icons", "label", "view", "panel", "slot", "list",
    "area", "section", "tag", "popup", "layout", "background", "image", "element",
    "elements", "ui", "box", "text", "screen", "and", "of", "the", "a",
})

# LLM이 같은 대상을 영어와 한국어로 번갈아 부릅니다("Coins" / "코인"). 대표 표기로
# 모아 두면 같은 요소로 셉니다. 뜻이 갈릴 수 있는 낱말(골드/코인 등)은 게임마다
# 다를 수 있어 일부러 넣지 않았습니다.
NAME_ALIASES = {
    "coin": "코인", "coins": "코인", "currency": "화폐",
    "character": "캐릭터", "player": "플레이어",
    "board": "보드", "game": "게임", "level": "레벨", "map": "맵",
    "booster": "부스터", "boosters": "부스터",
    "shop": "상점", "store": "상점",
    "reward": "보상", "rewards": "보상", "bonus": "보너스", "time": "시간",
    "progress": "진행", "obstacle": "장애물", "mission": "임무", "score": "점수",
    "price": "가격", "product": "상품", "products": "상품",
    "friend": "친구", "friends": "친구",
    "home": "홈", "back": "뒤로", "close": "닫기", "next": "다음",
    "play": "플레이", "buy": "구매", "purchase": "구매",
    "profile": "프로필", "settings": "설정", "setting": "설정",
    "star": "별", "stars": "별", "tutorial": "튜토리얼",
}
# 탐색이 목적이라 게임 구조를 더 보여주는 버튼을 먼저 누릅니다.
# 진행 버튼이 그다음이고, 화면을 되돌리는 버튼이 마지막입니다.
BUTTON_ROLE_PRIORITY = {
    "structure": 0,
    "progression": 1,
    "safe_navigation": 2,
}


def normalize_text(value: str) -> str:
    """대소문자, 공백, 흔한 기호 차이 때문에 중복이 늘지 않게 정규화합니다."""

    lowered = value.casefold().strip()
    compact = re.sub(r"[^0-9a-z가-힣]+", " ", lowered)
    return re.sub(r"\s+", " ", compact).strip()


def stable_hash(parts: list[str]) -> str:
    """문자열 조각들을 합쳐 SHA1 해시를 반환합니다."""
    payload = "|".join(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def canonical_name_tokens(value: str) -> tuple[str, ...]:
    """이름을 대표 낱말 목록으로 바꿉니다(나온 순서 유지, 중복 제거).

    표현용 낱말만으로 된 이름("UI 요소")은 전부 떼면 빈 이름이 되어 서로 다른
    요소가 한 덩어리가 되므로, 그럴 때만 원래 낱말을 그대로 둡니다."""

    normalized = normalize_text(value)
    if not normalized:
        return ()
    tokens = list(
        dict.fromkeys(NAME_ALIASES.get(token, token) for token in normalized.split(" "))
    )
    kept = [token for token in tokens if token not in GENERIC_NAME_TOKENS]
    return tuple(kept) if kept else tuple(tokens)


def canonical_name(value: str) -> str:
    """표기 차이를 지운 대표 이름입니다. 같은 요소면 같은 문자열이 나옵니다."""
    return " ".join(canonical_name_tokens(value))


def fold_contained_names(
    token_groups: Iterable[tuple[str, ...]],
) -> dict[tuple[str, ...], tuple[str, ...]]:
    """자세한 이름을 더 짧은 이름으로 흡수시키는 대응표를 만듭니다.

    "부스터 선택"의 낱말이 "부스터"를 전부 포함하므로 같은 요소로 봅니다. 여러
    이름에 포함될 때는 가장 짧은 쪽(같으면 사전순 앞)으로 보내 결과가 실행마다
    달라지지 않게 합니다.

    보고서 표시에만 쓰고 저장 키(element_key)에는 쓰지 않습니다. 이 병합은 그때
    모인 이름 전체를 봐야 정해지므로, 기록이 하나 늘 때마다 예전 기록의 키까지
    바뀌어 버리기 때문입니다."""

    groups = {tokens: frozenset(tokens) for tokens in token_groups}
    absorbed_by: dict[tuple[str, ...], tuple[str, ...]] = {}
    for tokens, token_set in groups.items():
        contained = [
            other
            for other, other_set in groups.items()
            if other_set < token_set
        ]
        if contained:
            absorbed_by[tokens] = min(contained, key=lambda item: (len(item), item))

    resolved: dict[tuple[str, ...], tuple[str, ...]] = {}
    for tokens in groups:
        # 흡수 대상은 항상 더 짧은 이름이라 순환하지 않지만, 방어적으로 막습니다.
        current = tokens
        seen = {current}
        while current in absorbed_by and absorbed_by[current] not in seen:
            current = absorbed_by[current]
            seen.add(current)
        resolved[tokens] = current
    return resolved


def element_key(category: str, name: str) -> str:
    return stable_hash(["element", normalize_text(category), canonical_name(name)])


def button_key(role: str, label: str) -> str:
    return stable_hash(["button", normalize_text(role), normalize_text(label)])


def _get_value(source: Any, name: str, default: Any = None) -> Any:
    """dict와 객체 속성 모두에서 값을 꺼냅니다."""
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
    """조사 결정에 담긴 게임 요소 키 목록을 중복 없이 수집합니다."""
    keys: list[str] = []
    for element in _get_value(decision, "game_elements", []) or []:
        category = str(_get_value(element, "category", ""))
        name = str(_get_value(element, "name", ""))
        key = element_key(category, name)
        if key not in keys:
            keys.append(key)
    return keys


def collect_button_keys(decision: Any) -> list[str]:
    """조사 결정에 담긴 버튼 키 목록을 중복 없이 수집합니다."""
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
) -> list[Any]:
    """누르기 안전한 버튼만 남기고 역할 우선순위와 확신도로 정렬합니다."""

    screen_type = str(_get_value(decision, "screen_type", ""))
    buttons = []
    for button in _get_value(decision, "button_candidates", []) or []:
        role = str(_get_value(button, "role", "unknown"))
        label = str(_get_value(button, "label", ""))
        normalized_label = normalize_text(label)
        confidence = float(_get_value(button, "confidence", 0.0) or 0.0)
        center = _get_value(button, "center", None)
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
