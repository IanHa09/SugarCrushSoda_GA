"""OpenAI 비전 모델의 구조화 응답 요청을 한 곳에서 처리합니다."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from config import BOARD_IMAGE_DETAIL, FULL_IMAGE_DETAIL
from image_utils import image_to_data_url


def model_supports_reasoning(model: str) -> bool:
    """모델명이 reasoning effort 파라미터를 지원하는 계열인지 판별합니다."""

    normalized = model.lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def request_structured_vision(
    client,
    *,
    model: str,
    system_prompt: str,
    prompt: str,
    full_image: np.ndarray,
    board_image: np.ndarray | None,
    board_note: str,
    text_format,
    max_output_tokens: int,
    timing_label: str,
    missing_response_message: str,
):
    """전체 화면과 선택적 보드 확대본을 보내 구조화된 응답을 받습니다(플레이 분석/구조 조사 공용)."""

    total_started = time.perf_counter()
    encode_started = time.perf_counter()
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": prompt},
        {
            "type": "input_image",
            "image_url": image_to_data_url(full_image),
            "detail": FULL_IMAGE_DETAIL,
        },
    ]
    if board_image is not None:
        content.extend([
            {"type": "input_text", "text": board_note},
            {
                "type": "input_image",
                "image_url": image_to_data_url(board_image),
                "detail": BOARD_IMAGE_DETAIL,
            },
        ])
    encode_elapsed = time.perf_counter() - encode_started

    request: dict[str, Any] = {
        "model": model,
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "text_format": text_format,
    }
    if model_supports_reasoning(model):
        request["reasoning"] = {"effort": "minimal"}

    api_started = time.perf_counter()
    try:
        response = client.responses.parse(**request)
    finally:
        print(
            f"[{timing_label}] "
            f"image_encode={encode_elapsed:.2f}s, "
            f"api={time.perf_counter() - api_started:.2f}s, "
            f"total={time.perf_counter() - total_started:.2f}s"
        )
    if response.output_parsed is None:
        raise RuntimeError(missing_response_message)
    return response.output_parsed
