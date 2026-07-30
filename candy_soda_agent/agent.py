"""OpenAI 비전 모델 호출과 행동 검증."""

from __future__ import annotations

from typing import Any

import numpy as np
from openai import OpenAI

from config import (
    COLS,
    IMAGE_DETAIL,
    MAX_OUTPUT_TOKENS,
    MAX_VIDEO_LABEL_OUTPUT_TOKENS,
    MIN_CONFIDENCE,
    MODEL,
    ROWS,
    VIDEO_IMAGE_DETAIL,
)
from image_utils import image_to_data_url
from schemas import AgentDecision, VideoTransitionLabel
from usage import ApiUsage


def model_supports_reasoning(model: str) -> bool:
    """Responses API의 reasoning 설정을 지원하는 모델인지 확인."""

    normalized = model.lower()
    return normalized.startswith(
        (
            "gpt-5",
            "o1",
            "o3",
            "o4",
        )
    )


def _response_arguments() -> dict[str, Any]:
    """모델 계열에 맞는 공통 Responses API 인자."""

    arguments: dict[str, Any] = {
        "model": MODEL,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }

    # gpt-4o-mini 같은 비추론 모델에는 reasoning을 보내면
    # unsupported parameter 오류가 발생합니다.
    if model_supports_reasoning(MODEL):
        arguments["reasoning"] = {
            "effort": "minimal",
        }

    return arguments


def analyze_board(
    client: OpenAI,
    grid_image: np.ndarray,
    memory_prompt: str = "",
) -> tuple[AgentDecision, ApiUsage]:
    """현재 보드에서 다음 행동 후보와 API 사용량을 반환."""

    image_data_url = image_to_data_url(grid_image)

    memory_section = ""
    if memory_prompt.strip():
        memory_section = f"""

    과거 영상 경험 요약:
    {memory_prompt.strip()}

    과거 경험은 참고 자료일 뿐이다. 현재 화면과 충돌하면
    반드시 현재 화면을 우선한다.
    """

    prompt = f"""
이미지는 Candy Crush Soda 퍼즐 보드이며 빨간 격자와
1부터 시작하는 좌표가 표시되어 있다.

좌표 규칙:
- 행은 위에서 아래로 1부터 {ROWS}까지이다.
- 열은 왼쪽에서 오른쪽으로 1부터 {COLS}까지이다.
- source와 target은 반드시 상하좌우로 붙은 두 칸이다.
- 대각선 교환은 허용하지 않는다.

해야 할 일:
1. 화면 상태를 board_status로 분류한다.
2. 보드의 핵심 상태를 board_summary 한 문장으로 요약한다.
3. 확실히 보이는 사탕·특수 사탕·장애물·목표만
   visible_elements에 최대 6개 기록한다.
4. 안정된 화면이면 매치 가능성이 높은 swap 하나를 고른다.
5. 선택한 행동을 move_type으로 분류한다.
6. 애니메이션, 팝업, 잘린 화면 또는 불확실한 보드라면
   action=wait로 두고 source와 target은 null로 둔다.
7. 보이지 않는 요소는 추측하지 않는다.
8. reason은 한 문장으로 작성한다.

move_type:
- normal_match: 일반적인 3개 이상 매치
- create_special: 특수 사탕 생성
- activate_special: 특수 사탕 작동
- combine_specials: 특수 사탕끼리 결합
- clear_objective: 목표 장애물 제거 중심
- unknown: 판단 불가

게임을 직접 조작하지 말고 행동 후보만 반환한다.
{memory_section}
""".strip()

    input_payload: Any = [
        {
            "role": "system",
            "content": (
                "You are a conservative visual "
                "puzzle-game agent. Use only information "
                "clearly visible in the image."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": prompt,
                },
                {
                    "type": "input_image",
                    "image_url": image_data_url,
                    "detail": IMAGE_DETAIL,
                },
            ],
        },
    ]

    response = client.responses.parse(
        **_response_arguments(),
        input=input_payload,
        text_format=AgentDecision,
    )

    decision = response.output_parsed
    if decision is None:
        raise RuntimeError(
            "구조화된 보드 분석 응답을 받지 못했습니다."
        )

    return decision, ApiUsage.from_response(response)


def validate_decision(
    decision: AgentDecision,
) -> tuple[bool, str]:
    """모델의 행동 후보가 게임 좌표 규칙에 맞는지 검증."""

    if decision.action == "wait":
        if (
            decision.source is not None
            or decision.target is not None
        ):
            return (
                False,
                "wait 행동에는 source와 target이 없어야 합니다.",
            )
        return True, "wait 행동"

    if decision.board_status != "stable":
        return (
            False,
            "안정된 보드가 아닌데 swap을 선택했습니다.",
        )

    if decision.source is None or decision.target is None:
        return (
            False,
            "swap인데 source 또는 target 좌표가 없습니다.",
        )

    valid, message = _validate_swap_cells(
        decision.source,
        decision.target,
    )
    if not valid:
        return valid, message

    if decision.confidence < MIN_CONFIDENCE:
        return (
            False,
            f"confidence={decision.confidence:.2f}가 "
            f"기준 {MIN_CONFIDENCE:.2f}보다 낮습니다.",
        )

    return True, "기본 규칙 검증을 통과한 swap"


