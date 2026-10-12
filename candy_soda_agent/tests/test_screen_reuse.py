"""아는 화면이면 LLM 분석을 건너뛰고 처음 분석한 결과를 다시 쓰는지 검증합니다."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from navigation.graph import GraphStore
from navigation.navigator import run_autodrive
from schemas import SurveyButtonCandidate, SurveyDecision
from tap_effect import TapEffect
from test_screen_index import _popup_over, _screen


def _decision(screen_type: str, *labels: str) -> SurveyDecision:
    return SurveyDecision(
        screen_type=screen_type,
        button_candidates=[
            SurveyButtonCandidate(label=label, role="structure", center={"x": 0.5, "y": 0.5}, confidence=0.9)
            for label in labels
        ],
    )


def _banner(image: np.ndarray, height: int) -> np.ndarray:
    """위쪽에 띠를 덮은 같은 화면(높이 40이면 HOG 거리 약 0.11: 같은 노드지만 재사용 기준 밖)."""
    image = image.copy()
    cv2.rectangle(image, (20, 20), (220, 20 + height), (245, 240, 230), -1)
    return image


def _paths(root: Path):
    return (
        ("navigation.graph.ROOT", root),
        ("navigation.graph.GRAPH_PATH", root / "graph.json"),
        ("navigation.graph.JOURNEY_PATH", root / "journeys.jsonl"),
        ("navigation.graph.IMAGE_DIR", root / "representatives"),
        ("navigation.ledger.NAVIGATION_LEDGER_PATH", root / "ledger.json"),
        ("config.NAVIGATION_UI_CHROME_PATH", root / "ui_chrome.json"),
    )


@contextmanager
def _store(reuse: float = 0.10):
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        for target, value in _paths(Path(directory) / "navigation"):
            stack.enter_context(patch(target, value))
        stack.enter_context(patch("navigation.graph.SCREEN_REUSE_MAX_DISTANCE", reuse))
        yield GraphStore()


class DecisionCacheTests(unittest.TestCase):
    def test_first_analysis_is_kept(self) -> None:
        with _store() as store:
            store.save_decision("home", _decision("home", "Shop"))
            store.save_decision("home", _decision("home", "Shop icon", "Event"))  # 다시 분석해도 기준은 그대로
            self.assertEqual([b.label for b in store.load_decision("home").button_candidates], ["Shop"])
            self.assertIsNone(store.load_decision("unknown_node"))

    def test_unreadable_file_is_ignored_and_replaced(self) -> None:
        with _store() as store:
            path = store._decision_path("home")
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(store.load_decision("home"))
            store.save_decision("home", _decision("home", "Shop"))
            self.assertEqual(store.load_decision("home").screen_type, "home")


class KnownDecisionTests(unittest.TestCase):
    def _home(self, store: GraphStore) -> str:
        node = store.observe(_decision("home", "Shop"), _screen("map"))
        store.save_decision(node.id, _decision("home", "Shop"))
        return node.id

    def test_near_identical_screen_reuses_the_saved_analysis(self) -> None:
        with _store() as store:
            home = self._home(store)
            text = _screen("map")
            cv2.putText(text, "12", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)  # 숫자만 바뀜
            node_id, decision = store.known_decision(text)
            self.assertEqual(node_id, home)
            self.assertEqual(decision.button_candidates[0].label, "Shop")

    def test_next_session_still_reuses_it(self) -> None:
        with _store() as store:
            home = self._home(store)
            fresh = GraphStore()  # autodrive 재실행: 색인은 대표 이미지, 분석은 decisions/에서 다시 읽음
            self.assertEqual(fresh.known_decision(_screen("map"))[0], home)

    def test_other_or_uncertain_screens_are_analyzed_again(self) -> None:
        with _store() as store:
            self._home(store)
            self.assertIsNone(store.known_decision(_screen("shop")))
            self.assertIsNone(store.known_decision(_popup_over(_screen("map"))))
            # 0.10~0.15: 같은 노드로는 붙지만(observe) 분석을 건너뛸 만큼 확실하지는 않습니다.
            uncertain = _banner(_screen("map"), 40)
            self.assertIsNone(store.known_decision(uncertain))
            self.assertEqual(store.observe(_decision("home"), uncertain).visits, 2)

    def test_same_pixels_use_that_node_and_zero_turns_reuse_off(self) -> None:
        with _store() as store:
            home = self._home(store)
            self.assertEqual(store.known_decision(None, same_node=home)[0], home)
            self.assertIsNone(store.known_decision(_screen("shop"), same_node="no_cache_node"))
        with _store(reuse=0.0) as store:
            home = self._home(store)
            self.assertIsNone(store.known_decision(_screen("map")))
            self.assertIsNone(store.known_decision(None, same_node=home))


def _bundle(image: np.ndarray) -> SimpleNamespace:
    height, width = image.shape[:2]
    return SimpleNamespace(full_image=image, full_region={"left": 0, "top": 0, "width": width, "height": height})


EFFECT = TapEffect(changed=True, final_change=0.5, peak_change=0.5, settled=True, waited=0.0, frames=1)


class AutodriveReuseTests(unittest.TestCase):
    """지도(Shop) -> 상점(Home) -> 지도. 세 번째 관찰은 대표 화면과 같아 분석을 건너뜁니다."""

    def _run(self, decisions, reuse: float = 0.10):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "navigation"
        captures = [_bundle(_screen("map")), _bundle(_screen("shop")), _bundle(_screen("map"))]
        with ExitStack() as stack:
            for target, value in _paths(root) + (
                ("navigation.graph.SCREEN_REUSE_MAX_DISTANCE", reuse),
                ("navigation.navigator.CAPTURE_MODE", "window"),
                ("navigation.navigator.DRY_RUN", False),
                ("navigation.navigator.TAP_REFINE", False),
                ("navigation.navigator.BACK_KEY", ""),
                ("navigation.navigator.ADB_PATH", ""),
                ("navigation.navigator.CHROME_DETECTION", False),
            ):
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("navigation.navigator.time.sleep"))
            stack.enter_context(patch("navigation.navigator.capture_bundle", side_effect=captures))
            analyze = stack.enter_context(patch("navigation.navigator.analyze_survey_screen", side_effect=decisions))
            stack.enter_context(patch("navigation.navigator.load_recent_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.load_all_survey_records", return_value=[]))
            stack.enter_context(patch("navigation.navigator.wait_for_settle", return_value=EFFECT))
            stack.enter_context(patch("navigation.navigator.execute_button_tap", return_value=True))
            record = stack.enter_context(patch("navigation.navigator.record_survey_screen"))
            report = stack.enter_context(patch("navigation.navigator.write_survey_report", return_value=Path("r.md")))
            stack.enter_context(patch("navigation.navigator.mss.MSS")).return_value.__enter__.return_value = object()
            summary = run_autodrive(object(), max_steps=3, allow_taps=True)
            journeys = [json.loads(line) for line in (root / "journeys.jsonl").read_text(encoding="utf-8").splitlines()]
        return SimpleNamespace(
            summary=summary, analyze=analyze, record=record, journeys=journeys,
            graph=report.call_args.kwargs["graph"],
        )

    def test_revisited_screen_skips_the_llm(self) -> None:
        home, shop = _decision("home", "Shop"), _decision("shop", "Home")
        result = self._run([home, shop])  # LLM 응답은 두 번뿐: 세 번째에 부르면 StopIteration

        self.assertEqual(result.analyze.call_count, 2)
        self.assertEqual(result.summary.stop_kind, "converged")
        self.assertEqual(len(result.graph.nodes), 2)
        self.assertEqual([j["reused_analysis"] for j in result.journeys], [False, False, True])
        # 분석을 건너뛴 기록은 캡처를 새로 저장하지 않고 대표 이미지를 가리킵니다.
        image_paths = [c.kwargs["image_path"] for c in result.record.call_args_list]
        self.assertEqual(image_paths[:2], [None, None])
        reused_image = Path(image_paths[2])
        self.assertEqual(reused_image.parent.name, "representatives")
        self.assertEqual(reused_image.name, f"{result.journeys[2]['screen_id']}.png")

    def test_reuse_off_analyzes_every_step(self) -> None:
        home, shop = _decision("home", "Shop"), _decision("shop", "Home")
        result = self._run([home, shop, home], reuse=0.0)
        self.assertEqual(result.analyze.call_count, 3)
        self.assertFalse(any(j["reused_analysis"] for j in result.journeys))


class SurveyRecordImageTests(unittest.TestCase):
    def test_given_image_path_is_recorded_without_saving_a_capture(self) -> None:
        import storage

        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory) / "captures"
            with (
                patch.object(storage, "SURVEY_LOG_PATH", Path(directory) / "survey.jsonl"),
                patch.object(storage, "CAPTURE_DIR", capture_dir),
            ):
                record = storage.save_survey_record(
                    session_id="s", step=1, mode="autodrive", raw_image=_screen("map"), grid_image=None,
                    decision=_decision("home"), stored_image_scope="window", screen_fingerprint="",
                    recent_records=[], image_path="representatives/home.png",
                )
            self.assertEqual(record["raw_image"], "representatives/home.png")
            self.assertFalse(capture_dir.exists())


if __name__ == "__main__":
    unittest.main()
