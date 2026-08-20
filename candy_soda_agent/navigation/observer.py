# 동적인 점수, 숫자 제거 + 화면 종류 문구 버튼으로 화면 ID 생성
import hashlib
import re

from survey_utils import SAFE_BUTTON_ROLES, normalize_text

def _stable_text(value: str) -> str:
    return re.sub(r"\d+", "#", normalize_text(value))

def screen_id(decision) -> str:
    texts = [_stable_text(value) for value in decision.visible_text[:4]]

    buttons = sorted(
        _stable_text(button.label)
        for button in decision.button_candidates
        if button.role in SAFE_BUTTON_ROLES and button.label
    )

    payload = "|".join([decision.screen_type, *texts, *buttons])
    digest = hashlib.sha1(payload.encode()).hexdigest()[:12]
    return f"screen_{digest}"
