"""OpenAI 비전 모델 호출과 행동 검증을 담당합니다."""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from config import COLS, MIN_CONFIDENCE, MODEL, ROWS
from image_utils import image_to_data_url
from schemas import AgentDecision


def analyze_board(
    client: OpenAI,
    grid_image: np.ndarray,
) -> AgentDecision:
    """격자가 추가된 퍼즐 화면을 보내 다음 행동 후보를 받습니다."""

    image_data_url = image_to_data_url(grid_image)

    prompt = f"""
이미지는 Candy Crush Soda 퍼즐 보드이며 빨간 격자와 좌표가 표시되어 있다.

좌표 규칙:
- 행은 위에서 아래로 1부터 {ROWS}까지이다.
- 열은 왼쪽에서 오른쪽으로 1부터 {COLS}까지이다.
- source와 target은 반드시 상하좌우로 붙은 두 칸이어야 한다.
- 대각선 교환은 허용하지 않는다.

해야 할 일:
1. 화면에서 확실히 확인되는 사탕, 특수 사탕, 장애물, 목표 요소를
   visible_elements에 간단히 기록한다.
2. 현재 화면에서 3개 이상의 매치를 만들 가능성이 높은 swap 하나를 고른다.
3. 애니메이션, 팝업, 잘린 화면 또는 불확실한 보드라면 wait를 고른다.
4. 보이지 않는 요소를 추측하지 않는다.
5. 확신이 낮으면 억지로 swap하지 말고 wait를 고른다.
6. reason은 한 문장으로 작성한다.

현재 단계에서는 게임을 직접 조작하지 말고 다음 행동 후보만 반환한다.
"""

    # responses.parse와 Pydantic 모델을 사용하면 응답 형식을 일정하게
    # 유지할 수 있어 문자열 JSON을 직접 파싱하는 수고가 줄어듭니다.
    response = client.responses.parse(
        model=MODEL,
        # 퍼즐 화면 한장에 대한 짧은 판단, reasoning 토큰 최소화
        reasoning={"effort":"minimal"},

        # reasoing 과 최종 JSON 을 합친 출력 토큰 상한
        # 최대 사용 가능량을 제한
        max_output_tokens=1000,
        
        input=[
            {
                "role": "system",
                "content": (
                    "You are a conservative visual puzzle-game agent. "
                    "Use only information clearly visible in the image."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": image_data_url,
                        "detail": "high",
                    },
                ],
            },
        ],
        text_format=AgentDecision,
    )

    decision = response.output_parsed
    if decision is None:
        raise RuntimeError("구조화된 LLM 응답을 받지 못했습니다.")

    return decision


def validate_decision(
    decision: AgentDecision,
) -> tuple[bool, str]:
    """
    LLM 응답이 최소한의 게임 행동 형식에 맞는지 검사합니다.

    이 검증은 실제로 매치가 만들어지는지까지 증명하지는 않습니다.
    좌표 범위, 인접 여부, 화면 상태, 신뢰도만 확인합니다.
    """

    if decision.action == "wait":
        return True, "wait 행동"

    if decision.board_status != "stable":
        return False, "안정된 보드가 아닌데 swap을 선택했습니다."

    if decision.source is None or decision.target is None:
        return False, "swap인데 source 또는 target 좌표가 없습니다."

    for cell in (decision.source, decision.target):
        if not 1 <= cell.row <= ROWS:
            return False, f"행 {cell.row}가 1~{ROWS} 범위를 벗어났습니다."
        if not 1 <= cell.col <= COLS:
            return False, f"열 {cell.col}가 1~{COLS} 범위를 벗어났습니다."

    # 상하좌우로 바로 붙은 두 칸은 맨해튼 거리가 정확히 1입니다.
    distance = (
        abs(decision.source.row - decision.target.row)
        + abs(decision.source.col - decision.target.col)
    )
    if distance != 1:
        return False, "source와 target이 상하좌우로 인접하지 않습니다."

    if decision.confidence < MIN_CONFIDENCE:
        return False, (
            f"confidence={decision.confidence:.2f}가 "
            f"기준 {MIN_CONFIDENCE:.2f}보다 낮습니다."
        )

    return True, "기본 형식 검증을 통과한 swap"
