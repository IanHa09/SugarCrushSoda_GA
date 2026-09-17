"""navigation.frontier의 프론티어 계획·경로 재생·루트 리셋 상태 기계를 검증합니다.

실제 캡처/탭 없이 순수 로직만 테스트합니다: GraphStore/LedgerStore를 임시 폴더에
만들고, FrontierWalker에 직접 결과를 보고하며 decide()가 매번 무엇을 시키는지 봅니다.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.frontier import FrontierWalker
from navigation.graph import GraphStore
from navigation.ledger import LedgerStore


def _make_stores(directory: Path):
    root = Path(directory) / "navigation"
    graph_path = root / "graph.json"
    journey_path = root / "journeys.jsonl"
    image_dir = root / "representatives"
    ledger_path = root / "ledger.json"
    root.mkdir(parents=True)

    graph_patches = (
        patch("navigation.graph.ROOT", root),
        patch("navigation.graph.GRAPH_PATH", graph_path),
        patch("navigation.graph.JOURNEY_PATH", journey_path),
        patch("navigation.graph.IMAGE_DIR", image_dir),
    )
    for p in graph_patches:
        p.start()
    graph = GraphStore()
    ledger = LedgerStore(ledger_path)
    return graph, ledger


class FrontierWalkerLocalTests(unittest.TestCase):
    def test_local_frontier_needs_no_travel(self) -> None:
        """현재 화면에 미시도 항목이 있으면 이동 없이 바로 그 화면에서 고릅니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            ledger.seen("screen_a", "action_shop", "Shop")
            walker = FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

            goal = walker.decide("screen_a")

            self.assertEqual(goal.kind, "frontier")

    def test_no_frontier_anywhere_means_exploration_converged(self) -> None:
        """전역 프론티어가 비어 있으면 decide()가 None을 반환해 탐색 종료를 알립니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            walker = FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

            self.assertIsNone(walker.decide("screen_a"))
            self.assertEqual(walker.stop_reason, "converged")

    def test_unreachable_frontier_is_reported_separately(self) -> None:
        """프론티어가 남았는데 가는 길을 모르면 '완료'가 아니라 'unreachable'입니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            ledger.seen("screen_island", "action_event", "Event")  # 간선 없는 화면
            walker = FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

            self.assertIsNone(walker.decide("screen_a"))
            self.assertEqual(walker.stop_reason, "unreachable")

    def test_invisible_local_frontier_asks_for_reobservation(self) -> None:
        """원장엔 있지만 이번 후보엔 없는 버튼이면 멈추지 않고 재관찰을 요청합니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            ledger.seen("screen_a", "action_event", "Event")
            walker = FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

            self.assertEqual(walker.decide("screen_a", visible_action_keys=[]).kind, "reobserve")
            self.assertEqual(
                walker.decide("screen_a", visible_action_keys=["action_event"]).kind,
                "frontier",
            )


class FrontierWalkerReplayTests(unittest.TestCase):
    def _walker_with_path(self, graph, ledger):
        # A --go_b--> B, B에 미시도 프론티어가 있어 A에서는 원거리 재생이 필요합니다.
        graph.connect("screen_a", "screen_b", "Go to B", "go_b")
        ledger.seen("screen_b", "action_settings", "Settings")
        return FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

    def test_replay_targets_nearest_frontier_and_drains_on_success(self) -> None:
        """원거리 프론티어까지의 경로를 계획하고, 기대한 화면에 도착하면 다음
        스텝에는 그 화면에서 프론티어를 바로 고릅니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            walker = self._walker_with_path(graph, ledger)

            goal = walker.decide("screen_a")
            self.assertEqual(goal.kind, "replay")
            self.assertEqual(goal.action_key, "go_b")
            self.assertEqual(goal.expected_target_screen_id, "screen_b")

            walker.report_replay_step(executed=True, arrived_screen_id="screen_b")
            next_goal = walker.decide("screen_b")
            self.assertEqual(next_goal.kind, "frontier")

    def test_arrival_mismatch_triggers_reset_then_replans_from_new_position(self) -> None:
        """도착 화면이 예상과 다르면(로컬 재생 실패) 리셋으로 전환하고, 리셋이
        끝나면 실제로 도착한 화면에서 목표까지 다시 경로를 짭니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            walker = self._walker_with_path(graph, ledger)

            walker.decide("screen_a")
            # 팝업 때문에 엉뚱한 화면(screen_x)에 도착했다고 가정합니다.
            walker.report_replay_step(executed=True, arrived_screen_id="screen_x")

            reset_goal = walker.decide("screen_x")
            self.assertEqual(reset_goal.kind, "reset")
            self.assertTrue(walker.is_resetting)

            # home 버튼을 눌러 screen_a로 돌아왔다고 보고합니다.
            walker.report_reset_step(
                executed=True, arrived_screen_id="screen_a", used_home=True
            )
            self.assertFalse(walker.is_resetting)

            replay_goal = walker.decide("screen_a")
            self.assertEqual(replay_goal.kind, "replay")
            self.assertEqual(replay_goal.expected_target_screen_id, "screen_b")

    def test_repeated_mismatches_beyond_cap_block_the_target(self) -> None:
        """같은 목표가 재계획 상한을 넘겨 계속 실패하면 blocked 처리하고 포기합니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            walker = self._walker_with_path(graph, ledger)

            # 1번째 실패 -> 리셋 진입
            walker.decide("screen_a")
            walker.report_replay_step(executed=True, arrived_screen_id="screen_x")
            self.assertTrue(walker.is_resetting)
            walker.report_reset_step(
                executed=True, arrived_screen_id="screen_a", used_home=True
            )

            # 2번째(재계획 상한=2) 실패 -> 다시 리셋
            walker.decide("screen_a")
            walker.report_replay_step(executed=True, arrived_screen_id="screen_x")
            self.assertTrue(walker.is_resetting)
            walker.report_reset_step(
                executed=True, arrived_screen_id="screen_a", used_home=True
            )

            # 3번째 실패 -> 상한 초과, 포기(blocked)
            walker.decide("screen_a")
            walker.report_replay_step(executed=True, arrived_screen_id="screen_x")

            self.assertEqual(ledger.frontier_at("screen_b"), [])
            self.assertEqual(
                ledger.ledger.entries["screen_b:action_settings"].verdict, "blocked"
            )

    def test_reset_gives_up_after_exceeding_back_tap_budget(self) -> None:
        """리셋 중 계속 back을 눌러도 루트에 못 돌아오면 budget을 넘긴 뒤 포기합니다."""
        with tempfile.TemporaryDirectory() as directory:
            graph, ledger = _make_stores(directory)
            walker = self._walker_with_path(graph, ledger)
            walker.max_back_taps = 2

            walker.decide("screen_a")
            walker.report_replay_step(executed=True, arrived_screen_id="screen_x")
            self.assertTrue(walker.is_resetting)

            for _ in range(2):
                walker.report_reset_step(
                    executed=True, arrived_screen_id="screen_y", used_home=False
                )
            self.assertTrue(walker.is_resetting)

            # budget(2) 초과 -> 포기
            walker.report_reset_step(
                executed=True, arrived_screen_id="screen_y", used_home=False
            )
            self.assertFalse(walker.is_resetting)
            self.assertEqual(ledger.frontier_at("screen_b"), [])


if __name__ == "__main__":
    unittest.main()
