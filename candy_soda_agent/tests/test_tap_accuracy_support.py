"""클릭 정확도 작업의 보조 기능 테스트: 원장(missed/not_found), 지표, CLI, ESC 가드."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from cli import build_run_plan
from navigation.ledger import LedgerStore, is_open_entry
from survey_metrics import compute_session_metrics, graph_summary, render_markdown


class LedgerMissTests(unittest.TestCase):
    def test_miss_is_retried_then_closed_as_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.seen("a", "k", "Shop")
            ledger.mark_attempted("a", "k", "Shop")
            self.assertEqual(ledger.record_miss("a", "k", max_retries=1), "missed")
            self.assertEqual(ledger.frontier_at("a"), ["k"])  # 다시 시도 후보

            ledger.mark_attempted("a", "k", "Shop")
            self.assertEqual(ledger.record_miss("a", "k", max_retries=1), "no_change")
            self.assertEqual(ledger.frontier_at("a"), [])
            self.assertEqual(ledger.coverage(), (1, 1))

    def test_zero_retries_closes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.mark_attempted("a", "k", "Shop")
            self.assertEqual(ledger.record_miss("a", "k", max_retries=0), "no_change")

    def test_not_found_closes_without_counting_a_tap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.seen("a", "k", "Ghost")
            ledger.close_not_found("a", "k")
            entry = ledger.ledger.entries["a:k"]
            self.assertEqual(entry.verdict, "not_found")
            self.assertFalse(is_open_entry(entry))
            self.assertEqual(ledger.coverage(), (0, 1))
            self.assertEqual(ledger.closed_without_tap(), 1)

            # 저장 후 다시 읽어도 유지됩니다(스키마 Literal에 들어 있어야 함).
            reloaded = LedgerStore(Path(directory) / "ledger.json")
            self.assertEqual(reloaded.ledger.entries["a:k"].verdict, "not_found")

    def test_missed_entry_that_is_not_found_later_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = LedgerStore(Path(directory) / "ledger.json")
            ledger.mark_attempted("a", "k", "Shop")
            ledger.record_miss("a", "k", max_retries=1)
            ledger.close_not_found("a", "k")
            self.assertEqual(ledger.frontier_at("a"), [])


def _record(session: str, step: int, screen: str, action: dict, raw: str = "") -> dict:
    return {
        "session_id": session,
        "timestamp": f"{session}T{step:02d}",
        "step": step,
        "mode": "autodrive",
        "screen_id": screen,
        "raw_image": raw,
        "action": action,
    }


def _tap(label: str, changed: bool | None = None, refine: dict | None = None) -> dict:
    action = {"type": "tap_button", "executed": True, "outcome": "tap_executed",
              "button": {"label": label}}
    if changed is not None:
        action["tap_effect"] = {"changed": changed}
    if refine is not None:
        action["refine"] = refine
    return action


class MetricsTests(unittest.TestCase):
    def test_sessions_hit_rate_and_cumulative_growth(self) -> None:
        records = [
            # 세션 1: 탭 3번 중 2번 화면 바뀜(tap_effect 기준)
            _record("s1", 1, "A", _tap("Shop", True,
                                       {"found": True, "shift_px": 90.0})),
            _record("s1", 2, "B", _tap("Close", True,
                                       {"found": True, "shift_px": 10.0})),
            _record("s1", 3, "A", _tap("!", False, {"found": False})),
            _record("s1", 4, "A", {"type": "record_only", "outcome": "frontier_unreachable"}),
            # 세션 2: 새 화면 하나, 뒤로 키 1번
            _record("s2", 1, "A", _tap("Settings", True)),
            _record("s2", 2, "C", {"type": "back_key", "executed": True,
                                   "button": {"label": "뒤로(키)"},
                                   "tap_effect": {"changed": True}}),
            _record("s2", 3, "A", {"type": "record_only", "outcome": "frontier_converged"}),
        ]
        first, second = compute_session_metrics(records, image_loader=None)

        self.assertEqual((first.taps, first.taps_changed, first.taps_unchanged), (3, 2, 1))
        self.assertAlmostEqual(first.hit_rate, 2 / 3, places=3)
        self.assertEqual((first.refine_found, first.refine_not_found), (2, 1))
        self.assertEqual(first.refine_median_shift_px, 50.0)
        self.assertEqual((first.cum_screens, first.cum_transitions), (2, 2))
        self.assertEqual(first.stop, "unreachable")

        self.assertEqual(second.back_keys, 1)
        self.assertEqual((second.new_screens, second.cum_screens), (1, 3))
        self.assertEqual((second.new_transitions, second.cum_transitions), (2, 4))
        self.assertEqual(second.stop, "converged")
        self.assertIn("| 1 |", render_markdown("run_x", [first, second], {}))

    def test_old_records_use_pixels_then_screen_id(self) -> None:
        images = {
            "a1": np.zeros((8, 8, 3), np.uint8),
            "a2": np.zeros((8, 8, 3), np.uint8),
            "b": np.full((8, 8, 3), 200, np.uint8),
        }
        records = [
            _record("s1", 1, "A", _tap("!"), raw="a1"),
            _record("s1", 2, "A", _tap("Shop"), raw="a2"),  # 픽셀 그대로 -> 빗나감
            _record("s1", 3, "B", _tap("Close"), raw="missing"),  # 이미지 없음 -> screen_id
            _record("s1", 4, "A", {"type": "record_only", "outcome": "x"}, raw="a1"),
        ]
        (metrics,) = compute_session_metrics(records, image_loader=images.get)
        self.assertEqual(metrics.taps, 3)
        # a1->a2 그대로, a2->b 바뀜, b->(이미지 없음) screen_id B->A 바뀜
        self.assertEqual((metrics.taps_changed, metrics.taps_unchanged), (2, 1))
        self.assertEqual(metrics.change_source, "pixels+screen_id")

    def test_graph_summary_counts_self_loops_and_unreached(self) -> None:
        graph = {
            "nodes": {"A": {"screen_type": "map"}, "B": {"screen_type": "shop"},
                      "C": {"screen_type": "shop"}},
            "edges": [{"source": "A", "target": "B"}, {"source": "A", "target": "A"}],
        }
        ledger = {"entries": {"A:k": {"attempts": 1, "verdict": "new_screen"},
                              "A:j": {"attempts": 0, "verdict": None}}}
        summary = graph_summary(graph, ledger)
        self.assertEqual(summary["edges"], 1)
        self.assertEqual(summary["self_loop_edges"], 1)
        self.assertEqual(summary["unreached_from_root"], 1)
        self.assertEqual(summary["coverage"], "1/2")


def _args(**overrides) -> argparse.Namespace:
    values = dict(
        once=False, auto=False, survey_once=False, survey_auto=False,
        survey_report=False, survey_taps=True, hotkeys=False, autodrive=False,
        autodrive_steps=40, autodrive_repeat=1, autodrive_pause=3.0,
        new_run=False, survey_metrics=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class CliTests(unittest.TestCase):
    def test_repeat_requires_autodrive(self) -> None:
        with self.assertRaises(ValueError):
            build_run_plan(_args(autodrive_repeat=3))
        plan = build_run_plan(_args(autodrive=True, autodrive_repeat=3))
        self.assertTrue(plan.autodrive_mode)

    def test_invalid_repeat_and_metrics_combo(self) -> None:
        with self.assertRaises(ValueError):
            build_run_plan(_args(autodrive=True, autodrive_repeat=0))
        with self.assertRaises(ValueError):
            build_run_plan(_args(autodrive=True, survey_metrics=True))


class HotkeyGuardTests(unittest.TestCase):
    def test_program_sent_esc_does_not_request_exit(self) -> None:
        import hotkeys

        hotkeys.EXIT_REQUESTED.clear()
        hotkeys.SYNTHETIC_KEY_ACTIVE.set()
        try:
            hotkeys.request_exit()
            self.assertFalse(hotkeys.EXIT_REQUESTED.is_set())
        finally:
            hotkeys.SYNTHETIC_KEY_ACTIVE.clear()
        hotkeys.request_exit()
        self.assertTrue(hotkeys.EXIT_REQUESTED.is_set())
        hotkeys.EXIT_REQUESTED.clear()


if __name__ == "__main__":
    unittest.main()
