"""게임 구조 조사 분석기."""

from __future__ import annotations

import json
import time

import numpy as np
from openai import OpenAI

from agent import model_supports_reasoning
from config import (
    AUTO_GRID,
    BOARD_IMAGE_DETAIL,
    CAPTURE_MODE,
    COLS,
    FULL_IMAGE_DETAIL,
    GRID_MAX_COLS,
    GRID_MAX_ROWS,
    GRID_MIN_COLS,
    GRID_MIN_CONFIDENCE,
    GRID_MIN_ROWS,
    MODEL,
    ROWS,
    SURVEY_DEDUP_SCAN_LIMIT,
    SURVEY_LLM_MAX_OUTPUT_TOKENS,
)
from grid_detector import detect_grid_shape
from image_utils import board_fingerprint, image_to_data_url
from schemas import SurveyDecision
from storage import load_recent_survey_records, save_survey_record
from survey_report import write_survey_report
from survey_utils import RATING_SIGNAL_FIELDS


def _format_survey_context(records: list[dict]) -> str:
    summaries = []
    for record in records[-8:]:
        survey = record.get("survey", {})
        dedupe = record.get("dedupe", {})
        summaries.append({
            "screen_type": survey.get("screen_type"),
            "summary": survey.get("summary"),
            "new_element_count": len(dedupe.get("new_element_keys", [])),
            "duplicate_screen": dedupe.get("is_duplicate_screen", False),
        })
    if not summaries:
        return "아직 참고할 조사 기록이 없다."
    return json.dumps(summaries, ensure_ascii=False)


