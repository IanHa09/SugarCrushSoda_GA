"""storage.py의 저장/조회 함수를 검증하는 단위 테스트입니다."""

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
        """손상된 줄을 건너뛰고 정상 메모리 기록만 불러오는지 검증합니다."""
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
        """세션 시작/종료 이벤트가 순서대로 기록되는지 검증합니다."""
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

    def test_loads_only_failed_moves_from_same_board(self) -> None:
        """같은 보드에서 거부된 swap만 걸러내는지 검증합니다."""
        import storage

        fingerprint = "00" * 32
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            with patch.object(storage, "MEMORY_PATH", path):
                storage.save_memory_entry(
                    session_id="session",
                    step=1,
                    mode="manual",
                    before={
                        "board_fingerprint": fingerprint,
                        "grid": {"rows": 7, "cols": 9},
                    },
                    decision={
                        "action": "swap",
                        "source": {"row": 3, "col": 4},
                        "target": {"row": 3, "col": 5},
                    },
                    execution={"action_outcome": "rejected"},
                    after={},
                    reward=-1.0,
                    success_estimate="negative",
                    lesson="rejected",
                )
                memories, moves = storage.load_failed_moves_for_board(
                    fingerprint,
                    7,
                    9,
                    scan_limit=10,
                    context_limit=5,
                    max_distance=0.12,
                )

        self.assertEqual(len(memories), 1)
        self.assertEqual(moves, {(3, 4, 3, 5)})

    def test_recent_records_come_from_the_end_of_the_log(self) -> None:
        """최근 기록은 로그 끝에서, 전체 기록은 처음부터 읽히는지 검증합니다."""
        import storage

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game_survey.jsonl"
            with path.open("w", encoding="utf-8") as survey_file:
                for index in range(1, 401):
                    survey_file.write(
                        json.dumps(
                            {"step": index, "summary": f"화면 {index} 기록"},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            with patch.object(storage, "SURVEY_LOG_PATH", path):
                recent = storage.load_recent_survey_records(3)
                every = storage.load_all_survey_records()

        self.assertEqual([record["step"] for record in recent], [398, 399, 400])
        self.assertEqual(recent[-1]["summary"], "화면 400 기록")
        self.assertEqual(len(every), 400)
        self.assertEqual(every[0]["step"], 1)

    def test_tail_reader_survives_chunk_boundaries_in_multibyte_text(self) -> None:
        """청크 경계가 멀티바이트 문자를 갈라도 tail 리더가 안전한지 검증합니다."""
        import storage

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lines.jsonl"
            with path.open("w", encoding="utf-8") as text_file:
                for index in range(1, 51):
                    text_file.write(f'{{"step": {index}, "summary": "한글 {index}"}}\n')

            # 청크 경계가 한글 글자 중간을 가르도록 아주 작게 잡습니다.
            lines = storage._read_tail_lines(path, 3, chunk_size=7)

        self.assertEqual(len(lines), 3)
        self.assertIn("한글 48", lines[0])
        self.assertIn("한글 50", lines[-1])

    def test_tail_reader_handles_short_and_empty_files(self) -> None:
        """줄바꿈 없는 파일과 빈 파일도 tail 리더가 올바르게 처리하는지 검증합니다."""
        import storage

        with tempfile.TemporaryDirectory() as directory:
            without_newline = Path(directory) / "partial.jsonl"
            without_newline.write_text('{"a": 1}\n{"a": 2}', encoding="utf-8")
            self.assertEqual(
                storage._read_tail_lines(without_newline, 5),
                ['{"a": 1}', '{"a": 2}'],
            )

            empty = Path(directory) / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(storage._read_tail_lines(empty, 5), [])


if __name__ == "__main__":
    unittest.main()
