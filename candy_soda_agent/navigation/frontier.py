"""프론티어(아직 안 눌러본 (화면,행동) 쌍) 선택과 경로 재생/루트 리셋을 담당합니다.

역할을 셋으로 나눕니다.
  1. 현재 화면에 프론티어가 있으면 그 자리에서 고르면 됩니다(이동 비용 0).
  2. 없으면 그래프에서 프론티어가 남은 가장 가까운 화면까지 간선 경로를 계획하고
     ("로컬 재생") 한 스텝씩 재생하며 도착 화면을 검증합니다.
  3. 도착 검증이 어긋나면(팝업/광고 등으로 게임 UI가 결정적이지 않아 생김) 루트로
     되돌아간 뒤("리셋") 그 자리에서 다시 경로를 짜서 재생합니다("혼합" 전략).
     같은 목표를 FRONTIER_MAX_REPLAN_ATTEMPTS번 넘게 재시도해도 안 되면 포기(blocked)
     하고 다음 프론티어로 넘어갑니다.

이 모듈은 실제 캡처/탭 실행은 하지 않는 순수 계획 로직입니다. 각 스텝의 실제 실행은
navigation.navigator가 담당하고, 그 결과(도착 화면, 실행 성공 여부)를 이 모듈에
보고해 다음 계획을 받습니다.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Literal

from navigation.graph import GraphStore
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore
from navigation.policy import canonical_action_label
from schemas import LedgerEntry, NavigationEdge

StepKind = Literal["frontier", "replay", "reset", "reobserve"]
# decide()가 None을 반환한 이유: 프론티어 소진 / 남았지만 가는 길을 모름.
StopReason = Literal["converged", "unreachable"]


@dataclass
class StepGoal:
    """이번 스텝에서 시도할 행동.

    kind == "frontier": 현재 화면의 미시도 후보 중 우선순위가 가장 높은 걸 고르면
      됩니다(navigator가 기존 choose_button으로 고릅니다). action_key는 비어 있습니다.
    kind == "replay": action_key와 일치하는 버튼을 찾아 눌러야 하고, 눌렀을 때
      도착 화면이 expected_target_screen_id와 같은지 검증해야 합니다.
    kind == "reset": 루트로 가는 길을 그래프에서 알면 action_key(그 길의 첫 간선)를 누르고,
      모르면(action_key 없음) 닫기 → 홈 → 뒤로 버튼을 찾아 누릅니다.
    kind == "reobserve": 이 화면의 미시도 버튼이 이번 후보에 안 보였습니다. 아무것도
      누르지 않고 다시 관찰합니다(연속으로 안 보이면 원장이 not_visible로 정리).
    """

    kind: StepKind
    action_key: str = ""
    expected_target_screen_id: str | None = None


@dataclass
class FrontierWalker:
    """프론티어 계획 + 경로 재생 + 루트 리셋 상태를 스텝 단위로 진행하는 상태 기계."""

    graph: GraphStore
    ledger: LedgerStore
    max_back_taps: int
    max_replan_attempts: int
    root_screen_id: str | None = None
    # 공통 버튼(네비게이션 바) 지원. extra_edges는 그래프에 없는 지름길 간선을,
    # global_screens는 공통 action_key가 보이는 화면 목록을 돌려줍니다(ui_chrome.py).
    extra_edges: Callable[[], list[NavigationEdge]] | None = None
    global_screens: Callable[[str], list[str]] | None = None

    # decide()가 None을 반환했을 때 그 이유(navigator가 종료 메시지에 씁니다).
    stop_reason: StopReason | None = None

    _replay_queue: list[NavigationEdge] = field(default_factory=list)
    _replay_target: str | None = None
    _resetting: bool = False
    _back_taps_used: int = 0
    _replan_attempts: dict[str, int] = field(default_factory=dict)
    # 도달을 포기한 화면. 공통 버튼 목표 때문에 같은 화면을 다시 고르지 않게 합니다.
    _abandoned: set[str] = field(default_factory=set)
    # 리셋 중 루트로 가는 길의 첫 간선(아는 길로 돌아가는 중일 때). 결과를 그래프에 남깁니다.
    _homing_edge: NavigationEdge | None = None

    # -- 진행 상태 조회 -----------------------------------------------------

    def note_root_if_first(self, screen_id: str) -> None:
        """이번 run에서 처음 관찰한 화면을 루트로 기억합니다(ROOT_SCREEN_ID 미설정 시 폴백)."""
        if self.root_screen_id is None:
            self.root_screen_id = screen_id

    @property
    def is_resetting(self) -> bool:
        return self._resetting

    @property
    def replay_target(self) -> str | None:
        """지금 경로를 따라가거나 리셋 뒤 가려는 목표 화면."""
        return self._replay_target

    def _extra(self) -> list[NavigationEdge]:
        return self.extra_edges() if self.extra_edges else []

    def abort(self) -> None:
        """진행 중인 재생/리셋을 모두 버립니다(게임 재시작 뒤처럼 위치를 모를 때)."""
        self._replay_queue = []
        self._replay_target = None
        self._resetting = False
        self._back_taps_used = 0

    def begin_reset_to_root(self) -> None:
        """목표 없이 루트로 먼저 돌아갑니다(3단계: 시작 화면 정렬).

        이어서 하는 run이 루트가 아닌 화면(예: 상점)에서 시작하면, 그 화면부터 탐색해
        루트와 이어지지 않는 섬이 생겼습니다. 루트에 도착하면(report_reset_step)
        목표가 없으므로 그 자리에서 평소처럼 프론티어 탐색을 이어갑니다. 홈/뒤로를
        max_back_taps번 넘게 눌러도 못 가면 포기하고 지금 화면에서 그냥 시작합니다."""
        self._replay_queue = []
        self._replay_target = None
        self._resetting = True
        self._back_taps_used = 0

    # -- 다음 행동 계획 -------------------------------------------------------

    def decide(
        self,
        current_screen_id: str,
        visible_action_keys: Collection[str] | None = None,
        visible_global_keys: Collection[str] = (),
    ) -> StepGoal | None:
        """이번 스텝에서 시도할 행동을 정합니다.

        visible_action_keys: 이번 관찰에서 실제로 누를 수 있는 후보의 action_key.
          None이면 원장에 있는 항목이 전부 보인다고 봅니다.
        None 반환 시 stop_reason에 이유가 남습니다("converged"면 6단계 종료 조건)."""

        self.stop_reason = None

        if self._resetting and current_screen_id == self.root_screen_id:
            # 이미 루트입니다(다른 동작으로 돌아왔거나 시작 화면이 루트). 리셋을 끝냅니다.
            self._arrive_at_root(current_screen_id)
        if self._resetting:
            # 루트로 가는 길을 그래프에서 알면 그 길로 갑니다. 홈 탭 이름을 몰라도, 뒤로가 안 먹는
            # 탭 화면에서도 돌아갈 수 있습니다(고장 난 간선은 빼고 찾습니다).
            self._homing_edge = None
            if self.root_screen_id is not None:
                path = self.graph.bfs_path(current_screen_id, self.root_screen_id, self._extra())
                if path:
                    self._homing_edge = path[0]
                    return StepGoal(
                        kind="reset",
                        action_key=path[0].action_key,
                        expected_target_screen_id=path[0].target,
                    )
            return StepGoal(kind="reset")

        if self._replay_queue:
            edge = self._replay_queue[0]
            return StepGoal(
                kind="replay",
                action_key=edge.action_key,
                expected_target_screen_id=edge.target,
            )

        open_local = self.ledger.frontier_at(current_screen_id)
        if visible_action_keys is None:
            visible_local = open_local
        else:
            visible = set(visible_action_keys)
            visible_local = [key for key in open_local if key in visible]

        # 공통 버튼은 화면별이 아니라 GLOBAL 항목 하나로 관리합니다. 이 화면에 보이는
        # 공통 버튼 중 아직 안 눌러 본 것이 있으면 여기서 누르면 됩니다.
        open_global = self.ledger.frontier_at(GLOBAL_SCREEN_ID)
        visible_global = [key for key in open_global if key in set(visible_global_keys)]

        if visible_local or visible_global:
            return StepGoal(kind="frontier")
        if open_local:
            # 원장에는 남아 있는데 이번 후보엔 안 보입니다. 멀리 이동했다가 다시
            # 돌아오는 것보다 여기서 한 번 더 보는 쪽이 싸서 먼저 재관찰합니다.
            # 연속으로 안 보이면 원장이 정리하므로 무한 반복되지 않습니다.
            return StepGoal(kind="reobserve")

        frontier = {
            screen: keys
            for screen, keys in self.ledger.global_frontier().items()
            if screen != GLOBAL_SCREEN_ID
        }
        # 아직 안 눌러 본 공통 버튼은 그 버튼이 보였던 화면들을 목표로 삼습니다.
        if self.global_screens is not None:
            for key in open_global:
                for screen in self.global_screens(key):
                    if screen not in self._abandoned:
                        frontier.setdefault(screen, []).append(key)
        if not frontier:
            self.stop_reason = "converged"
            return None

        path = self.graph.bfs_nearest_matching(
            current_screen_id,
            lambda node_id: node_id != current_screen_id and bool(frontier.get(node_id)),
            self._extra(),
        )
        if not path:
            # 프론티어는 남아 있지만 지금까지 관찰한 그래프로는 이 화면에서
            # 도달하는 경로를 모릅니다.
            self.stop_reason = "unreachable"
            return None

        self._replay_target = path[-1].target
        self._replay_queue = list(path)
        edge = self._replay_queue[0]
        return StepGoal(
            kind="replay",
            action_key=edge.action_key,
            expected_target_screen_id=edge.target,
        )

    # -- 실행 결과 반영 -------------------------------------------------------

    def report_replay_step(self, executed: bool, arrived_screen_id: str | None) -> None:
        """경로 재생 한 스텝의 결과를 반영합니다. 기대한 화면에 도착했으면 다음
        간선으로 넘어가고, 어긋났으면(로컬 재생 실패) 혼합 전략에 따라 루트
        리셋으로 전환합니다."""

        if not self._replay_queue:
            return

        edge = self._replay_queue[0]
        ok = executed and arrived_screen_id == edge.target
        self.graph.note_edge_result(edge, ok)
        if ok:
            self._replay_queue.pop(0)
            if not self._replay_queue:
                self._replay_target = None
            return

        # 도착 검증 실패: 여기서 남은 경로를 버리고 혼합 전략의 2단계(리셋)로 넘어갑니다.
        target_id = self._replay_target
        self._replay_queue = []
        if target_id is None:
            return

        attempts = self._replan_attempts.get(target_id, 0) + 1
        self._replan_attempts[target_id] = attempts
        if attempts > self.max_replan_attempts:
            self._give_up_on(target_id)
            return

        self._resetting = True
        self._back_taps_used = 0

    def report_reset_step(
        self,
        *,
        executed: bool,
        arrived_screen_id: str | None,
        used_home: bool,
    ) -> None:
        """리셋(홈/뒤로) 한 스텝의 결과를 반영합니다. 루트에 도착했다고 판단되면
        (홈 버튼 탭 성공, 또는 도착 화면이 root_screen_id와 일치) 방금 도착한
        화면에서 목표까지 새로 경로를 짜서 재생을 재개합니다."""

        if self._homing_edge is not None:
            edge, self._homing_edge = self._homing_edge, None
            self.graph.note_edge_result(edge, executed and arrived_screen_id == edge.target)

        target_id = self._replay_target
        reached_root = executed and (
            used_home or arrived_screen_id == self.root_screen_id
        )

        if reached_root:
            self._arrive_at_root(arrived_screen_id)
            return

        self._back_taps_used += 1
        if self._back_taps_used > self.max_back_taps:
            self._resetting = False
            if target_id is not None:
                self._give_up_on(target_id)

    def reopen_failed(self, current_screen_id: str, max_retries: int) -> list[LedgerEntry]:
        """프론티어가 바닥났을 때 실패로 닫힌 항목을 한 번 더 엽니다(원장 retries 상한까지).

        지금 화면에서 가는 길을 아는 항목만 엽니다. 길을 모르는 항목을 열면 "모두 확인" 종료가
        "가는 길을 모름" 종료로 바뀌기만 하므로 그대로 둡니다(이번 run에서 포기한 화면도 제외)."""

        def can_reach(entry: LedgerEntry) -> bool:
            if entry.screen_id == GLOBAL_SCREEN_ID:
                screens = self.global_screens(entry.action_key) if self.global_screens else []
            else:
                screens = [entry.screen_id]
            return any(
                screen not in self._abandoned
                and self.graph.bfs_path(current_screen_id, screen, self._extra()) is not None
                for screen in screens
            )

        return self.ledger.reopen_failed(max_retries, can_reach)

    def give_up(self, screen_ids) -> int:
        """이 화면들의 남은 프론티어를 모두 포기(blocked)합니다. 같은 화면 묶음을 계속 맴돌 때
        navigator가 부릅니다. 반환: 포기한 항목 수."""
        closed = 0
        for screen_id in screen_ids:
            closed += len(self.ledger.frontier_at(screen_id))
            self._give_up_on(screen_id)
        return closed

    # -- 내부 ---------------------------------------------------------------

    def _arrive_at_root(self, arrived_screen_id: str | None) -> None:
        """루트에 도착했습니다. 리셋을 끝내고, 가려던 목표가 있으면 여기서 다시 길을 짭니다."""
        self._resetting = False
        self._homing_edge = None
        target_id = self._replay_target
        if target_id is None or arrived_screen_id is None:
            return
        path = self.graph.bfs_path(arrived_screen_id, target_id, self._extra())
        if path is None:
            # 루트에서도 목표까지 가는 길을 아직 모릅니다. 포기합니다.
            self._give_up_on(target_id)
            return
        self._replay_queue = path

    def _give_up_on(self, target_screen_id: str) -> None:
        """도달 자체가 반복 실패한 화면의 남은 프론티어 항목을 모두 blocked 처리합니다."""
        for action_key in list(self.ledger.frontier_at(target_screen_id)):
            self.ledger.block(target_screen_id, action_key)
        self._abandoned.add(target_screen_id)
        self._replay_queue = []
        self._replay_target = None
        self._resetting = False


def find_reset_button(candidates, *, prefer: Literal["close", "home", "back"]):
    """안전 버튼 후보 중 canonical 라벨이 prefer("close", "home", "back")와 일치하는
    첫 후보를 찾습니다. candidates는 이미 안전 필터를 통과한 목록이어야 합니다."""

    for button in candidates:
        if canonical_action_label(button.label) == prefer:
            return button
    return None
