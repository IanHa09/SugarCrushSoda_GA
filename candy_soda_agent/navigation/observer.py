"""LLM 표현 차이에 강한 탐색 화면 ID 생성."""

import hashlib

from config import SURVEY_MIN_BUTTON_CONFIDENCE
from navigation.policy import canonical_action_label
from survey_utils import SAFE_BUTTON_ROLES


# 병렬 섹션용 단일 노드 화면
SINGLETON_SCREEN_TYPES = {
    "map_or_level_select",
    "settings_menu",
    "shop_or_currency",
    "booster_panel",
}


# 화면 타입 하나로 고정 ID를 만듭니다.
def canonical_screen_id(screen_type: str) -> str:
    digest = hashlib.sha1(screen_type.encode()).hexdigest()[:12]
    return f"screen_{digest}"


def screen_buttons(decision) -> list[str]:
    """화면 식별에 쓰는 버튼 목록(정렬된 정규화 라벨).

    - "Close (X)"와 "Close (x icon)"처럼 표기만 다른 버튼은 행동 키와 같은 규칙
      (canonical_action_label)으로 같은 라벨이 됩니다.
    - 확신도가 기준 미만인 버튼은 관찰마다 보였다 안 보였다 해서 뺍니다.
    """

    labels = {
        canonical_action_label(button.label)
        for button in decision.button_candidates
        if button.role in SAFE_BUTTON_ROLES
        and button.label
        and button.confidence >= SURVEY_MIN_BUTTON_CONFIDENCE
    }
    labels.discard("")
    return sorted(labels)


def screen_id(decision) -> str:
    """화면 타입 + 정규화 버튼 목록으로 정확 일치용 ID를 만듭니다.

    visible_text는 LLM이 고르는 문구와 순서가 매번 달라 화면이 쪼개지는 주원인이라
    뺐습니다. 버튼이 하나 빠진 관찰까지 같은 화면으로 묶는 허용 매칭은
    GraphStore.observe()가 이 ID로 못 찾았을 때 추가로 합니다.
    """

    if decision.screen_type in SINGLETON_SCREEN_TYPES:
        return canonical_screen_id(decision.screen_type)

    payload = "|".join([decision.screen_type, *screen_buttons(decision)])
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"screen_{digest}"
