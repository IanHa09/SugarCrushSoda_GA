"""실행, 학습 메모리, 세션 수명주기 로그를 저장합니다."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
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
    SURVEY_DEDUP_SCAN_LIMIT,
    SURVEY_LOG_PATH,
)
from image_utils import fingerprint_distance
from schemas import AgentDecision, SurveyDecision
from survey_utils import (
    button_key,
    collect_button_keys,
    collect_element_keys,
    element_key,
    screen_signature,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """기록 한 건을 JSONL 파일에 한 줄로 추가합니다."""
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
    grid: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """원본/그리드 이미지를 저장하고 실행 결과를 기록으로 남깁니다."""
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
        "grid": grid,
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
    """행동-보상-교훈을 하나의 학습 메모리 기록으로 저장합니다."""
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


def _parse_jsonl_records(
    path: Path,
    lines: Iterable[str],
) -> list[dict[str, Any]]:
    """빈 줄과 손상된 줄은 건너뛰고 dict 기록만 모읍니다."""

    records: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            print(f"[WARNING] {path}의 손상된 줄을 건너뜁니다: {text[:60]}")
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _read_tail_lines(
    path: Path,
    limit: int,
    chunk_size: int = 65536,
) -> list[str]:
    """파일을 끝에서부터 청크 단위로 읽어 마지막 limit개 줄만 반환합니다."""

    with path.open("rb") as binary_file:
        binary_file.seek(0, os.SEEK_END)
        position = binary_file.tell()
        tail = b""
        # limit개를 온전히 확보하려면 줄바꿈이 limit개보다 많아야 합니다.
        while position > 0 and tail.count(b"\n") <= limit:
            step = min(chunk_size, position)
            position -= step
            binary_file.seek(position)
            tail = binary_file.read(step) + tail

    lines = tail.splitlines()
    if position > 0:
        # 첫 줄은 청크 경계에서 잘렸을 수 있어 버립니다.
        lines = lines[1:]
    # 완성된 줄만 디코딩하므로 경계가 여러 바이트 문자를 갈라도 안전합니다.
    return [line.decode("utf-8", errors="replace") for line in lines[-limit:]]


def _load_recent_jsonl(path: Path, limit: int | None) -> list[dict[str, Any]]:
    """손상된 줄은 건너뛰고 limit이 None이면 전체를, 정수면 최근 그만큼만 읽습니다."""

    if not path.exists():
        return []
    if limit is not None and limit <= 0:
        return []

    if limit is None:
        with path.open("r", encoding="utf-8") as input_file:
            return _parse_jsonl_records(path, input_file)
    return _parse_jsonl_records(path, _read_tail_lines(path, limit))


def load_recent_memories(limit: int) -> list[dict[str, Any]]:
    """최근 학습 기록을 반환합니다."""

    return _load_recent_jsonl(MEMORY_PATH, limit)


def load_recent_survey_records(limit: int) -> list[dict[str, Any]]:
    """중복 판정에 참조할 최근 게임 구조 조사 기록을 반환합니다."""

    return _load_recent_jsonl(SURVEY_LOG_PATH, limit)


def load_all_survey_records() -> list[dict[str, Any]]:
    """보고서 생성을 위해 게임 구조 조사 기록 전체를 반환합니다."""

    return _load_recent_jsonl(SURVEY_LOG_PATH, None)


def _known_survey_keys(
    recent_records: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    # 최근 기록에서 이미 등장한 화면/요소/버튼 키를 모읍니다.
    screen_keys: set[str] = set()
    element_keys: set[str] = set()
    button_keys: set[str] = set()

    for record in recent_records:
        dedupe = record.get("dedupe", {})
        screen_key = dedupe.get("screen_signature")
        if isinstance(screen_key, str) and screen_key:
            screen_keys.add(screen_key)
        for key in dedupe.get("all_element_keys", []):
            if isinstance(key, str):
                element_keys.add(key)
        for key in dedupe.get("all_button_keys", []):
            if isinstance(key, str):
                button_keys.add(key)
    return screen_keys, element_keys, button_keys


def save_survey_record(
    *,
    session_id: str,
    step: int,
    mode: str,
    raw_image: np.ndarray,
    grid_image: np.ndarray | None,
    decision: SurveyDecision,
    stored_image_scope: str,
    screen_fingerprint: str,
    grid: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    recent_records: list[dict[str, Any]] | None = None,
    model: str = MODEL,
) -> dict[str, Any]:
    """조사 모드 분석 결과를 이미지 증거와 함께 저장합니다."""

    raw_path = save_image(raw_image, "survey_raw")
    if raw_path is None:
        raise OSError("조사 원본 이미지를 저장하지 못했습니다.")
    grid_path = save_image(grid_image, "survey_grid") if grid_image is not None else None
    if grid_image is not None and grid_path is None:
        raise OSError("조사 보드 이미지를 저장하지 못했습니다.")

    recent = (
        recent_records
        if recent_records is not None
        else load_recent_survey_records(SURVEY_DEDUP_SCAN_LIMIT)
    )
    known_screens, known_elements, known_buttons = _known_survey_keys(recent)

    current_screen = screen_signature(decision)
    all_element_keys = collect_element_keys(decision)
    all_button_keys = collect_button_keys(decision)
    new_element_keys = [key for key in all_element_keys if key not in known_elements]
    duplicate_element_keys = [
        key for key in all_element_keys if key in known_elements
    ]
    new_button_keys = [key for key in all_button_keys if key not in known_buttons]
    duplicate_button_keys = [key for key in all_button_keys if key in known_buttons]

    element_records = []
    for element in decision.game_elements:
        key = element_key(element.category, element.name)
        element_records.append({
            "key": key,
            "is_duplicate": key in known_elements,
            **element.model_dump(),
        })

    button_records = []
    for button in decision.button_candidates:
        key = button_key(button.role, button.label)
        button_records.append({
            "key": key,
            "is_duplicate": key in known_buttons,
            **button.model_dump(),
        })

    record = {
        "schema_version": 1,
        "timestamp": _now(),
        "session_id": session_id,
        "step": step,
        "model": model,
        "mode": mode,
        "stored_image_scope": stored_image_scope,
        "raw_image": raw_path,
        "grid_image": grid_path,
        "screen_fingerprint": screen_fingerprint,
        "grid": grid,
        "survey": decision.model_dump(),
        "elements": element_records,
        "buttons": button_records,
        "dedupe": {
            "screen_signature": current_screen,
            "is_duplicate_screen": current_screen in known_screens,
            "all_element_keys": all_element_keys,
            "new_element_keys": new_element_keys,
            "duplicate_element_keys": duplicate_element_keys,
            "all_button_keys": all_button_keys,
            "new_button_keys": new_button_keys,
            "duplicate_button_keys": duplicate_button_keys,
        },
        "action": action or {"type": "record_only"},
    }
    _append_jsonl(SURVEY_LOG_PATH, record)
    return record


def load_failed_moves_for_board(
    fingerprint: str,
    rows: int,
    cols: int,
    *,
    scan_limit: int,
    context_limit: int,
    max_distance: float,
) -> tuple[list[dict[str, Any]], set[tuple[int, int, int, int]]]:
    """같은 보드에서 게임이 거부했거나 변화가 없던 swap만 반환합니다."""

    matches: list[dict[str, Any]] = []
    moves: set[tuple[int, int, int, int]] = set()
    for memory in reversed(load_recent_memories(scan_limit)):
        before = memory.get("before", {})
        grid = before.get("grid", {})
        execution = memory.get("execution", {})
        if execution.get("action_outcome") not in {"rejected", "no_change"}:
            continue
        if grid.get("rows") != rows or grid.get("cols") != cols:
            continue
        if fingerprint_distance(
            fingerprint,
            str(before.get("board_fingerprint", "")),
        ) > max_distance:
            continue

        decision = memory.get("decision", {})
        source = decision.get("source")
        target = decision.get("target")
        if not isinstance(source, dict) or not isinstance(target, dict):
            continue
        try:
            first = (int(source["row"]), int(source["col"]))
            second = (int(target["row"]), int(target["col"]))
        except (KeyError, TypeError, ValueError):
            continue
        move = (*min(first, second), *max(first, second))
        if move in moves:
            continue
        moves.add(move)
        matches.append(memory)
        if len(matches) >= context_limit:
            break
    return matches, moves


def append_session_event(
    session_id: str,
    event: str,
    **details: Any,
) -> dict[str, Any]:
    # 세션 이벤트 한 건을 세션 로그에 기록합니다.
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
    # 세션 시작 이벤트를 기록합니다.
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
    # 세션 종료 이벤트와 최종 통계를 기록합니다.
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
