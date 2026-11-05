"""지표·클릭 위치 도구가 마지막에 고른 게임의 결과 폴더(output/<slug>)를 찾는지."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import game_select
from game_knowledge import GameEntry
from survey_metrics import game_root

GAMES = {
    "candy crush soda saga": GameEntry("Candy Crush Soda Saga", "match3", "com.king.candycrushsodasaga"),
    "royal match": GameEntry("Royal Match", "match3", "com.dreamgames.royalmatch"),
}


class GameRootTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pointer = Path(directory.name) / "current_game.txt"
        for target, value in (("CURRENT_GAME_PATH", self.pointer), ("load_games", lambda: GAMES)):
            patcher = patch.object(game_select, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_follows_last_selected_game(self) -> None:
        self.pointer.write_text("royal match", encoding="utf-8")
        self.assertEqual(game_root().name, "royal_match")

    def test_first_game_when_nothing_selected_yet(self) -> None:
        self.assertEqual(game_root().name, "candy_crush_soda_saga")

    def test_first_game_when_selected_game_was_removed(self) -> None:
        self.pointer.write_text("Removed Game", encoding="utf-8")
        self.assertEqual(game_root().name, "candy_crush_soda_saga")


if __name__ == "__main__":
    unittest.main()