def label_video_transition(
    client: OpenAI,
    before_image: np.ndarray,
    action_image: np.ndarray,
    after_image: np.ndarray,
) -> tuple[VideoTransitionLabel, ApiUsage]:
    """영상의 전·중·후 화면에서 실제 플레이 행동을 추출."""

    before_url = image_to_data_url(before_image)
    action_url = image_to_data_url(action_image)
    after_url = image_to_data_url(after_image)

    prompt = f"""
세 이미지는 Candy Crush Soda 영상에서 시간 순서대로
추출한 관측 화면이다. 각 이미지 위쪽에는 현재 점수 패널,
아래쪽에는 좌표 격자가 표시된 보드가 있다.

IMAGE 1: 행동 직전의 안정된 보드
IMAGE 2: swap 시작 또는 사탕이 움직이는 순간
IMAGE 3: 연쇄 반응이 끝난 뒤의 안정된 보드

좌표 규칙:
- 행은 위에서 아래로 1부터 {ROWS}까지이다.
- 열은 왼쪽에서 오른쪽으로 1부터 {COLS}까지이다.
- 실제 swap은 상하좌우로 인접한 두 칸이다.

해야 할 일:
1. IMAGE 1과 IMAGE 2를 중심으로 최초로 바뀐 두 칸을 찾는다.
2. IMAGE 3의 연쇄 낙하만 보고 최초 행동을 추측하지 않는다.
3. 행동을 확인할 수 없으면 action_observable=false,
   actual_action=unknown, source와 target=null로 둔다.
4. 각 점수 패널에서 행동 전후 누적 점수를 읽는다.
5. 행동 유형을 move_type으로 분류한다.
6. board_summary와 reason은 각각 한 문장으로 작성한다.

move_type:
- normal_match: 일반적인 3개 이상 매치
- create_special: 특수 사탕 생성
- activate_special: 특수 사탕 작동
- combine_specials: 특수 사탕끼리 결합
- clear_objective: 목표 장애물 제거 중심
- unknown: 판단 불가
""".strip()

    arguments = _response_arguments()
    arguments["max_output_tokens"] = (
        MAX_VIDEO_LABEL_OUTPUT_TOKENS
    )

    input_payload: Any = [
        {
            "role": "system",
            "content": (
                "You label puzzle-game actions from "
                "ordered frames. Reject ambiguous "
                "transitions."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": prompt,
                },
                {
                    "type": "input_text",
                    "text": "IMAGE 1: BEFORE",
                },
                {
                    "type": "input_image",
                    "image_url": before_url,
                    "detail": VIDEO_IMAGE_DETAIL,
                },
                {
                    "type": "input_text",
                    "text": "IMAGE 2: ACTION START",
                },
                {
                    "type": "input_image",
                    "image_url": action_url,
                    "detail": VIDEO_IMAGE_DETAIL,
                },
                {
                    "type": "input_text",
                    "text": "IMAGE 3: AFTER",
                },
                {
                    "type": "input_image",
                    "image_url": after_url,
                    "detail": VIDEO_IMAGE_DETAIL,
                },
            ],
        },
    ]

    response = client.responses.parse(
        **arguments,
        input=input_payload,
        text_format=VideoTransitionLabel,
    )

    label = response.output_parsed
    if label is None:
        raise RuntimeError(
            "구조화된 영상 행동 라벨을 받지 못했습니다."
        )

    return label, ApiUsage.from_response(response)


def validate_transition_label(
    label: VideoTransitionLabel,
) -> tuple[bool, str]:
    """영상에서 추출한 실제 행동 좌표를 검증."""

    if not label.action_observable:
        if label.actual_action != "unknown":
            return (
                False,
                "관측 불가 라벨의 actual_action은 "
                "unknown이어야 합니다.",
            )
        return False, "최초 행동을 관측할 수 없습니다."

    if label.actual_action != "swap":
        return False, "실제 행동이 swap이 아닙니다."

    if label.source is None or label.target is None:
        return (
            False,
            "swap 라벨에 source 또는 target이 없습니다.",
        )

    return _validate_swap_cells(
        label.source,
        label.target,
    )


def _validate_swap_cells(
    source: Any,
    target: Any,
) -> tuple[bool, str]:
    for cell in (source, target):
        if not 1 <= cell.row <= ROWS:
            return (
                False,
                f"행 {cell.row}가 1~{ROWS} 범위를 "
                "벗어났습니다.",
            )
        if not 1 <= cell.col <= COLS:
            return (
                False,
                f"열 {cell.col}가 1~{COLS} 범위를 "
                "벗어났습니다.",
            )

    distance = (
        abs(source.row - target.row)
        + abs(source.col - target.col)
    )
    if distance != 1:
        return (
            False,
            "source와 target이 상하좌우로 "
            "인접하지 않습니다.",
        )

    return True, "유효한 인접 swap"
