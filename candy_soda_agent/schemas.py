"""LLM이 반환해야 할 JSON 구조를 Pydantic 모델로 정의합니다."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from game_knowledge import FALLBACK_SCREEN_TYPE, MAX_BUTTON_LIMIT, screen_type_ids

# 조사 모델이 고를 수 있는 화면 타입. knowledge/screen_types.json에서 enabled인 id를
# 파일 순서대로 읽습니다(Literal[("a", "b")]는 Literal["a", "b"]와 같습니다).
SurveyScreenType = Literal[screen_type_ids()]  # type: ignore[valid-type]

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


class PixelBox(BaseModel):
    """첫 번째 이미지(전체 캡처) 기준 픽셀 좌표의 버튼 영역입니다(왼쪽 위 x1,y1 / 오른쪽 아래 x2,y2).

    0~1 상대 좌표로 받으면 모델이 0.1 단위로 뭉개서(실측 78%가 소수 한 자리) 픽셀로
    받습니다. 값 검사(x2>x1, 이미지 안쪽)는 survey.apply_bbox_centers가 합니다."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)


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
    # 모델은 bbox(픽셀)를 적고, center는 survey.apply_bbox_centers가 bbox 중심으로
    # 다시 계산합니다. 실행부(action_executor)는 계속 center(0~1)만 씁니다.
    bbox: Optional[PixelBox] = None
    center: Optional[NormalizedPoint] = None
    # 버튼이 놓인 영역. 네비게이션 바(공통 버튼) 판정의 보조 신호입니다(ui_chrome.py).
    region: Literal["bottom_nav", "top_bar", "popup", "content", "unknown"] = "unknown"
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

    # 허용 값은 knowledge/screen_types.json이 정합니다(위 SurveyScreenType).
    screen_type: SurveyScreenType = FALLBACK_SCREEN_TYPE  # type: ignore[valid-type]
    summary: str = ""
    visible_text: list[str] = Field(default_factory=list)
    game_elements: list[SurveyElement] = Field(default_factory=list)
    button_candidates: list[SurveyButtonCandidate] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    # 상한(8/10/5)은 프롬프트의 "응답 간결성" 지시와 같은 값입니다. 버튼 후보는 프롬프트가
    # 기본 6개를 요청하고, 게임 지식에 탭바 탭이 있으면 그만큼 더 허용해 최대 10개입니다
    # (game_knowledge.GameProfile.button_limit).
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
        return _truncate_list(value, MAX_BUTTON_LIMIT)

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
    # 처음 관찰했을 때의 화면 이미지 지문(dHash). 같은 화면인지 판단하는 1차 기준입니다.
    # LLM이 보고하는 버튼 목록은 같은 화면을 봐도 매번 달라져 화면이 쪼개지는데,
    # 이미지는 같은 화면이면 같게 나옵니다. 옛 기록에는 없으므로 기본값은 빈 문자열이고,
    # 그때는 지문 비교를 건너뜁니다.
    fingerprint: str = ""
    # 플레이 화면에서 나오는 도중에 처음 본 화면(나가기 확인창 등)이면 True. 이런 화면은
    # 버튼을 프론티어로 등록하지 않고, 다시 만나도 "플레이에서 빠져나왔다"고 보지 않습니다.
    play_flow: bool = False


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
# not_visible(그 화면을 다시 봐도 후보에 연속으로 안 나와 정리됨) /
# missed(눌렀는데 픽셀이 그대로라 빗나감 의심, 재시도 대기 중 = 아직 프론티어) /
# not_found(확대 재확인에서 그 버튼을 화면에서 찾지 못해 누르지 않고 정리됨) /
# global(네비게이션 바 같은 공통 버튼으로 판정돼 화면별 항목은 닫고 공통 항목으로 옮김) /
# play_entry(플레이 진입 버튼이라 기록만 하고 누르지 않음, PLAY_POLICY).
# blocked, not_visible, not_found, play_entry는 탭 없이 프론티어에서 빠진 항목이라 attempts는 0 그대로입니다.
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
            "new_screen",
            "known_screen",
            "no_change",
            "blocked",
            "failed",
            "not_visible",
            "missed",
            "not_found",
            "global",
            "play_entry",
        ]
    ] = None
    target_screen_id: Optional[str] = None
    last_seen: str = ""
    # 미시도 상태에서 이 화면을 관찰했는데 후보에 안 보인 연속 횟수.
    misses: int = 0
    # 실패로 닫혔다가 다시 프론티어로 열린 횟수(LedgerStore.reopen_failed, FRONTIER_RETRY_MAX까지).
    retries: int = 0


class ActionLedger(BaseModel):
    """모든 (화면, 행동) 시도 기록. 키는 f"{screen_id}:{action_key}" 입니다."""

    entries: dict[str, LedgerEntry] = Field(default_factory=dict)


class TapLocation(BaseModel):
    """누르기 직전 확대 재확인(tap_refine)에서 모델이 돌려주는 버튼 위치입니다.

    격자 칸(cell, 예: "C5")과 픽셀 좌표(x, y)를 둘 다 받습니다. 칸 이름은 가장자리
    글자를 읽으면 되어 좌표보다 덜 틀리고, 픽셀 좌표는 더 정밀합니다. 둘이 맞으면
    픽셀을, 어긋나면 칸 중심을 씁니다(tap_refine.resolve_location)."""

    found: bool = False
    cell: str = ""
    x: int = Field(default=-1, ge=-1)
    y: int = Field(default=-1, ge=-1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    seen_label: str = ""

    @field_validator("cell", mode="after")
    @classmethod
    def _limit_cell(cls, value: str) -> str:
        return _truncate_text(value.strip(), 8)

    @field_validator("seen_label", mode="after")
    @classmethod
    def _limit_seen_label(cls, value: str) -> str:
        return _truncate_text(value, 60)


class ExitLocation(BaseModel):
    """탈출 사다리의 "나가기 요소 찾기"에서 모델이 돌려주는 위치입니다.

    kind: button(닫기/X/뒤로/확인/홈 탭 같은 나가기 요소) | outside(모달 팝업 바깥의
    어두운 영역) | none(둘 다 없음). 좌표 규칙은 TapLocation과 같습니다(칸 + 픽셀)."""

    kind: Literal["button", "outside", "none"] = "none"
    cell: str = ""
    x: int = Field(default=-1, ge=-1)
    y: int = Field(default=-1, ge=-1)
    label: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("cell", mode="after")
    @classmethod
    def _limit_cell(cls, value: str) -> str:
        return _truncate_text(value.strip(), 8)

    @field_validator("label", mode="after")
    @classmethod
    def _limit_label(cls, value: str) -> str:
        return _truncate_text(value, 40)
