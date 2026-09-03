"""게임 구조 조사 분석기."""

from __future__ import annotations

import json

import numpy as np
from openai import OpenAI

from capture_modes import CaptureBundle
from config import (
    AUTO_GRID,
    CAPTURE_MODE,
    COLS,
    GRID_MAX_COLS,
    GRID_MAX_ROWS,
    GRID_MIN_COLS,
    GRID_MIN_CONFIDENCE,
    GRID_MIN_ROWS,
    ROWS,
    SURVEY_DEDUP_SCAN_LIMIT,
    SURVEY_LLM_MAX_OUTPUT_TOKENS,
    SURVEY_MODEL,
)
from grid_detector import detect_grid_shape
from image_utils import board_fingerprint
from llm_request import request_structured_vision
from schemas import SurveyDecision
from storage import (
    load_all_survey_records,
    load_recent_survey_records,
    save_survey_record,
)
from survey_report import write_survey_report


SYSTEM_PROMPT = (
    "You are a game survey agent. Record observable game "
    "structure and never approve purchases, ads, login, or "
    "permission flows as safe actions."
)
BOARD_NOTE = (
    "아래 이미지는 보드 확대본이다. 보드 구조, 목표, 장애물, 부스터를 "
    "확인할 때만 참고한다."
)


def _format_survey_context(records: list[dict]) -> str:
    """최근 조사 기록을 LLM 프롬프트용 요약 문자열로 변환합니다."""
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

    context_text = _format_survey_context(survey_context or [])

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

응답 간결성:
- visible_text는 화면에 실제로 보이는 핵심 문구만 최대 8개.
- game_elements는 문서에 남길 핵심 요소만 최대 10개.
- button_candidates는 탐색 가치가 있는 후보만 최대 6개.
- summary, description, reason은 짧게 작성한다.

최근 조사 기록:
{context_text}
"""

    return request_structured_vision(
        client,
        model=SURVEY_MODEL,
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        full_image=full_image,
        board_image=board_image,
        board_note=BOARD_NOTE,
        text_format=SurveyDecision,
        max_output_tokens=SURVEY_LLM_MAX_OUTPUT_TOKENS,
        timing_label="SURVEY TIMING",
        missing_response_message="구조화된 조사 응답을 받지 못했습니다.",
    )


def record_survey_screen(
    bundle,
    decision: SurveyDecision,
    *,
    session_id: str,
    step: int,
    mode: str,
    action: dict | None = None,
    recent_records: list[dict] | None = None,
) -> dict:
    """화면 조사 결과를 기록 한 건으로 남깁니다 (보고서는 별도 요청 시 생성)."""

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
        model=SURVEY_MODEL,
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
    return record


def print_survey_decision(decision) -> None:
    """조사 판단 결과 출력."""

    print("\n[SURVEY 응답]")
    print(
        json.dumps(
            decision.model_dump(),
            ensure_ascii=False,
            indent=2,
        )
    )


def process_survey_frame(
    client: OpenAI,
    bundle: CaptureBundle,
    *,
    session_id: str,
    step: int,
    mode: str,
) -> str:
    """현재 화면의 조사 기록 저장."""

    recent_records = load_recent_survey_records(SURVEY_DEDUP_SCAN_LIMIT)
    print("\n[SURVEY REQUEST] 전체 게임 화면을 구조 조사 모드로 분석합니다.")
    decision = analyze_survey_screen(
        client,
        bundle.full_image,
        None,
        survey_context=recent_records,
    )
    print_survey_decision(decision)

    record = record_survey_screen(
        bundle,
        decision,
        session_id=session_id,
        step=step,
        mode=mode,
        recent_records=recent_records,
    )
    report_path = write_survey_report(load_all_survey_records())
    print(f"[SURVEY REPORT] {report_path}")
    if record["dedupe"]["is_duplicate_screen"]:
        print("[SURVEY DEDUPE] 이미 기록한 화면과 유사합니다.")
    new_count = len(record["dedupe"]["new_element_keys"])
    duplicate_count = len(record["dedupe"]["duplicate_element_keys"])
    print(
        "[SURVEY DEDUPE] "
        f"new_document_elements={new_count}, "
        f"duplicate_document_elements={duplicate_count}"
    )
    return "record_only"
