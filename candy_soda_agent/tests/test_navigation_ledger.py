"""navigation.ledger의 원장(시도/판정) 로직을 검증하는 테스트입니다."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from navigation.ledger import LedgerStore


class LedgerStoreTests(unittest.TestCase):
    def test_seen_adds_untried_entry_once(self) -> None:
        """처음 본 (화면,행동)만 프론티어에 0회 시도로 추가되고, 다시 봐도 안 덮어씁니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_shop", "Shop")
            store.mark_attempted("screen_a", "action_shop", "Shop")
            store.seen("screen_a", "action_shop", "Shop")  # 재관찰해도 시도 이력 유지

            self.assertNotIn("action_shop", store.frontier_at("screen_a"))

    def test_unresponsive_button_never_returns_to_frontier(self) -> None:
        """눌러도 화면이 안 바뀌는(no_change) 버튼은 attempts>0이라 다시 안 뽑힙니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_mute", "Mute")
            self.assertIn("action_mute", store.frontier_at("screen_a"))

            store.mark_attempted("screen_a", "action_mute", "Mute")
            store.finalize("screen_a", "action_mute", "no_change", target_screen_id="screen_a")

            self.assertNotIn("action_mute", store.frontier_at("screen_a"))

    def test_global_frontier_covers_every_untried_screen(self) -> None:
        """전역 프론티어는 시도하지 않은 모든 화면의 항목을 모읍니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_shop", "Shop")
            store.seen("screen_b", "action_settings", "Settings")
            store.mark_attempted("screen_a", "action_shop", "Shop")
            store.finalize("screen_a", "action_shop", "new_screen", target_screen_id="screen_b")

            frontier = store.global_frontier()
            self.assertNotIn("screen_a", frontier)
            self.assertEqual(frontier["screen_b"], ["action_settings"])

    def test_block_removes_target_from_frontier_permanently(self) -> None:
        """도달 불가로 포기한 항목은 블록되어 프론티어에서 빠집니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_c", "action_event", "Event")
            store.block("screen_c", "action_event")

            self.assertEqual(store.frontier_at("screen_c"), [])
            self.assertEqual(
                store.ledger.entries["screen_c:action_event"].verdict, "blocked"
            )

    def test_crash_mid_step_is_swept_back_into_frontier_on_reload(self) -> None:
        """attempts>0인데 verdict가 없으면(직전 실행이 결과 확인 전에 끊김)
        다음 로드 때 다시 프론티어로 돌아옵니다."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            store = LedgerStore(path)
            store.seen("screen_a", "action_shop", "Shop")
            store.mark_attempted("screen_a", "action_shop", "Shop")  # finalize 없이 종료

            reloaded = LedgerStore(path)
            self.assertIn("action_shop", reloaded.frontier_at("screen_a"))

    def test_coverage_counts_attempted_over_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_shop", "Shop")
            store.seen("screen_a", "action_settings", "Settings")
            store.mark_attempted("screen_a", "action_shop", "Shop")
            store.finalize("screen_a", "action_shop", "known_screen", target_screen_id="screen_b")

            self.assertEqual(store.coverage(), (1, 2))


class LedgerVisibilityTests(unittest.TestCase):
    """2번 수정: 한 번 본 버튼이 다시 후보에 안 나오는 경우."""

    def test_missing_entry_is_retired_after_consecutive_misses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_event", "Event")

            self.assertEqual(store.note_visit("screen_a", [], max_misses=2), [])
            self.assertIn("action_event", store.frontier_at("screen_a"))  # 1회는 유지

            retired = store.note_visit("screen_a", [], max_misses=2)
            self.assertEqual(retired, ["action_event"])
            self.assertEqual(store.frontier_at("screen_a"), [])
            entry = store.ledger.entries["screen_a:action_event"]
            self.assertEqual(entry.verdict, "not_visible")
            self.assertEqual(entry.attempts, 0)  # 누른 적은 없음

    def test_seeing_the_button_again_resets_the_miss_count(self) -> None:
        """'연속'으로 안 보일 때만 정리합니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_event", "Event")
            store.note_visit("screen_a", [], max_misses=2)
            store.note_visit("screen_a", ["action_event"], max_misses=2)
            store.note_visit("screen_a", [], max_misses=2)

            self.assertIn("action_event", store.frontier_at("screen_a"))

    def test_other_screens_and_tried_entries_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_shop", "Shop")
            store.mark_attempted("screen_a", "action_shop", "Shop")
            store.finalize("screen_a", "action_shop", "new_screen", target_screen_id="screen_b")
            store.seen("screen_b", "action_event", "Event")

            store.note_visit("screen_a", [], max_misses=1)

            self.assertEqual(store.ledger.entries["screen_a:action_shop"].verdict, "new_screen")
            self.assertIn("action_event", store.frontier_at("screen_b"))

    def test_closed_entries_do_not_count_as_tried(self) -> None:
        """blocked/not_visible은 누른 적이 없으니 커버리지의 '눌러본 것'에서 빠집니다."""
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            store.seen("screen_a", "action_event", "Event")
            store.seen("screen_a", "action_ad", "Watch Ad")
            store.note_visit("screen_a", ["action_ad"], max_misses=1)  # event 정리
            store.block("screen_a", "action_ad")

            self.assertEqual(store.coverage(), (0, 2))
            self.assertEqual(store.closed_without_tap(), 2)
            self.assertTrue(store.is_frontier_empty())


if __name__ == "__main__":
    unittest.main()
