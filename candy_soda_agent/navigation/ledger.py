"""(화면, 행동) 조합별로 "눌러봤는지"와 "눌렀더니 무슨 일이 있었는지"를 따로 기록하는 원장.

기존 GraphStore.explored_actions()는 간선이 생겼을 때만 "탐색됨"으로 쳤기 때문에,
눌러도 화면이 안 바뀌는 버튼(no_change)은 실행할 때마다 다시 프론티어로 뽑혔습니다.
원장은 attempts(시도 횟수) 하나만으로 프론티어 여부를 정하고, verdict는 결과를
설명하는 별도 필드로 둡니다. 그래서:
  - 무반응 버튼: attempts > 0 이 되면 프론티어에서 빠짐(프론티어가 바닥나면
    reopen_failed가 FRONTIER_RETRY_MAX번까지만 다시 엶)
  - 프론티어: attempts == 0 인 (화면, 행동) 쌍의 전역 집합
  - 커버리지: 전체 대비 attempts == 0 비율
  - 트리 간선 라벨: 어떤 verdict였는지 그대로 재사용
가 전부 이 원장 하나에서 파생됩니다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from config import NAVIGATION_LEDGER_PATH
from schemas import ActionLedger, LedgerEntry

# 실행 시점에 알 수 있는 시도 결과. finalize()가 한 스텝 뒤 관찰로 채웁니다.
Verdict = str  # "new_screen" | "known_screen" | "no_change" | "blocked" | "failed" | "not_visible" | "missed" | "not_found" | "global" | "play_entry"

# 탭 없이 프론티어에서 빠진 판정입니다(도달 포기 / 후보에서 사라짐 / 확대 확인에서 못 찾음 /
# 공통 버튼으로 옮겨짐 / 플레이 진입 버튼이라 안 누름).
CLOSED_WITHOUT_TAP = frozenset({"blocked", "not_visible", "not_found", "global", "play_entry"})

# 실패로 닫혀 다시 시도할 가치가 있는 판정(reopen_failed): 눌러도 화면 그대로 / 확대 확인에서
# 못 찾음 / 가는 길이 반복 실패. 새 화면·아는 화면으로 간 것, 공통 버튼으로 합쳐진 것, 플레이
# 진입 버튼은 다시 열지 않습니다. not_visible은 분석 재사용(decisions/)과 겹쳐 다시 열어도
# 같은 이름이 다시 나오지 않으므로 넣지 않습니다.
RETRYABLE_VERDICTS = frozenset({"no_change", "not_found", "blocked"})

# 네비게이션 바처럼 여러 화면에 똑같이 있는 공통 버튼의 원장 항목은 화면 ID 대신 이 값을
# 씁니다. 공통 버튼은 화면마다가 아니라 전체에서 한 번만 눌러 봅니다(ui_chrome.py).
GLOBAL_SCREEN_ID = "__global__"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _entry_key(screen_id: str, action_key: str) -> str:
    return f"{screen_id}:{action_key}"


def is_open_entry(entry: LedgerEntry) -> bool:
    """프론티어 = 아직 눌러보지 않았고 탭 없이 정리되지도 않은 항목, 또는 눌렀는데
    픽셀이 그대로라 빗나감이 의심돼 재시도를 기다리는 항목(missed)."""
    if entry.verdict == "missed":
        return True
    return entry.attempts == 0 and entry.verdict not in CLOSED_WITHOUT_TAP


class LedgerStore:
    """행동 원장을 불러오고, 갱신하고, 저장합니다(GraphStore와 짝을 이루는 저장소)."""

    def __init__(self, path: Path | None = None):
        # 기본값을 함수 시그니처의 기본 인자로 두면 import 시점 값에 고정되어
        # (GraphStore의 GRAPH_PATH처럼) 테스트에서 모듈 속성을 patch해도 반영되지
        # 않습니다. 호출 시점에 모듈 전역을 다시 읽도록 본문에서 폴백합니다.
        self._path = path if path is not None else NAVIGATION_LEDGER_PATH
        if self._path.exists():
            self.ledger = ActionLedger.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        else:
            self.ledger = ActionLedger()

        # verdict가 비어 있는데 attempts > 0인 항목은 직전 실행이 탭 이후 결과를
        # 확인하기 전에 끊긴 것입니다. 증거가 없으니 안전하게 "안 시도함"으로
        # 되돌려 다음 실행에서 프론티어로 다시 잡히게 합니다.
        swept = False
        for entry in self.ledger.entries.values():
            if entry.verdict is None and entry.attempts > 0:
                entry.attempts = 0
                swept = True
        if swept:
            self.save()

    def seen(self, screen_id: str, action_key: str, action_label: str) -> None:
        """이번 화면에서 처음 발견한 (화면,행동)이면 시도 0회 상태로 원장에 추가합니다.
        이미 있으면 아무것도 바꾸지 않습니다(시도 이력을 덮어쓰지 않기 위해)."""

        key = _entry_key(screen_id, action_key)
        if key in self.ledger.entries:
            return
        self.ledger.entries[key] = LedgerEntry(
            screen_id=screen_id,
            action_key=action_key,
            action_label=action_label,
        )
        self.save()

    def mark_attempted(self, screen_id: str, action_key: str, action_label: str) -> None:
        """실제로 탭을 실행하는 시점에 attempts를 올립니다. 같은 실행 안에서 같은
        (화면,행동)이 다시 프론티어로 뽑히지 않도록 결과를 알기 전에 먼저 반영하고,
        판정은 한 스텝 뒤 finalize()가 채웁니다."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            entry = LedgerEntry(
                screen_id=screen_id, action_key=action_key, action_label=action_label
            )
            self.ledger.entries[key] = entry
        entry.attempts += 1
        entry.verdict = None
        entry.last_seen = _now()
        self.save()

    def finalize(
        self,
        screen_id: str,
        action_key: str,
        verdict: Verdict,
        target_screen_id: str | None,
    ) -> None:
        """다음 화면 관찰 결과로 직전 시도의 판정을 채웁니다."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            # mark_attempted 없이 finalize만 불릴 일은 정상 흐름에서 없지만,
            # 방어적으로 기록이 사라지지 않게 새로 만듭니다.
            entry = LedgerEntry(screen_id=screen_id, action_key=action_key, attempts=1)
            self.ledger.entries[key] = entry
        entry.verdict = verdict
        entry.target_screen_id = target_screen_id
        entry.last_seen = _now()
        self.save()

    def record_miss(
        self,
        screen_id: str,
        action_key: str,
        max_retries: int,
    ) -> Verdict:
        """눌렀는데 화면 픽셀이 그대로였던 시도를 기록합니다(2단계).

        빗나간 탭일 가능성이 커서 바로 no_change로 닫지 않고 missed(프론티어 유지)로
        두었다가, 다시 눌러도(attempts > max_retries) 그대로면 no_change로 닫습니다.
        반환: 이번에 남긴 판정."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            entry = LedgerEntry(screen_id=screen_id, action_key=action_key, attempts=1)
            self.ledger.entries[key] = entry
        verdict = "missed" if entry.attempts <= max_retries else "no_change"
        entry.verdict = verdict
        entry.target_screen_id = screen_id if verdict == "no_change" else None
        entry.last_seen = _now()
        self.save()
        return verdict

    def close_not_found(self, screen_id: str, action_key: str) -> None:
        """확대 재확인에서 버튼을 찾지 못해 누르지 않은 항목을 프론티어에서 뺍니다.
        조사 모델이 없는 버튼을 지어낸 경우라, 다시 골라도 같은 결과일 가능성이 큽니다."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            entry = LedgerEntry(screen_id=screen_id, action_key=action_key)
            self.ledger.entries[key] = entry
        # 실제로 누르지 않았으므로 attempts는 그대로 둡니다. missed 상태에서 못 찾은
        # 경우에도 다시 고르지 않도록 판정만 not_found로 바꿉니다.
        if entry.attempts > 0:
            entry.verdict = "no_change"
        else:
            entry.verdict = "not_found"
        entry.last_seen = _now()
        self.save()

    def close_play_entry(self, screen_id: str, action_key: str, action_label: str) -> bool:
        """플레이 진입 버튼(PLAY_POLICY=avoid)을 기록만 하고 프론티어에서 뺍니다.

        보고서에 "봤지만 누르지 않음"으로 남도록 처음 보는 버튼도 항목을 만들어 닫습니다.
        정책을 켜기 전에 이미 눌러 본 항목(판정이 있는 것)은 기록을 지우지 않고 그대로 둡니다.
        반환: 이번에 새로 닫았으면 True."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            entry = LedgerEntry(screen_id=screen_id, action_key=action_key, action_label=action_label)
            self.ledger.entries[key] = entry
        elif entry.verdict == "play_entry" or not is_open_entry(entry):
            return False
        entry.verdict = "play_entry"
        entry.last_seen = _now()
        self.save()
        return True

    def promote_to_global(self, action_key: str, action_label: str) -> tuple[int, bool]:
        """이 행동이 공통 버튼(네비게이션 바)으로 판정되면 화면별 항목을 공통 항목 하나로 합칩니다.

        - 화면별로 아직 안 눌러 본 같은 행동 항목은 닫습니다(verdict="global").
        - 판정 전에 어느 화면에서 이미 눌러 봐서 간 곳을 알면, 그 결과를 공통 항목에
          옮겨 적어 공통 버튼을 다시 누르지 않게 합니다.
        반환: (닫은 항목 수, 이전 결과를 옮겨 적었는지)."""

        closed = 0
        best: LedgerEntry | None = None
        for entry in self.ledger.entries.values():
            if entry.action_key != action_key or entry.screen_id == GLOBAL_SCREEN_ID:
                continue
            if is_open_entry(entry):
                entry.verdict = "global"
                entry.last_seen = _now()
                closed += 1
            elif entry.verdict in {"new_screen", "known_screen"} and entry.target_screen_id:
                if best is None or entry.last_seen > best.last_seen:
                    best = entry

        seeded = False
        key = _entry_key(GLOBAL_SCREEN_ID, action_key)
        current = self.ledger.entries.get(key)
        if best is not None and (current is None or current.attempts == 0):
            self.ledger.entries[key] = LedgerEntry(
                screen_id=GLOBAL_SCREEN_ID,
                action_key=action_key,
                action_label=action_label or best.action_label,
                attempts=max(1, best.attempts),
                verdict=best.verdict,
                target_screen_id=best.target_screen_id,
                last_seen=_now(),
            )
            seeded = True
        if closed or seeded:
            self.save()
        return closed, seeded

    def block(self, screen_id: str, action_key: str) -> None:
        """경로 자체가 반복 도달 불가라 더 이상 프론티어로 고르지 않을 항목을 막습니다."""

        key = _entry_key(screen_id, action_key)
        entry = self.ledger.entries.get(key)
        if entry is None:
            entry = LedgerEntry(screen_id=screen_id, action_key=action_key)
            self.ledger.entries[key] = entry
        # 실제로 누른 적이 없으므로 attempts는 올리지 않고 판정만 남깁니다
        # (커버리지의 "시도한 것"에 섞이지 않게).
        entry.verdict = "blocked"
        entry.last_seen = _now()
        self.save()

    def note_visit(
        self,
        screen_id: str,
        visible_action_keys: list[str],
        max_misses: int,
    ) -> list[str]:
        """이 화면을 관찰할 때마다 호출합니다. 미시도 항목이 이번 후보에 보였으면
        misses를 0으로, 안 보였으면 1 올립니다. 연속 max_misses번 안 보인 항목은
        not_visible로 정리하고, 정리된 action_key 목록을 반환합니다.
        (LLM이 매번 후보를 조금씩 다르게 내서, 한 번 본 버튼이 다시 안 나올 수 있습니다.)"""

        visible = set(visible_action_keys)
        retired: list[str] = []
        changed = False
        for entry in self.ledger.entries.values():
            if entry.screen_id != screen_id or not is_open_entry(entry):
                continue
            if entry.action_key in visible:
                if entry.misses:
                    entry.misses = 0
                    changed = True
                continue
            entry.misses += 1
            changed = True
            if entry.misses >= max_misses:
                entry.verdict = "not_visible"
                entry.last_seen = _now()
                retired.append(entry.action_key)
        if changed:
            self.save()
        return retired

    def reopen_failed(
        self,
        max_retries: int,
        can_reach: Callable[[LedgerEntry], bool] | None = None,
    ) -> list[LedgerEntry]:
        """실패로 닫힌 항목(RETRYABLE_VERDICTS)을 다시 프론티어로 엽니다.

        항목마다 retries를 세어 max_retries번까지만 엽니다(원장에 남으므로 run을 넘어도
        무한 반복하지 않습니다). can_reach가 False인 항목은 열지 않고 횟수도 쓰지 않습니다.
        다시 연 항목은 시도 0회·판정 없음으로 돌아가 평소처럼 프론티어로 뽑힙니다.
        반환: 이번에 다시 연 항목 목록."""

        reopened: list[LedgerEntry] = []
        if max_retries <= 0:
            return reopened
        for entry in self.ledger.entries.values():
            if entry.verdict not in RETRYABLE_VERDICTS or entry.retries >= max_retries:
                continue
            if can_reach is not None and not can_reach(entry):
                continue
            entry.retries += 1
            entry.attempts = 0
            entry.verdict = None
            entry.target_screen_id = None
            entry.misses = 0
            entry.last_seen = _now()
            reopened.append(entry)
        if reopened:
            self.save()
        return reopened

    def entry(self, screen_id: str, action_key: str) -> LedgerEntry | None:
        return self.ledger.entries.get(_entry_key(screen_id, action_key))

    def dead_keys(
        self, screen_id: str, verdicts: tuple[str, ...] = ("not_found", "no_change")
    ) -> set[str]:
        """이 화면에서 눌러도 소용없다고 확인된 행동: 확대 확인에서 못 찾음(화면에 없음) 또는
        눌러도 화면 그대로. 리셋·탈출에서 같은 버튼을 다시 찾느라 스텝을 쓰지 않게 합니다."""
        return {
            entry.action_key
            for entry in self.ledger.entries.values()
            if entry.screen_id == screen_id and entry.verdict in verdicts
        }

    def failed_count(self) -> int:
        """화면을 못 바꾸거나 못 찾아 닫힌 항목 수(종료 메시지용, not_visible 포함)."""
        failed = RETRYABLE_VERDICTS | {"not_visible"}
        return sum(1 for entry in self.ledger.entries.values() if entry.verdict in failed)

    def frontier_at(self, screen_id: str) -> list[str]:
        """이 화면에서 아직 시도하지 않은 action_key 목록."""
        return [
            entry.action_key
            for entry in self.ledger.entries.values()
            if entry.screen_id == screen_id and is_open_entry(entry)
        ]

    def global_frontier(self) -> dict[str, list[str]]:
        """화면별 미시도 action_key 목록 전체(2단계: 원거리 프론티어 탐색용)."""
        frontier: dict[str, list[str]] = {}
        for entry in self.ledger.entries.values():
            if is_open_entry(entry):
                frontier.setdefault(entry.screen_id, []).append(entry.action_key)
        return frontier

    def is_frontier_empty(self) -> bool:
        return not any(is_open_entry(entry) for entry in self.ledger.entries.values())

    def coverage(self) -> tuple[int, int]:
        """(실제로 눌러본 항목 수, 전체 항목 수). 보고서의 커버리지 지표에 씁니다."""
        total = len(self.ledger.entries)
        attempted = sum(1 for entry in self.ledger.entries.values() if entry.attempts > 0)
        return attempted, total

    def closed_without_tap(self) -> int:
        """탭 없이 프론티어에서 빠진 항목 수(blocked + not_visible)."""
        return sum(
            1 for entry in self.ledger.entries.values()
            if entry.attempts == 0 and entry.verdict in CLOSED_WITHOUT_TAP
        )

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self.ledger.model_dump_json(indent=2), encoding="utf-8")
