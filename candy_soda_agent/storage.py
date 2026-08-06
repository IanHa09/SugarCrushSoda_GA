"""실행, 학습 메모리, 세션 수명주기 로그를 저장합니다."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import (
    CAPTURE_DIR,
    LOG_PATH,
    MEMORY_PATH,
    MODEL,
    SESSION_LOG_PATH,
)
from schemas import AgentDecision


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as output_file:
        output_file.write(line + "\n")


def save_image(image: np.ndarray, label: str) -> str | None:
    """캡처 이미지를 저장하고 실패하면 존재하지 않는 경로를 반환하지 않습니다."""

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    path = CAPTURE_DIR / f"{timestamp}_{label}.png"
    if not cv2.imwrite(str(path), image):
        print(f"[WARNING] 이미지 저장 실패: {path}")
        return None
    return str(path)


def save_run(
    raw_image: np.ndarray,
    grid_image: np.ndarray,
    decision: AgentDecision,
    is_valid: bool,
    validation_message: str,
    *,
    session_id: str,
    step: int,
    stored_image_scope: str,
) -> dict[str, Any]:
    raw_path = save_image(raw_image, "raw")
    grid_path = save_image(grid_image, "grid")
    if raw_path is None or grid_path is None:
        raise OSError("분석 이미지를 완전하게 저장하지 못해 행동을 차단합니다.")

    record = {
        "schema_version": 2,
        "timestamp": _now(),
        "session_id": session_id,
        "step": step,
        "model": MODEL,
        "stored_image_scope": stored_image_scope,
        "raw_image": raw_path,
        "grid_image": grid_path,
        "valid": is_valid,
        "validation_message": validation_message,
        "decision": decision.model_dump(),
    }
    _append_jsonl(LOG_PATH, record)
    return record


def save_memory_entry(
    *,
    session_id: str,
    step: int,
    mode: str,
    before: dict,
    decision: dict,
    execution: dict,
    after: dict,
    reward: float,
    success_estimate: str,
    lesson: str,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    if reward not in {-1.0, 0.0, 1.0}:
        raise ValueError("reward는 -1.0, 0.0, 1.0 중 하나여야 합니다.")

    record = {
        "schema_version": 2,
        "timestamp": _now(),
        "session_id": session_id,
        "step": step,
        "model": MODEL,
        "mode": mode,
        "before": before,
        "decision": decision,
        "execution": execution,
        "after": after,
        "outcome": {
            "reward": reward,
            "success_estimate": success_estimate,
            "failure_reason": failure_reason,
        },
        "lesson": lesson,
    }
    _append_jsonl(MEMORY_PATH, record)
    return record


def load_recent_memories(limit: int) -> list[dict[str, Any]]:
    """손상된 줄을 건너뛰며 최근 학습 기록만 반환합니다."""

    if limit <= 0 or not MEMORY_PATH.exists():
        return []

    recent: deque[dict[str, Any]] = deque(maxlen=limit)
    with MEMORY_PATH.open("r", encoding="utf-8") as memory_file:
        for line_number, line in enumerate(memory_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARNING] memory.jsonl {line_number}번 줄을 건너뜁니다.")
                continue
            if isinstance(record, dict):
                recent.append(record)
    return list(recent)


def append_session_event(
    session_id: str,
    event: str,
    **details: Any,
) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "timestamp": _now(),
        "session_id": session_id,
        "event": event,
        **details,
    }
    _append_jsonl(SESSION_LOG_PATH, record)
    return record


def start_session(session_id: str, settings: dict[str, Any]) -> None:
    append_session_event(
        session_id,
        "started",
        model=MODEL,
        settings=settings,
    )


def finish_session(
    session_id: str,
    *,
    stop_reason: str,
    api_call_count: int,
    successful_actions: int,
    failed_actions: int,
    blocked_actions: int,
    last_error: str | None,
) -> None:
    append_session_event(
        session_id,
        "finished",
        stop_reason=stop_reason,
        api_call_count=api_call_count,
        successful_actions=successful_actions,
        failed_actions=failed_actions,
        blocked_actions=blocked_actions,
        last_error=last_error,
    )
