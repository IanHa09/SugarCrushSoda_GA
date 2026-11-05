"""실행할 게임 고르기: --game → adb 감지 → 목록 선택, 그리고 current_game.txt 기록."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

import game_select
from game_knowledge import GameEntry

SODA = GameEntry("Candy Crush Soda Saga", "match3", "com.king.candycrushsodasaga")
ROYAL = GameEntry("Royal Match", "match3", "com.dreamgames.royalmatch")
GAMES = {"candy crush soda saga": SODA, "royal match": ROYAL}

# LDPlayer(Android 9)와 Android 12 이상에서 보이는 포커스 줄 형태입니다.
DUMPSYS_FOCUS = (
    "  mCurrentFocus=Window{1a2b3c u0 com.dreamgames.royalmatch/com.dreamgames.royalmatch.MainActivity}\n"
    "  mFocusedApp=AppWindowToken{4d5e token=Token{6f ActivityRecord{7a u0 com.dreamgames.royalmatch/.MainActivity t12}}}\n"
)
DUMPSYS_POPUP_FOCUS = (
    "  mCurrentFocus=null\n"
    "  mFocusedApp=ActivityRecord{7a u0 com.king.candycrushsodasaga/.CandyActivity t3}\n"
)


def _adb_output(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class SelectGameTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pointer = Path(directory.name) / "output" / "current_game.txt"
        patches = [
            patch.object(game_select, "CURRENT_GAME_PATH", self.pointer),
            patch.object(game_select, "load_games", lambda: GAMES),
            patch.dict(os.environ, {"ADB_PATH": "adb", "ADB_SERIAL": ""}),
            patch("sys.stdout", new_callable=io.StringIO),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        for key in ("GAME_TITLE", "GAME_SLUG", "GAME_PACKAGE"):
            os.environ.pop(key, None)

    def _select(self, argv: list[str], adb_stdout: str = "", stdin=None) -> GameEntry:
        with patch.object(game_select.subprocess, "run", return_value=_adb_output(adb_stdout)) as run, \
                patch("sys.stdin", stdin if stdin is not None else io.StringIO()):
            entry = game_select.select_game(argv)
        self.adb_calls = run.call_count
        return entry

    def test_game_option_wins_and_skips_adb(self) -> None:
        self.assertEqual(self._select(["--autodrive", "--game", "royal match"], DUMPSYS_POPUP_FOCUS), ROYAL)
        self.assertEqual(self._select(["--game=Candy Crush Soda Saga"]), SODA)
        self.assertEqual(self.adb_calls, 0)
        self.assertEqual(self.pointer.read_text(encoding="utf-8"), "Candy Crush Soda Saga")

    def test_unknown_game_option_stops(self) -> None:
        with self.assertRaisesRegex(SystemExit, "games.txt에 없는 게임"):
            self._select(["--game", "Unknown"])

    def test_adb_detects_foreground_game(self) -> None:
        self.assertEqual(self._select(["--autodrive"], DUMPSYS_FOCUS), ROYAL)
        self.assertEqual(self.pointer.read_text(encoding="utf-8"), "Royal Match")
        self.assertIn("adb 감지", sys.stdout.getvalue())

    def test_adb_uses_focused_app_when_popup_has_focus(self) -> None:
        self.assertEqual(self._select([], DUMPSYS_POPUP_FOCUS), SODA)

    def test_unknown_foreground_app_falls_back_to_last_game_without_tty(self) -> None:
        self.pointer.parent.mkdir(parents=True)
        self.pointer.write_text("Royal Match", encoding="utf-8")
        output = "  mCurrentFocus=Window{1 u0 com.android.launcher3/.Launcher}\n"
        self.assertEqual(self._select([], output), ROYAL)
        self.assertIn("com.android.launcher3는 games.txt에 없습니다", sys.stdout.getvalue())

    def test_menu_choice_and_enter_keeps_last_game(self) -> None:
        with patch("builtins.input", side_effect=["9", "2"]):
            self.assertEqual(self._select([], "", stdin=_Tty()), ROYAL)
        with patch("builtins.input", side_effect=[""]):
            self.assertEqual(self._select([], "", stdin=_Tty()), ROYAL)

    def test_without_adb_path_asks(self) -> None:
        os.environ["ADB_PATH"] = ""
        with patch("builtins.input", side_effect=["1"]):
            self.assertEqual(self._select([], DUMPSYS_FOCUS, stdin=_Tty()), SODA)
        self.assertEqual(self.adb_calls, 0)

    def test_adb_failure_asks(self) -> None:
        with patch.object(game_select.subprocess, "run", side_effect=OSError("no adb")), \
                patch("sys.stdin", io.StringIO()):
            self.assertEqual(game_select.select_game([]), SODA)

    def test_warns_about_old_env_keys(self) -> None:
        os.environ["GAME_SLUG"] = "candy_crush_soda"
        self._select(["--game", "Royal Match"])
        self.assertIn(".env의 GAME_SLUG는 이제 쓰지 않습니다", sys.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
