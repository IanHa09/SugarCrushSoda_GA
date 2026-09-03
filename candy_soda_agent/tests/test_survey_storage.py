"""survey 기록 저장 시 중복 요소 처리를 검증하는 테스트입니다."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("cv2", "numpy", "pydantic")
)


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "survey storage dependencies are not installed")
class SurveyStorageTests(unittest.TestCase):
    def test_survey_record_marks_duplicate_elements(self) -> None:
        """동일한 화면을 다시 기록하면 중복 요소로 표시되는지 검증합니다."""
        import numpy as np

        import storage
        from schemas import SurveyDecision, SurveyElement

        image = np.zeros((16, 16, 3), dtype=np.uint8)
        decision = SurveyDecision(
            screen_type="level_complete",
            summary="Level clear screen",
            visible_text=["Level 9", "Next"],
            game_elements=[
                SurveyElement(category="reward", name="Three Stars"),
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            survey_path = Path(directory) / "survey.jsonl"
            capture_dir = Path(directory) / "captures"
            with (
                patch.object(storage, "SURVEY_LOG_PATH", survey_path),
                patch.object(storage, "CAPTURE_DIR", capture_dir),
            ):
                first = storage.save_survey_record(
                    session_id="session",
                    step=1,
                    mode="manual_survey",
                    raw_image=image,
                    grid_image=None,
                    decision=decision,
                    stored_image_scope="window",
                    screen_fingerprint="abc",
                    recent_records=[],
                )
                second = storage.save_survey_record(
                    session_id="session",
                    step=2,
                    mode="manual_survey",
                    raw_image=image,
                    grid_image=None,
                    decision=decision,
                    stored_image_scope="window",
                    screen_fingerprint="abc",
                    recent_records=[first],
                )

        self.assertFalse(first["dedupe"]["is_duplicate_screen"])
        self.assertEqual(len(first["dedupe"]["new_element_keys"]), 1)
        self.assertTrue(second["dedupe"]["is_duplicate_screen"])
        self.assertEqual(len(second["dedupe"]["duplicate_element_keys"]), 1)
        self.assertTrue(second["elements"][0]["is_duplicate"])


if __name__ == "__main__":
    unittest.main()
