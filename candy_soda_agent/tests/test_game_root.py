"""지표·클릭 위치 도구가 GAME_SLUG의 결과 폴더(output/<GAME_SLUG>)를 찾는지."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import survey_metrics
from survey_metrics import DEFAULT_GAME_SLUG, game_root


class GameRootTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_path = Path(directory.name) / ".env"

    def _root(self, environ: dict) -> Path:
        with patch.dict(os.environ, environ, clear=False), patch.object(survey_metrics, "ENV_PATH", self.env_path):
            if "GAME_SLUG" not in environ:
                os.environ.pop("GAME_SLUG", None)
            return game_root()

    def test_environment_variable_wins(self) -> None:
        self.env_path.write_text("GAME_SLUG=from_file\n", encoding="utf-8")
        self.assertEqual(self._root({"GAME_SLUG": "royal_match"}).name, "royal_match")

    def test_env_file_then_default(self) -> None:
        self.assertEqual(self._root({}).name, DEFAULT_GAME_SLUG)
        try:
            import dotenv  # noqa: F401
        except ImportError:
            self.skipTest("python-dotenv 없음")
        self.env_path.write_text("GAME_SLUG=toon_blast\n", encoding="utf-8")
        self.assertEqual(self._root({}).name, "toon_blast")
        self.assertEqual(self._root({}).parent.name, "output")


if __name__ == "__main__":
    unittest.main()
