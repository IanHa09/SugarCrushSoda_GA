from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

import numpy as np

from config import (
    OUTPUT_DIR,
    VIDEO_EVALUATION_PATH,
)

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(OUTPUT_DIR / ".matplotlib"),
)


def load_records() -> list[dict[str, Any]]:
    if not VIDEO_EVALUATION_PATH.exists():
        return []

    records = []

    with VIDEO_EVALUATION_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if (
                record.get("record_type")
                == "video_evaluation"
            ):
                records.append(record)

    return records


def main() -> None:
    import matplotlib.pyplot as plt

    records = load_records()

    if not records:
        print(
            "평가 기록이 없습니다."
        )
        return

    grouped = defaultdict(list)

    for record in records:
        key = record.get(
            "experiment_label",
            "unknown",
        )

        grouped[key].append(record)

    labels = []
    memory_sizes = []
    accuracies = []

    for label, group in grouped.items():
        exact_matches = [
            bool(
                record.get(
                    "prediction_matches_actual"
                )
            )
            for record in group
        ]

        valid_rewards = [
            record["score_delta"]
            for record in group
            if record.get("reward_valid")
            and isinstance(
                record.get("score_delta"),
                int,
            )
        ]

        memory_values = [
            int(
                record.get(
                    "memory_size",
                    0,
                )
            )
            for record in group
        ]

        accuracy = (
            sum(exact_matches)
            / len(exact_matches)
        )

        average_reward = (
            float(np.mean(valid_rewards))
            if valid_rewards
            else 0.0
        )

        memory_size = (
            int(np.median(memory_values))
            if memory_values
            else 0
        )

        labels.append(label)
        memory_sizes.append(memory_size)
        accuracies.append(accuracy)

        print(
            f"{label}: "
            f"memory={memory_size}, "
            f"정확도={accuracy:.2%}, "
            f"평균점수상승={average_reward:.1f}, "
            f"표본={len(group)}"
        )

    order = np.argsort(
        memory_sizes
    )

    sorted_memory = [
        memory_sizes[index]
        for index in order
    ]

    sorted_accuracy = [
        accuracies[index]
        for index in order
    ]

    plt.figure(
        figsize=(8, 5)
    )

    plt.plot(
        sorted_memory,
        sorted_accuracy,
        marker="o",
    )

    plt.ylim(
        0.0,
        1.0,
    )

    plt.xlabel(
        "Number of training experiences"
    )

    plt.ylabel(
        "Exact action accuracy"
    )

    plt.title(
        "Accuracy by Experience Memory Size"
    )

    plt.grid(True)
    plt.tight_layout()

    output_path = (
        OUTPUT_DIR
        / "memory_accuracy.png"
    )
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    plt.savefig(
        output_path,
        dpi=160,
    )

    plt.close()

    print(
        f"\n그래프 저장: {output_path}"
    )


if __name__ == "__main__":
    main()
