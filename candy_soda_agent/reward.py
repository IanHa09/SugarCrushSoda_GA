"""행동 실행 결과를 보수적인 보상 값으로 변환합니다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RewardResult:
    reward: float
    success_estimate: str
    lesson: str
    failure_reason: str | None = None


def evaluate_reward(
    *,
    action: str,
    validation_passed: bool,
    executed: bool,
    dry_run: bool,
    screen_change_score: float | None,
    success_threshold: float,
    blocked_reason: str | None = None,
    execution_error: str | None = None,
) -> RewardResult:
    if not validation_passed:
        reason = blocked_reason or "행동 검증 실패"
        return RewardResult(-1.0, "negative", f"검증 실패: {reason}", reason)

    if execution_error:
        return RewardResult(
            -1.0,
            "negative",
            f"행동 실행 실패: {execution_error}",
            execution_error,
        )

    if blocked_reason:
        return RewardResult(
            0.0,
            "unknown",
            f"안전 검사로 행동이 차단됨: {blocked_reason}",
            blocked_reason,
        )

    if action == "wait":
        return RewardResult(0.0, "not_applicable", "불확실한 상태에서 wait를 유지함")

    if dry_run:
        return RewardResult(0.0, "unknown", "드라이런이므로 swap 결과를 평가하지 않음")

    if not executed or screen_change_score is None:
        return RewardResult(
            0.0,
            "unknown",
            "행동 후 화면을 확인하지 못해 결과를 보류함",
        )

    if screen_change_score >= success_threshold:
        return RewardResult(
            1.0,
            "positive",
            f"swap 후 화면 변화가 확인됨: {screen_change_score:.4f}",
        )

    reason = f"swap 후 화면 변화가 너무 작음: {screen_change_score:.4f}"
    return RewardResult(-1.0, "negative", reason, reason)
