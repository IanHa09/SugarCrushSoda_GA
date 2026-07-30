"""LLM이 반환해야 할 JSON 구조를 Pydantic 모델로 정의합니다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Cell(BaseModel):
    """퍼즐 보드의 한 칸을 행과 열로 표현합니다."""

    row: int = Field(ge=1)
    col: int = Field(ge=1)


class AgentDecision(BaseModel):
    """한 번의 화면 분석 결과입니다."""

    board_status: Literal["stable", "animation", "popup", "unclear"]

    # 화면에서 확인한 사탕, 장애물, 목표 요소 등을 기록합니다.
    visible_elements: list[str]

    # 현재 단계에서는 실제 클릭 대신 swap 후보 또는 대기를 출력합니다.
    action: Literal["swap", "wait"]

    source: Cell | None = None
    target: Cell | None = None

    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
