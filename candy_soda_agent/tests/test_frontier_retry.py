"""실패로 닫힌 프론티어 항목을 "모두 확인" 직전에 다시 여는 로직(재시도 상한 포함)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.frontier import FrontierWalker
from navigation.graph import GraphStore
from navigation.ledger import GLOBAL_SCREEN_ID, LedgerStore


def _close(store: LedgerStore, screen: str, key: str, verdict: str) -> None:
    store.seen(screen, key, key.title())
    if verdict in {"no_change", "new_screen"}:
        store.mark_attempted(screen, key, key.title())
        store.finalize(screen, key, verdict, target_screen_id=screen)
    elif verdict == "not_found":
        store.close_not_found(screen, key)
    elif verdict == "blocked":
        store.block(screen, key)
    else:
        store.ledger.entries[f"{screen}:{key}"].verdict = verdict


class ReopenFailedTests(unittest.TestCase):
    def test_only_failed_verdicts_reopen_and_budget_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            store = LedgerStore(path)
            for key, verdict in [
                ("mute", "no_change"), ("event", "not_found"), ("pass", "blocked"),
                ("shop", "new_screen"), ("map", "global"), ("play", "play_entry"),
                ("ghost", "not_visible"),
            ]:
                _close(store, "screen_a", key, verdict)

            self.assertEqual(store.failed_count(), 4)  # no_change·not_found·blocked·not_visible
            reopened = store.reopen_failed(max_retries=1)
            self.assertEqual({e.action_key for e in reopened}, {"mute", "event", "pass"})
            self.assertEqual(sorted(store.frontier_at("screen_a")), ["event", "mute", "pass"])
            self.assertEqual(store.ledger.entries["screen_a:mute"].attempts, 0)

            # 다시 실패하면 상한을 다 써서 더는 열리지 않습니다(원장에 저장되어 다음 run도 동일).
            _close(store, "screen_a", "mute", "no_change")
            self.assertEqual(LedgerStore(path).reopen_failed(max_retries=1), [])

    def test_zero_budget_and_filter_reopen_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            _close(store, "screen_a", "mute", "no_change")
            self.assertEqual(store.reopen_failed(max_retries=0), [])
            self.assertEqual(store.reopen_failed(1, can_reach=lambda entry: False), [])
            self.assertEqual(store.ledger.entries["screen_a:mute"].retries, 0)  # 횟수도 안 씀


@contextmanager
def _stores():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "navigation"
        with (
            patch("navigation.graph.ROOT", root),
            patch("navigation.graph.GRAPH_PATH", root / "graph.json"),
            patch("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
            patch("navigation.graph.IMAGE_DIR", root / "representatives"),
        ):
            yield GraphStore(), LedgerStore(root / "ledger.json")


class WalkerReopenTests(unittest.TestCase):
    def test_reachable_failed_item_becomes_frontier_again(self) -> None:
        with _stores() as (graph, ledger):
            graph.connect("screen_a", "screen_b", "Go to B", "go_b")
            _close(ledger, "screen_b", "settings", "no_change")
            _close(ledger, "screen_island", "event", "not_found")  # 가는 길을 모름
            walker = FrontierWalker(graph, ledger, max_back_taps=3, max_replan_attempts=2)

            self.assertIsNone(walker.decide("screen_a"))
            self.assertEqual(walker.stop_reason, "converged")
            reopened = walker.reopen_failed("screen_a", max_retries=1)

            # 길을 모르는 항목은 열지 않아 "모두 확인"이 "길을 모름" 종료로 바뀌지 않습니다.
            self.assertEqual([e.action_key for e in reopened], ["settings"])
            goal = walker.decide("screen_a")
            self.assertEqual((goal.kind, goal.expected_target_screen_id), ("replay", "screen_b"))

    def test_global_item_needs_a_reachable_screen_where_it_was_seen(self) -> None:
        with _stores() as (graph, ledger):
            _close(ledger, GLOBAL_SCREEN_ID, "nav_shop", "no_change")
            seen_on = {"nav_shop": ["screen_island"]}
            walker = FrontierWalker(
                graph, ledger, max_back_taps=3, max_replan_attempts=2,
                global_screens=lambda key: seen_on.get(key, []),
            )
            self.assertEqual(walker.reopen_failed("screen_a", max_retries=1), [])
            seen_on["nav_shop"].append("screen_a")  # 지금 화면에서도 보였음
            self.assertEqual(len(walker.reopen_failed("screen_a", max_retries=1)), 1)


if __name__ == "__main__":
    unittest.main()
