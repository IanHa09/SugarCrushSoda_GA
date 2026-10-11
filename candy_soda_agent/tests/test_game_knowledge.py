"""게임 지식 파일(knowledge/) 읽기 테스트."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from game_knowledge import (
    FALLBACK_SCREEN_TYPE,
    KnowledgeError,
    load_game_profile,
    load_games,
    load_screen_types,
    matches_any_label,
    screen_policy,
    screen_type_ids,
    screen_type_labels,
    survey_prompt_block,
)
from schemas import SurveyDecision

# 큰 화면 타입 10개(2026-10-11 검수). id는 그래프·기록에 남는 값이라 바꾸면 예전 run과
# 이어지지 않으므로, 바꿀 때는 이 목록도 함께 고칩니다.
SCREEN_TYPES = (
    "play", "home", "level", "popup", "menu", "shop", "ad", "tutorial", "loading", "unknown",
)


class _TempFiles:
    def __init__(self, test: unittest.TestCase) -> None:
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, name: str, content) -> Path:
        path = self.root / name
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path


def _types_file(files: _TempFiles, types: list[dict]) -> Path:
    return files.write("screen_types.json", {"types": types})


BASE_TYPES = [
    {"id": "play", "policy": "leave", "description": "보드"},
    {"id": "booster", "genres": ["match3"]},
    {"id": "notice_popup", "enabled": False},
    {"id": FALLBACK_SCREEN_TYPE},
]


class ShippedFilesTests(unittest.TestCase):
    """저장소에 들어 있는 knowledge/ 파일이 그대로 읽히는지."""

    def test_big_screen_types(self) -> None:
        self.assertEqual(screen_type_ids(), SCREEN_TYPES)
        self.assertEqual(FALLBACK_SCREEN_TYPE, "unknown")
        # 분류 원칙(rules)이 프롬프트에 들어갑니다.
        block = survey_prompt_block(load_game_profile("Candy Crush Soda Saga"))
        self.assertIn("화면 타입 고르는 원칙:", block)
        self.assertIn("play > level", block)

    def test_schema_enum_matches_the_file(self) -> None:
        enum = SurveyDecision.model_json_schema()["properties"]["screen_type"]["enum"]
        self.assertEqual(tuple(enum), screen_type_ids())
        self.assertEqual(SurveyDecision().screen_type, FALLBACK_SCREEN_TYPE)

    def test_every_type_has_a_label(self) -> None:
        self.assertTrue(set(screen_type_ids()) <= set(screen_type_labels()))

    def test_policies(self) -> None:
        self.assertEqual(screen_policy("play"), "leave")
        self.assertEqual(screen_policy("loading"), "wait")
        self.assertEqual(screen_policy("home"), "explore")
        self.assertEqual(screen_policy("no_such_type"), "explore")

    def test_match3_profile_is_genre_level(self) -> None:
        # 장르 지식은 특정 게임을 모릅니다: 탭 이름·탭바 위치를 단정하지 않습니다.
        profile = load_game_profile("candy crush soda saga")
        self.assertTrue(profile.known)
        self.assertEqual(profile.genre, "match3")
        self.assertEqual((profile.nav_bar_position, profile.nav_tabs), ("varies", ()))
        self.assertEqual(profile.nav_max_tabs, 5)
        self.assertEqual(profile.button_limit, 10)  # 기본 6 + (탭 5 - 선택된 탭 1)
        self.assertTrue(any(hint.startswith("[네비게이션 바]") for hint in profile.layout_hints))
        self.assertEqual(profile.home_screen_types, ("home",))
        block = survey_prompt_block(profile)
        self.assertIn("이 장르에서 흔한 UI 형태", block)
        self.assertIn("글자 없는 아이콘 탭 포함", block)
        self.assertIn("탭바 게임마다 다름", profile.summary_line())
        self.assertTrue(profile.is_play_entry("플레이!"))
        self.assertTrue(profile.is_play_entry("Try again"))
        self.assertTrue(profile.is_play_exit("레벨 종료"))
        self.assertTrue(profile.is_play_exit("메인으로"))


class GamesFileTests(unittest.TestCase):
    def test_comments_blank_lines_and_commas_in_title(self) -> None:
        files = _TempFiles(self)
        path = files.write("games.txt", "# 게임제목, 장르\n\nCandy, Crush,  match3 \n Other Game ,rpg\n")
        games = load_games(path)
        self.assertEqual(games["candy, crush"], ("Candy, Crush", "match3"))
        self.assertEqual(games["other game"], ("Other Game", "rpg"))

    def test_bad_line_reports_line_number(self) -> None:
        files = _TempFiles(self)
        path = files.write("games.txt", "# 주석\nNo genre here\n")
        with self.assertRaisesRegex(KnowledgeError, "2번째 줄"):
            load_games(path)


class ScreenTypesFileTests(unittest.TestCase):
    def test_disabled_types_are_skipped_unless_asked(self) -> None:
        path = _types_file(_TempFiles(self), BASE_TYPES)
        self.assertNotIn("notice_popup", [t.id for t in load_screen_types(path)])
        self.assertIn("notice_popup", [t.id for t in load_screen_types(path, include_disabled=True)])

    def test_invalid_files_fail_with_a_clear_message(self) -> None:
        files = _TempFiles(self)
        cases = {
            "두 번": [*BASE_TYPES, {"id": "play"}],
            "policy": [{"id": "a", "policy": "jump"}, {"id": FALLBACK_SCREEN_TYPE}],
            "지우거나": [{"id": "play"}],
            "소문자": [{"id": "Bad Id"}, {"id": FALLBACK_SCREEN_TYPE}],
        }
        for message, types in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(KnowledgeError, message):
                    load_screen_types(_types_file(files, types))
        broken = files.write("broken.json", "{\"types\": [")
        with self.assertRaisesRegex(KnowledgeError, "JSON 형식 오류"):
            load_screen_types(broken)


class ProfileMergeTests(unittest.TestCase):
    def _profile(self, title: str):
        files = _TempFiles(self)
        games = files.write("games.txt", "Test Game, match3\n")
        ui = files.write("ui.json", {
            "genres": {
                "_default": {"layout_hints": ["공통"], "play_entry_labels": ["play"],
                             "play_exit_hint": "기본 힌트"},
                "match3": {"label": "매치3", "layout_hints": ["장르"], "play_entry_labels": ["재도전"],
                           "nav_bar": {"position": "bottom"}, "home_screen_types": ["home"]},
            },
            "games": {"test game": {"layout_hints": ["게임", "공통"], "nav_bar": {"tabs": ["상점", "홈"]}}},
        })
        types = _types_file(files, BASE_TYPES)
        return load_game_profile(title, games_path=games, ui_path=ui, types_path=types)

    def test_default_then_genre_then_game(self) -> None:
        profile = self._profile("TEST  game")
        self.assertEqual(profile.title, "Test Game")
        self.assertEqual(profile.genre_label, "매치3")
        self.assertEqual(profile.layout_hints, ("공통", "장르", "게임"))  # 이어 붙이고 중복 제거
        self.assertEqual(profile.play_entry_labels, ("play", "재도전"))
        # nav_bar는 덮어쓰기: 장르의 position과 게임의 tabs가 합쳐집니다.
        self.assertEqual((profile.nav_bar_position, profile.nav_tabs), ("bottom", ("상점", "홈")))
        self.assertEqual(profile.play_exit_hint, "기본 힌트")
        self.assertIn("booster", [t.id for t in profile.screen_types])

    def test_unknown_title_falls_back_to_default_genre(self) -> None:
        profile = self._profile("Another Game")
        self.assertFalse(profile.known)
        self.assertEqual(profile.genre, "_default")
        self.assertEqual(profile.layout_hints, ("공통",))
        # 장르 전용 타입(booster)은 _default 프롬프트에 들어가지 않습니다.
        self.assertNotIn("booster", [t.id for t in profile.screen_types])

    def test_prompt_block_lists_types_hints_and_tab_rule(self) -> None:
        profile = self._profile("Test Game")
        block = survey_prompt_block(profile)
        self.assertIn("게임 정보: Test Game (장르: 매치3)", block)
        self.assertIn("- play: 보드", block)
        self.assertNotIn("notice_popup", block)
        self.assertIn("하단 탭바(상점, 홈)", block)
        self.assertIn("region=bottom_nav", block)
        self.assertEqual(profile.button_limit, 7)  # 기본 6 + (탭 2 - 선택된 탭 1)


class GenreNavBarTests(unittest.TestCase):
    """탭 이름을 모르는 장르 수준 탭바(nav_bar.position=varies, max_tabs)."""

    def _profile(self, nav_bar: dict, *, game_tabs=None):
        files = _TempFiles(self)
        games = files.write("games.txt", "Some Game, match3\n")
        ui = files.write("ui.json", {
            "genres": {"match3": {"nav_bar": nav_bar}},
            "games": {"some game": {"nav_bar": {"tabs": game_tabs}}} if game_tabs else {},
        })
        types = _types_file(files, BASE_TYPES)
        return load_game_profile("Some Game", games_path=games, ui_path=ui, types_path=types)

    def test_varies_with_max_tabs_adds_a_generic_rule_and_room(self) -> None:
        profile = self._profile({"position": "varies", "max_tabs": 4})
        block = survey_prompt_block(profile)
        self.assertIn("글자 없는 아이콘 탭 포함", block)
        self.assertNotIn("하단 탭바(", block)
        self.assertEqual(profile.button_limit, 9)  # 6 + (4 - 1)
        self.assertIn("탭 최대 4개", profile.summary_line())

    def test_known_tab_names_win_over_max_tabs(self) -> None:
        profile = self._profile({"position": "bottom", "max_tabs": 5}, game_tabs=["상점", "홈", "친구"])
        block = survey_prompt_block(profile)
        self.assertIn("하단 탭바(상점, 홈, 친구)", block)
        self.assertNotIn("글자 없는 아이콘 탭 포함", block)
        self.assertEqual(profile.button_limit, 8)  # 6 + (3 - 1)

    def test_no_nav_info_keeps_the_base_limit(self) -> None:
        profile = self._profile({"position": "none"})
        self.assertNotIn("탭바 규칙", survey_prompt_block(profile))
        self.assertEqual(profile.button_limit, 6)
        self.assertIn("탭바 정보 없음", profile.summary_line())

    def test_bad_nav_values_fail_clearly(self) -> None:
        with self.assertRaisesRegex(KnowledgeError, "nav_bar.position"):
            self._profile({"position": "sideways"})
        with self.assertRaisesRegex(KnowledgeError, "max_tabs"):
            self._profile({"position": "varies", "max_tabs": "many"})


class LabelMatchTests(unittest.TestCase):
    def test_word_level_matching(self) -> None:
        patterns = ["플레이", "play", "레벨 종료"]
        for label, expected in (
            ("플레이!", True),
            ("지금 플레이", True),
            ("Play (green button)", True),
            ("플레이어 프로필", False),
            ("Replay", False),
            ("레벨 종료하기", False),
            ("레벨 종료", True),
            ("", False),
        ):
            with self.subTest(label=label):
                self.assertEqual(matches_any_label(label, patterns), expected)


if __name__ == "__main__":
    unittest.main()
