"""OpenAI 구조화 출력에 사용하는 Pydantic 스키마."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MoveType = Literal[
    "normal_match",
    "create_special",
    "activate_special",
    "combine_specials",
    "clear_objective",
    "unknown",
]


class Cell(BaseModel):
    """퍼즐 보드의 한 칸을 1부터 시작하는 행·열로 표현."""

    row: int = Field(ge=1)
    col: int = Field(ge=1)


class AgentDecision(BaseModel):
    """현재 보드에서 선택한 다음 행동 후보."""

    board_status: Literal[
        "stable",
        "animation",
        "popup",
        "unclear",
    ]
    board_summary: str
    move_type: MoveType
    visible_elements: list[str]
    action: Literal["swap", "wait"]
    source: Cell | None = None
    target: Cell | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class VideoTransitionLabel(BaseModel):
    """영상의 전·중·후 프레임에서 추출한 실제 행동."""

    action_observable: bool
    actual_action: Literal["swap", "unknown"]
    source: Cell | None = None
    target: Cell | None = None
    move_type: MoveType
    board_summary: str
    score_before: int | None = Field(
        default=None,
        ge=0,
    )
    score_after: int | None = Field(
        default=None,
        ge=0,
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class VideoOutcomeLabel(BaseModel):
    """로컬에서 확정한 영상 행동의 점수와 결과 분석."""

    move_type: MoveType
    board_summary: str
    score_before: int | None = Field(
        default=None,
        ge=0,
    )
    score_after: int | None = Field(
        default=None,
        ge=0,
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
