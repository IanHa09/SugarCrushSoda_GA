"""분석에 사용한 이미지와 LLM 응답을 실험 데이터로 저장합니다."""

from __future__ import annotations

import json
from datetime import datetime

import cv2
import numpy as np

from config import CAPTURE_DIR, LOG_PATH, MODEL
from schemas import AgentDecision


def save_run(
    raw_image: np.ndarray,
    grid_image: np.ndarray,
    decision: AgentDecision,
    is_valid: bool,
    validation_message: str,
) -> None:
    """원본, 격자 이미지, JSONL 로그를 한 번의 실행 기록으로 저장합니다."""

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")

    raw_path = CAPTURE_DIR / f"{timestamp}_raw.png"
    grid_path = CAPTURE_DIR / f"{timestamp}_grid.png"

    if not cv2.imwrite(str(raw_path), raw_image):
        print(f"[WARNING] 원본 이미지 저장 실패: {raw_path}")

    if not cv2.imwrite(str(grid_path), grid_image):
        print(f"[WARNING] 격자 이미지 저장 실패: {grid_path}")

    record = {
        "timestamp": datetime.now().isoformat(),
        "model": MODEL,
        "raw_image": str(raw_path),
        "grid_image": str(grid_path),
        "valid": is_valid,
        "validation_message": validation_message,
        "decision": decision.model_dump(),
    }

    # JSONL은 한 줄에 한 번의 실험 결과가 들어가는 형식입니다.
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
