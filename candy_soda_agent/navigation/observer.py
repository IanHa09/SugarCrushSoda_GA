"""LLM 표현 차이에 강한 탐색 화면 ID 생성."""

import hashlib

from config import SURVEY_MIN_BUTTON_CONFIDENCE
from navigation.policy import canonical_action_label
from survey_utils import SAFE_BUTTON_ROLES


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
    뺐습니다. 버튼 목록도 같은 이유로 믿을 수 없어서(같은 플레이 화면인데 관찰마다
    3~6개씩 다른 부분집합을 보고합니다) 이 ID는 "정확히 같은 관찰"을 빠르게 찾는
    용도일 뿐입니다. 같은 화면인지는 GraphStore.observe()가 화면 이미지 지문으로
    판단하고, 지문이 없을 때만 버튼 허용 매칭으로 넘어갑니다.

    전에는 상점·설정처럼 하나뿐이라고 본 화면 타입을 타입 이름만으로 한 노드에
    묶었는데(SINGLETON_SCREEN_TYPES), 실측해 보니 상점은 실제로 서로 다른 화면이
    여러 개여서 과병합이었습니다. 지문 판정이 그 역할을 대신합니다.
    """

    payload = "|".join([decision.screen_type, *screen_buttons(decision)])
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"screen_{digest}"
