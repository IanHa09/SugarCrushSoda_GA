from __future__ import annotations

import importlib.util
import json
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


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "storage dependencies are not installed")
class StorageTests(unittest.TestCase):
    def test_memory_round_trip_skips_corrupt_line(self) -> None:
        import storage

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            with patch.object(storage, "MEMORY_PATH", path):
                storage.save_memory_entry(
                    session_id="session",
                    step=1,
                    mode="manual",
                    before={},
                    decision={"action": "wait"},
                    execution={},
                    after={},
                    reward=0.0,
                    success_estimate="unknown",
                    lesson="test",
                )
                with path.open("a", encoding="utf-8") as memory_file:
                    memory_file.write("not-json\n")

                memories = storage.load_recent_memories(5)

            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0]["decision"]["action"], "wait")

    def test_session_has_start_and_finish_events(self) -> None:
        import storage

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.jsonl"
            with patch.object(storage, "SESSION_LOG_PATH", path):
                storage.start_session("session", {"dry_run": True})
                storage.finish_session(
                    "session",
                    stop_reason="stopped_by_esc",
                    api_call_count=1,
                    successful_actions=0,
                    failed_actions=0,
                    blocked_actions=0,
                    last_error=None,
                )

            events = [json.loads(line)["event"] for line in path.read_text().splitlines()]
            self.assertEqual(events, ["started", "finished"])


if __name__ == "__main__":
    unittest.main()
