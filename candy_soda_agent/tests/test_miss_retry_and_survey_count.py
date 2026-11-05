"""빗나간 버튼 재시도는 화면 전체에서 다시 찾기(E1), 조사 모델 버튼 후보 수 기록(F1).

실측(run_2026-10-27_06-35-53): 친구 화면 '메시지함'의 처음 좌표가 x=243(실제 약 530)이라, 확대
확인이 그 주변을 잘라 볼 때마다 진짜 메시지함이 잘린 영역 끝에 걸려 4번 다 옆 '친구' 칸을 눌렀습니다.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import active_profile
from schemas import SurveyDecision
from tap_refine import TapRefineResult
from test_play_policy import AutodriveHarness, _bundle, _button


class MissRetryTests(AutodriveHarness):
    def test_retry_after_a_miss_searches_the_whole_screen(self) -> None:
        home = SurveyDecision(screen_type="home", button_candidates=[_button("메시지함")])
        found = TapRefineResult(found=True, initial=(2, 2), x=2, y=2, used_pass="zoom")
        with patch("navigation.navigator.refine_tap_target", return_value=found) as refine:
            # 화면이 그대로(같은 픽셀)라 첫 탭은 빗나감 → 같은 버튼을 다시 누름.
            mocks = self._run(
                captures=[_bundle(0), _bundle(0)],
                decisions=[home],
                max_steps=2,
                extra_patches=(("navigation.navigator.TAP_REFINE", True),),
            )
        self.assertEqual(mocks["tapped"], ["메시지함", "메시지함"])
        self.assertEqual([call.kwargs["full_only"] for call in refine.call_args_list], [False, True])


class SurveyButtonCountTests(AutodriveHarness):
    def _run_once(self, button_count: int):
        logged = []
        home = SurveyDecision(
            screen_type="home",
            button_candidates=[_button(f"버튼 {chr(65 + i)}") for i in range(button_count)],
        )
        output = io.StringIO()
        with redirect_stdout(output):
            self._run(
                captures=[_bundle(0)],
                decisions=[home],
                max_steps=1,
                extra_patches=(("navigation.graph.GraphStore.log", lambda store, record: logged.append(record)),),
            )
        return logged[0]["survey_buttons"], output.getvalue()

    def test_count_is_logged_and_hitting_the_limit_is_flagged(self) -> None:
        limit = active_profile().button_limit
        count, printed = self._run_once(limit)
        self.assertEqual(count, limit)
        self.assertIn(f"[SURVEY] 버튼 후보가 상한 {limit}개에 닿았습니다", printed)

        count, printed = self._run_once(2)
        self.assertEqual(count, 2)
        self.assertNotIn("[SURVEY]", printed)


if __name__ == "__main__":
    unittest.main()
