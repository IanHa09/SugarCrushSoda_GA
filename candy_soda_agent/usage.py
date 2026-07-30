"""OpenAI API 사용량 집계와 실행별 예산 제한."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApiUsage:
    """Responses API가 반환한 토큰 사용량."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_response(cls, response: Any) -> "ApiUsage":
        usage = getattr(response, "usage", None)
        if usage is None:
            return cls()

        input_details = getattr(
            usage,
            "input_tokens_details",
            None,
        )
        output_details = getattr(
            usage,
            "output_tokens_details",
            None,
        )

        input_tokens = int(
            getattr(usage, "input_tokens", 0) or 0
        )
        output_tokens = int(
            getattr(usage, "output_tokens", 0) or 0
        )
        total_tokens = int(
            getattr(
                usage,
                "total_tokens",
                input_tokens + output_tokens,
            )
            or input_tokens + output_tokens
        )

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=int(
                getattr(
                    input_details,
                    "cached_tokens",
                    0,
                )
                or 0
            ),
            reasoning_tokens=int(
                getattr(
                    output_details,
                    "reasoning_tokens",
                    0,
                )
                or 0
            ),
        )

    def __add__(self, other: "ApiUsage") -> "ApiUsage":
        if not isinstance(other, ApiUsage):
            return NotImplemented

        return ApiUsage(
            input_tokens=(
                self.input_tokens + other.input_tokens
            ),
            output_tokens=(
                self.output_tokens + other.output_tokens
            ),
            total_tokens=(
                self.total_tokens + other.total_tokens
            ),
            cached_input_tokens=(
                self.cached_input_tokens
                + other.cached_input_tokens
            ),
            reasoning_tokens=(
                self.reasoning_tokens
                + other.reasoning_tokens
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": (
                self.cached_input_tokens
            ),
            "reasoning_tokens": self.reasoning_tokens,
        }

    def estimated_cost_usd(
        self,
        input_usd_per_million: float,
        output_usd_per_million: float,
        cached_input_usd_per_million: float | None = None,
    ) -> float:
        cached_rate = (
            input_usd_per_million
            if cached_input_usd_per_million is None
            else cached_input_usd_per_million
        )
        cached_tokens = min(
            self.input_tokens,
            self.cached_input_tokens,
        )
        uncached_tokens = (
            self.input_tokens - cached_tokens
        )

        return (
            uncached_tokens * input_usd_per_million
            + cached_tokens * cached_rate
            + self.output_tokens
            * output_usd_per_million
        ) / 1_000_000


class ApiBudgetExceeded(RuntimeError):
    """설정한 호출 또는 토큰 예산을 모두 사용한 경우."""


@dataclass
class UsageBudget:
    """API 호출을 시작하기 전에 한도를 검사하고 사용량을 누적."""

    max_calls: int
    max_total_tokens: int
    calls: int = 0
    usage: ApiUsage = field(default_factory=ApiUsage)

    def __post_init__(self) -> None:
        if self.max_calls <= 0:
            raise ValueError("max_calls는 1 이상이어야 합니다.")
        if self.max_total_tokens <= 0:
            raise ValueError(
                "max_total_tokens는 1 이상이어야 합니다."
            )

    @property
    def exhausted(self) -> bool:
        return (
            self.calls >= self.max_calls
            or self.usage.total_tokens
            >= self.max_total_tokens
        )

    def start_call(self) -> None:
        if self.calls >= self.max_calls:
            raise ApiBudgetExceeded(
                f"최대 API 요청 수 "
                f"{self.max_calls}회에 도달했습니다."
            )

        if (
            self.usage.total_tokens
            >= self.max_total_tokens
        ):
            raise ApiBudgetExceeded(
                f"최대 누적 토큰 "
                f"{self.max_total_tokens:,}개에 "
                "도달했습니다."
            )

        # 실패한 요청이 무한히 반복되지 않도록 시도도
        # 호출 횟수에 포함합니다.
        self.calls += 1

    def add_usage(self, usage: ApiUsage) -> None:
        self.usage = self.usage + usage

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "max_total_tokens": self.max_total_tokens,
            "exhausted": self.exhausted,
            "usage": self.usage.to_dict(),
        }
