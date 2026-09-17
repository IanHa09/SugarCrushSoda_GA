"""LLM이 반환해야 할 JSON 구조를 Pydantic 모델로 정의합니다."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 모델이 프롬프트의 개수/길이 제한을 넘겨도 스키마로 강제하면 ValidationError로 응답 전체가 버려지므로, 실패 대신 아래 헬퍼로 조용히 잘라냅니다.


def _truncate_text(value: str, limit: int) -> str:
    """문자열이 limit보다 길면 잘라냅니다."""

    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _truncate_list(value: list, limit: int) -> list:
    """리스트 항목이 limit보다 많으면 앞에서부터 limit개만 남깁니다."""

    return value[:limit]


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

    @field_validator("reason", mode="after")
    @classmethod
    def _limit_reason(cls, value: str) -> str:
        return _truncate_text(value, 160)


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

    # 리스트 상한(3/2개)과 reason 길이(200자)는 프롬프트의 "응답 간결성" 지시와 같은 값입니다.
    @field_validator(
        "visible_elements",
        "objectives",
        "collectible_elements",
        "obstacles",
        "effects",
        "uncertainties",
        "memory_notes",
        mode="after",
    )
    @classmethod
    def _limit_note_lists(cls, value: list[str]) -> list[str]:
        return _truncate_list(value, 3)

    @field_validator("action_candidates", mode="after")
    @classmethod
    def _limit_action_candidates(
        cls, value: list[ActionCandidate]
    ) -> list[ActionCandidate]:
        return _truncate_list(value, 2)

    @field_validator("reason", mode="after")
    @classmethod
    def _limit_reason(cls, value: str) -> str:
        return _truncate_text(value, 200)


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

    @field_validator("label", mode="after")
    @classmethod
    def _limit_label(cls, value: str) -> str:
        return _truncate_text(value, 40)

    @field_validator("reason", mode="after")
    @classmethod
    def _limit_reason(cls, value: str) -> str:
        return _truncate_text(value, 140)


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
    # description과 evidence_text가 내용이 겹쳐 evidence 하나로 합쳐 토큰을 줄였습니다.
    evidence: str = ""

    @field_validator("name", mode="after")
    @classmethod
    def _limit_name(cls, value: str) -> str:
        return _truncate_text(value, 60)

    @field_validator("evidence", mode="after")
    @classmethod
    def _limit_evidence(cls, value: str) -> str:
        return _truncate_text(value, 200)


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
    evidence_notes: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    # 상한(8/10/6/5)은 프롬프트의 "응답 간결성" 지시와 같은 값입니다.
    @field_validator("summary", mode="after")
    @classmethod
    def _limit_summary(cls, value: str) -> str:
        return _truncate_text(value, 280)

    @field_validator("visible_text", mode="after")
    @classmethod
    def _limit_visible_text(cls, value: list[str]) -> list[str]:
        return _truncate_list(value, 8)

    @field_validator("game_elements", mode="after")
    @classmethod
    def _limit_game_elements(cls, value: list[SurveyElement]) -> list[SurveyElement]:
        return _truncate_list(value, 10)

    @field_validator("button_candidates", mode="after")
    @classmethod
    def _limit_button_candidates(
        cls, value: list[SurveyButtonCandidate]
    ) -> list[SurveyButtonCandidate]:
        return _truncate_list(value, 6)

    @field_validator("evidence_notes", "uncertainties", mode="after")
    @classmethod
    def _limit_note_lists(cls, value: list[str]) -> list[str]:
        return _truncate_list(value, 5)


class ScreenNode(BaseModel):
    """Autodrive가 관찰한 화면 하나를 나타내는 그래프 노드입니다."""

    id: str
    screen_type: str
    summary: str = ""
    representative: str = ""
    visits: int = 0
    # 처음 관찰했을 때의 정규화 버튼 목록. 버튼 1개 차이 허용 매칭의 기준(고정)입니다.
    buttons: list[str] = Field(default_factory=list)


class NavigationEdge(BaseModel):
    """두 화면 사이의 실제 전환 한 건입니다."""

    source: str
    target: str
    action: str = ""
    action_key: str = ""


class NavigationGraph(BaseModel):
    """Autodrive가 쌓아 온 화면 전환 그래프 전체입니다(survey_report.py와 navigation 양쪽에서 사용)."""

    nodes: dict[str, ScreenNode] = Field(default_factory=dict)
    edges: list[NavigationEdge] = Field(default_factory=list)


# "간선이 있으면 탐색됨"(explored_actions) 대신, (화면,행동) 조합 하나를 "눌러봤는지"와
# "눌렀더니 무슨 일이 있었는지"로 따로 기록하기 위한 원장입니다. verdict 값:
# new_screen(새 화면으로 이동) / known_screen(기존 다른 화면으로 이동) /
# no_change(눌러도 화면 그대로) / blocked(도달 불가로 포기) / failed(탭 자체가 실행 안 됨) /
# not_visible(그 화면을 다시 봐도 후보에 연속으로 안 나와 정리됨).
# blocked와 not_visible은 탭 없이 프론티어에서 빠진 항목이라 attempts는 0 그대로입니다.
class LedgerEntry(BaseModel):
    """(screen_id, action_key) 조합 하나의 시도 이력입니다.
    attempts == 0이고 탭 없이 정리되지도 않았으면 프론티어(미시도)입니다."""

    screen_id: str
    action_key: str
    action_label: str = ""
    attempts: int = 0
    # pydantic은 from __future__ import annotations가 있어도 필드 힌트를 다시 평가하므로
    # Python 3.9에서 동작하려면 `X | None`이 아니라 Optional[...]을 써야 합니다.
    verdict: Optional[
        Literal[
            "new_screen", "known_screen", "no_change", "blocked", "failed", "not_visible"
        ]
    ] = None
    target_screen_id: Optional[str] = None
    last_seen: str = ""
    # 미시도 상태에서 이 화면을 관찰했는데 후보에 안 보인 연속 횟수.
    misses: int = 0


class ActionLedger(BaseModel):
    """모든 (화면, 행동) 시도 기록. 키는 f"{screen_id}:{action_key}" 입니다."""

    entries: dict[str, LedgerEntry] = Field(default_factory=dict)
