"""adb로 Android 뒤로를 보내는 기능 테스트(실제 adb는 실행하지 않습니다)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from action_executor import back_method, check_adb, execute_back_key
from navigation.navigator import run_autodrive


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class AdbBackTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.adb = str(Path(directory.name) / "adb.exe")
        Path(self.adb).write_text("", encoding="utf-8")  # 실행 파일 자리만 만듭니다.

    def test_back_method_prefers_adb(self) -> None:
        self.assertEqual(back_method(self.adb, "esc"), "adb")
        self.assertEqual(back_method("", "esc"), "keyboard")
        self.assertEqual(back_method("", ""), "off")

    def test_adb_sends_android_back_keyevent_without_window_focus(self) -> None:
        with (
            patch("action_executor.subprocess.run", return_value=_completed()) as run_mock,
            patch("action_executor._ensure_game_window_active") as focus_mock,
        ):
            sent = execute_back_key(
                key="", adb_path=self.adb, adb_serial="emulator-5554",
                dry_run=False, allow_taps=True,
            )

        self.assertTrue(sent)
        focus_mock.assert_not_called()
        self.assertEqual(
            run_mock.call_args.args[0],
            [self.adb, "-s", "emulator-5554", "shell", "input", "keyevent", "4"],
        )

    def test_adb_failure_raises_so_the_session_stops_cleanly(self) -> None:
        with patch(
            "action_executor.subprocess.run",
            return_value=_completed(1, stderr="error: device offline"),
        ):
            with self.assertRaises(RuntimeError) as context:
                execute_back_key(adb_path=self.adb, adb_serial="", dry_run=False, allow_taps=True)
        self.assertIn("device offline", str(context.exception))

    def test_dry_run_and_off_send_nothing(self) -> None:
        with patch("action_executor.subprocess.run") as run_mock:
            self.assertFalse(execute_back_key(adb_path=self.adb, dry_run=True, allow_taps=True))
            self.assertFalse(execute_back_key(key="", adb_path="", dry_run=False, allow_taps=True))
        run_mock.assert_not_called()

    def test_check_adb_reports_each_failure_with_a_hint(self) -> None:
        ok, message = check_adb("C:/no/such/adb.exe", "")
        self.assertFalse(ok)
        self.assertIn("ADB_PATH", message)

        with patch("action_executor.subprocess.run", return_value=_completed(0, "device\n")):
            ok, message = check_adb(self.adb, "")
        self.assertTrue(ok)

        with patch(
            "action_executor.subprocess.run",
            return_value=_completed(1, stderr="error: more than one device/emulator"),
        ):
            ok, message = check_adb(self.adb, "")
        self.assertFalse(ok)
        self.assertIn("ADB_SERIAL", message)

        with patch(
            "action_executor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=20),
        ):
            ok, message = check_adb(self.adb, "")
        self.assertFalse(ok)

    def test_autodrive_does_not_start_when_adb_is_misconfigured(self) -> None:
        with (
            patch("navigation.navigator.CAPTURE_MODE", "window"),
            patch("navigation.navigator.DRY_RUN", False),
            patch("navigation.navigator.ADB_PATH", self.adb),
            patch(
                "navigation.navigator.check_adb",
                return_value=(False, "adb 연결 실패: no devices"),
            ),
            patch("navigation.navigator.GraphStore") as store_mock,
            patch("navigation.navigator.analyze_survey_screen") as analyze_mock,
        ):
            summary = run_autodrive(object(), max_steps=5, allow_taps=True)

        self.assertEqual(summary.stop_kind, "blocked")
        store_mock.assert_not_called()
        analyze_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
