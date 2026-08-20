"""LLM이 반환해야 할 JSON 구조를 Pydantic 모델로 정의합니다."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Cell(BaseModel):
    """퍼즐 보드의 한 칸을 행과 열로 표현합니다."""

    row: int = Field(ge=1)
    col: int = Field(ge=1)


class ActionCandidate(BaseModel):
    """분석 결과에서 고려한 행동 후보입니다."""

    # 현재 실행 레이어는 셀 좌표가 있는 swap만 실제로 수행합니다.
    action: Literal["swap", "wait"]
    source: Optional[Cell] = None
    target: Optional[Cell] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class AgentDecision(BaseModel):
    """한 번의 화면 분석 결과입니다."""

    board_status: Literal["stable", "animation", "popup", "unclear"]
    detected_ui_state: Literal[
        "playing", "tutorial", "popup", "level_start",
        "level_complete", "out_of_moves", "unknown"
    ]
    # 화면에서 확인한 사탕, 장애물, 목표 요소 등을 기록합니다.
    visible_elements: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    collectible_elements: list[str] = Field(default_factory=list)
    obstacles: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    memory_notes: list[str] = Field(default_factory=list)

    action_candidates: list[ActionCandidate] = Field(default_factory=list)

    # 기존 main.py와의 호환성을 위한 대표 행동
    action: Literal["swap", "wait"] = "wait"

    source: Optional[Cell] = None
    target: Optional[Cell] = None

    # ge : greater than or equal to, le : less than or equal to (이상, 이하)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class NormalizedPoint(BaseModel):
    """캡처된 전체 화면 안에서 0~1 범위로 표현한 클릭 좌표입니다."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class SurveyButtonCandidate(BaseModel):
    """조사 모드에서 발견한 UI 버튼 또는 탭 후보입니다."""

    label: str = ""
    role: Literal[
        "safe_navigation",
        "progression",
        "structure",
        "risky_monetization",
        "risky_ad",
        "risky_account",
        "unknown",
    ] = "unknown"
    center: Optional[NormalizedPoint] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class SurveyElement(BaseModel):
    """등급 문서에 남길 게임 요소 한 건입니다."""

    category: Literal[
        "core_play",
        "objective",
        "progression",
        "reward",
        "currency",
        "booster",
        "obstacle",
        "character",
        "ui",
        "monetization",
        "ad",
        "social",
        "account",
        "other",
    ]
    name: str
    description: str = ""
    evidence_text: str = ""


class RatingSignalVariables(BaseModel):
    """가이드라인 수령 전까지 변수명만 고정해 두는 등급 판정 신호입니다."""

    monetization: str = "unknown"
    ads: str = "unknown"
    loot_or_random_reward: str = "unknown"
    violence: str = "unknown"
    fear_or_horror: str = "unknown"
    sexuality_or_nudity: str = "unknown"
    profanity: str = "unknown"
    alcohol_tobacco_drugs: str = "unknown"
    gambling: str = "unknown"
    user_generated_content: str = "unknown"
    social_or_chat: str = "unknown"
    personal_data_or_account: str = "unknown"
    location_or_device_permissions: str = "unknown"
    time_pressure_or_retention: str = "unknown"


class SurveyDecision(BaseModel):
    """게임 구조 조사 모드의 한 번의 화면 분석 결과입니다."""

    screen_type: Literal[
        "playing_board",
        "level_start",
        "level_complete",
        "reward_popup",
        "map_or_level_select",
        "booster_panel",
        "shop_or_currency",
        "settings_menu",
        "event_or_mission",
        "ad_or_offer",
        "tutorial",
        "loading",
        "unknown_but_recordable",
    ] = "unknown_but_recordable"
    summary: str = ""
    visible_text: list[str] = Field(default_factory=list)
    game_elements: list[SurveyElement] = Field(default_factory=list)
    button_candidates: list[SurveyButtonCandidate] = Field(default_factory=list)
    rating_signals: RatingSignalVariables = Field(
        default_factory=RatingSignalVariables
    )
    evidence_notes: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
