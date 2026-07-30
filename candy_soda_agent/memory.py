from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from config import (
    MEMORY_MAX_CHARS,
    MEMORY_TOP_EXAMPLES,
    MEMORY_TOP_STRATEGIES,
    VIDEO_EXPERIENCE_PATH,
)


def load_training_experiences(
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """자동 생성된 영상 학습 경험을 읽습니다."""

    if limit is not None and limit < 0:
        raise ValueError("limit은 0 이상이어야 합니다.")

    if not VIDEO_EXPERIENCE_PATH.exists():
        return []

    records: list[dict[str, Any]] = []

    with VIDEO_EXPERIENCE_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("record_type") != "video_training":
                continue

            if not record.get("reward_valid"):
                continue

            if not isinstance(
                record.get("score_delta"),
                int,
            ):
                continue

            records.append(record)

    # 경험 개수별 비교 실험에 사용
    if limit is not None:
        records = records[:limit]

    return records


def build_memory_prompt(
    limit: int | None = None,
) -> tuple[str, int]:
    """
    경험을 전략 통계와 최고 보상 사례로 압축.

    수백 개가 쌓여도 API에는 짧은 텍스트만 전달함.
    """

    records = load_training_experiences(
        limit
    )

    if not records:
        return (
            "사용 가능한 영상 학습 경험이 없습니다.",
            0,
        )

    grouped: dict[
        str,
        list[int],
    ] = defaultdict(list)

    for record in records:
        move_type = record.get(
            "move_type",
            "unknown",
        )

        grouped[move_type].append(
            int(record["score_delta"])
        )

    strategy_rows: list[dict[str, Any]] = []

    for move_type, rewards in grouped.items():
        strategy_rows.append(
            {
                "move_type": move_type,
                "count": len(rewards),
                "average": statistics.mean(
                    rewards
                ),
                "median": statistics.median(
                    rewards
                ),
            }
        )

    strategy_rows.sort(
        key=lambda row: float(row["average"]),
        reverse=True,
    )

    lines = [
        f"영상에서 수집한 경험 {len(records)}개:",
        "전략별 관측 보상:",
    ]

    for row in strategy_rows[
        :MEMORY_TOP_STRATEGIES
    ]:
        lines.append(
            f"- {row['move_type']}: "
            f"평균 +{row['average']:.0f}, "
            f"중앙값 +{row['median']:.0f}, "
            f"표본 {row['count']}개"
        )

    high_reward_records = sorted(
        records,
        key=lambda record: record["score_delta"],
        reverse=True,
    )

    lines.append("고보상 사례:")
    footer = (
        "과거 경험은 전략 참고용이다. "
        "현재 화면의 실제 배치를 우선하라."
    )

    used_examples = 0

    for record in high_reward_records:
        summary = (
            record.get(
                "board_summary",
                "",
            )
            .replace("\n", " ")
        )[:130]

        candidate = (
            f"- {record['move_type']}: "
            f"{summary}, "
            f"점수 +{record['score_delta']}"
        )

        prospective = "\n".join(
            lines + [candidate, footer]
        )

        if len(prospective) > MEMORY_MAX_CHARS:
            break

        lines.append(candidate)
        used_examples += 1

        if used_examples >= MEMORY_TOP_EXAMPLES:
            break

    lines.append(footer)

    prompt = "\n".join(lines)

    return (
        prompt[:MEMORY_MAX_CHARS],
        len(records),
    )