def analyze_survey_screen(
    client: OpenAI,
    full_image: np.ndarray,
    board_image: np.ndarray | None = None,
    survey_context: list[dict] | None = None,
) -> SurveyDecision:
    """화면 요소, 버튼 후보, 문서화 증거 추출."""

    total_started = time.perf_counter()
    encode_started = time.perf_counter()
    full_image_data_url = image_to_data_url(full_image)
    board_image_data_url = (
        image_to_data_url(board_image)
        if board_image is not None
        else None
    )
    encode_elapsed = time.perf_counter() - encode_started
    context_text = _format_survey_context(survey_context or [])
    signal_text = ", ".join(RATING_SIGNAL_FIELDS)

    prompt = f"""
첫 번째 이미지는 게임 UI 전체 또는 게임 창이다. 두 번째 이미지가 있으면 같은 화면의 보드 확대본이다.

목표:
- 게임을 잘 플레이하는 것이 아니라, 이 게임이 어떤 구조와 요소를 가진 게임인지 문서화한다.
- 화면 캡처와 텍스트 설명을 함께 쓸 수 있도록 현재 화면의 증거를 정리한다.
- 룰북/보상 메모리와 별도로, 등급 검토자가 나중에 볼 수 있는 게임 구조 기록을 만든다.

화면 타입:
- playing_board: 실제 보드 플레이 화면
- level_start: 레벨 시작/목표 안내
- level_complete: 클리어, 별, Next 화면
- reward_popup: 보상 획득, Claim, 선물, 상자
- map_or_level_select: 스테이지 지도 또는 레벨 선택
- booster_panel: 부스터 선택, 잠금 부스터
- shop_or_currency: 코인, 상점, 재화
- settings_menu: 설정, 도움말, 개인정보, 계정 메뉴
- event_or_mission: 이벤트, 미션, 출석, 시즌 보상
- ad_or_offer: 광고 시청, 특가, 시간 제한 오퍼
- tutorial, loading, unknown_but_recordable

버튼 후보 정책:
- safe_navigation: 닫기, 뒤로, 홈처럼 화면 이동을 되돌리거나 정리하는 버튼
- progression: Next, Continue, Claim처럼 다음 화면을 보기 위한 진행 버튼
- structure: 메뉴, 설정, 보상, 상점처럼 게임 구조를 더 보여주는 버튼
- risky_monetization: 구매, 결제, 가격, 재화 구매, 패키지 구매
- risky_ad: 광고 시청, watch ad, rewarded ad
- risky_account: 로그인, 계정 연결, 개인정보, 권한 요청
- 결제/구매/광고/로그인/권한 관련 후보는 기록만 하고 안전 후보로 분류하지 않는다.
- center는 전체 첫 번째 이미지 기준의 상대 좌표 x,y를 0~1로 적는다. 모르면 center를 null로 둔다.

중복 방지:
- 최근 기록과 같은 화면/요소로 보이면 summary와 uncertainties에 그 사실을 적는다.
- 같은 요소를 여러 표현으로 반복하지 말고 대표 이름 하나로 정리한다.

등급 판정 신호:
- 아직 세부 가이드라인이 없으므로 다음 변수명을 유지하고, 확실히 보인 것이 아니면 unknown으로 둔다.
- 변수명: {signal_text}

응답 간결성:
- visible_text는 화면에 실제로 보이는 핵심 문구만 최대 8개.
- game_elements는 문서에 남길 핵심 요소만 최대 10개.
- button_candidates는 탐색 가치가 있는 후보만 최대 6개.
- summary, description, reason은 짧게 작성한다.

최근 조사 기록:
{context_text}
"""

    content = [
        {"type": "input_text", "text": prompt},
        {
            "type": "input_image",
            "image_url": full_image_data_url,
            "detail": FULL_IMAGE_DETAIL,
        },
    ]
    if board_image_data_url is not None:
        content.extend([
            {
                "type": "input_text",
                "text": "아래 이미지는 보드 확대본이다. 보드 구조, 목표, 장애물, 부스터를 확인할 때만 참고한다.",
            },
            {
                "type": "input_image",
                "image_url": board_image_data_url,
                "detail": BOARD_IMAGE_DETAIL,
            },
        ])

    request = {
        "model": MODEL,
        "max_output_tokens": SURVEY_LLM_MAX_OUTPUT_TOKENS,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a game survey agent. Record observable game "
                    "structure and never approve purchases, ads, login, or "
                    "permission flows as safe actions."
                ),
            },
            {"role": "user", "content": content},
        ],
        "text_format": SurveyDecision,
    }
    if model_supports_reasoning(MODEL):
        request["reasoning"] = {"effort": "minimal"}

    api_started = time.perf_counter()
    try:
        response = client.responses.parse(**request)
    finally:
        api_elapsed = time.perf_counter() - api_started
        total_elapsed = time.perf_counter() - total_started
        print(
            "[SURVEY TIMING] "
            f"image_encode={encode_elapsed:.2f}s, "
            f"api={api_elapsed:.2f}s, "
            f"total={total_elapsed:.2f}s"
        )
    if response.output_parsed is None:
        raise RuntimeError("구조화된 조사 응답을 받지 못했습니다.")
    return response.output_parsed


def record_survey_screen(
    bundle,
    decision: SurveyDecision,
    *,
    session_id: str,
    step: int,
    mode: str,
    action: dict | None = None,
    recent_records: list[dict] | None = None,
    graph=None,
):
    """화면 조사 기록과 통합 보고서 생성."""

    recent = recent_records
    if recent is None:
        recent = load_recent_survey_records(SURVEY_DEDUP_SCAN_LIMIT)

    shape = detect_grid_shape(
        bundle.board_image,
        ROWS,
        COLS,
        enabled=AUTO_GRID,
        min_rows=GRID_MIN_ROWS,
        max_rows=GRID_MAX_ROWS,
        min_cols=GRID_MIN_COLS,
        max_cols=GRID_MAX_COLS,
        min_confidence=GRID_MIN_CONFIDENCE,
    )
    record = save_survey_record(
        session_id=session_id,
        step=step,
        mode=mode,
        raw_image=bundle.full_image,
        grid_image=None,
        decision=decision,
        stored_image_scope=CAPTURE_MODE,
        screen_fingerprint=board_fingerprint(bundle.full_image),
        grid={
            "rows": shape.rows,
            "cols": shape.cols,
            "confidence": shape.confidence,
            "source": shape.source,
        },
        action=action,
        recent_records=recent,
    )
    report = write_survey_report([*recent, record], graph=graph)
    return record, report
