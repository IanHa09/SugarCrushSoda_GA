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
