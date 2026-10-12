"""게임 지식 파일(knowledge/)을 읽어 autodrive가 쓰기 좋은 형태로 넘깁니다.

파일 세 개를 읽습니다.
  - knowledge/games.txt          : "게임제목, 장르" 목록. .env의 GAME_TITLE로 장르를 찾습니다.
  - knowledge/ui_knowledge.json  : 장르 공통 UI 관례 + 게임별 덧붙임(네비게이션 바, 플레이 진입/나가기 버튼 이름).
  - knowledge/screen_types.json  : 화면 타입 분류표(id, 설명, 처리 방식 policy, 적용 장르).

쓰이는 곳:
  - schemas.SurveyDecision.screen_type의 허용 값 = screen_type_ids()
  - survey.analyze_survey_screen 프롬프트의 게임 정보·UI 힌트·화면 타입 목록 = survey_prompt_block()
  - survey_report 화면 타입 라벨 = screen_type_labels()
  - navigator의 플레이 차단(PLAY_POLICY)과 화면 처리 방식 = GameProfile, screen_policy()

이 모듈은 config를 맨 위에서 import하지 않습니다(schemas가 이 모듈을 import하므로
순환을 피하려고). 현재 게임 프로필은 active_profile()이 처음 불릴 때 config.GAME_TITLE로 읽습니다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from survey_utils import normalize_text

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
GAMES_PATH = KNOWLEDGE_DIR / "games.txt"
UI_KNOWLEDGE_PATH = KNOWLEDGE_DIR / "ui_knowledge.json"
SCREEN_TYPES_PATH = KNOWLEDGE_DIR / "screen_types.json"

# 화면 타입을 모를 때 쓰는 값. screen_types.json에서 지우면 안 됩니다.
FALLBACK_SCREEN_TYPE = "unknown"
# explore: 버튼 탐색 / leave: 기록만 하고 바로 나옴(플레이) / wait: 기다렸다 다시 캡처(로딩)
SCREEN_POLICIES = frozenset({"explore", "leave", "wait"})
DEFAULT_GENRE = "_default"
NAV_POSITIONS = {"bottom": "하단", "top": "상단", "left": "왼쪽", "right": "오른쪽"}
# nav_bar.position에 쓸 수 있는 값. none = 정보 없음, varies = 게임마다 다름(있을 수도 없을 수도).
NAV_POSITION_VALUES = frozenset({*NAV_POSITIONS, "none", "varies"})
# 프롬프트가 요청하는 버튼 후보 기본 개수와, 탭바 탭을 더한 최대 개수(schemas와 같은 값).
BASE_BUTTON_LIMIT = 6
MAX_BUTTON_LIMIT = 10

_SCREEN_TYPE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class KnowledgeError(ValueError):
    """지식 파일 형식이 잘못됐을 때 냅니다(어느 파일 몇째 줄인지 메시지에 담습니다)."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise KnowledgeError(f"지식 파일이 없습니다: {path}") from error
    except json.JSONDecodeError as error:
        raise KnowledgeError(
            f"{path} JSON 형식 오류({error.lineno}번째 줄 {error.colno}번째 글자): {error.msg}"
        ) from error
    if not isinstance(data, dict):
        raise KnowledgeError(f"{path}: 맨 바깥은 {{ }} 객체여야 합니다.")
    return data


def _unique(items) -> tuple[str, ...]:
    """순서를 지키며 빈 값과 중복을 뺍니다."""
    return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


# -- 화면 타입 -------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenType:
    id: str
    label: str
    label_ko: str = ""
    group: str = "other"
    policy: str = "explore"
    genres: tuple[str, ...] = ("*",)
    description: str = ""
    enabled: bool = True

    def applies_to(self, genre: str) -> bool:
        return "*" in self.genres or genre in self.genres


def load_screen_types(
    path: Path | None = None,
    *,
    include_disabled: bool = False,
) -> list[ScreenType]:
    """screen_types.json을 읽어 검사합니다. 기본은 enabled인 타입만 돌려줍니다."""

    path = Path(path) if path is not None else SCREEN_TYPES_PATH
    data = _read_json(path)
    raw_types = data.get("types")
    if not isinstance(raw_types, list) or not raw_types:
        raise KnowledgeError(f"{path}: \"types\" 목록이 비어 있습니다.")

    types: list[ScreenType] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_types, start=1):
        if not isinstance(raw, dict):
            raise KnowledgeError(f"{path}: types의 {index}번째 항목이 객체가 아닙니다.")
        type_id = str(raw.get("id", "")).strip()
        if not _SCREEN_TYPE_ID.match(type_id):
            raise KnowledgeError(
                f"{path}: types {index}번째 id {type_id!r}는 영어 소문자·숫자·밑줄만 쓸 수 있습니다."
            )
        if type_id in seen:
            raise KnowledgeError(f"{path}: id {type_id!r}가 두 번 나옵니다.")
        seen.add(type_id)
        policy = str(raw.get("policy", "explore")).strip()
        if policy not in SCREEN_POLICIES:
            raise KnowledgeError(
                f"{path}: {type_id}의 policy {policy!r}는 {sorted(SCREEN_POLICIES)} 중 하나여야 합니다."
            )
        genres = raw.get("genres", ["*"])
        if isinstance(genres, str):
            genres = [genres]
        types.append(ScreenType(
            id=type_id,
            label=str(raw.get("label") or type_id.replace("_", " ").title()),
            label_ko=str(raw.get("label_ko", "")),
            group=str(raw.get("group", "other")),
            policy=policy,
            genres=_unique(genres) or ("*",),
            description=str(raw.get("description", "")).strip(),
            enabled=bool(raw.get("enabled", True)),
        ))

    if not any(t.id == FALLBACK_SCREEN_TYPE and t.enabled for t in types):
        raise KnowledgeError(f"{path}: {FALLBACK_SCREEN_TYPE}는 지우거나 끌 수 없습니다(응답 기본값).")
    return [t for t in types if include_disabled or t.enabled]


def load_screen_type_rules(path: Path | None = None) -> tuple[str, ...]:
    """screen_types.json의 분류 원칙(rules). 조사 프롬프트의 타입 목록 아래에 들어갑니다."""

    path = Path(path) if path is not None else SCREEN_TYPES_PATH
    rules = _read_json(path).get("rules", [])
    if not isinstance(rules, list):
        raise KnowledgeError(f"{path}: \"rules\"는 문장 목록이어야 합니다.")
    return _unique(rules)


@lru_cache(maxsize=None)
def _screen_types_cached(include_disabled: bool) -> tuple[ScreenType, ...]:
    return tuple(load_screen_types(include_disabled=include_disabled))


@lru_cache(maxsize=None)
def _screen_type_rules_cached() -> tuple[str, ...]:
    return load_screen_type_rules()


def screen_type_ids() -> tuple[str, ...]:
    """조사 응답 스키마가 허용하는 screen_type 값(enabled만, 파일 순서)."""
    return tuple(t.id for t in _screen_types_cached(False))


def screen_type_labels() -> dict[str, str]:
    """보고서 라벨. 꺼진 후보 타입도 포함합니다(예전 기록에 남아 있을 수 있음)."""
    return {t.id: t.label for t in _screen_types_cached(True)}


def screen_policy(screen_type: str | None) -> str:
    """화면 타입의 처리 방식. 모르는 타입이면 explore."""
    for screen in _screen_types_cached(True):
        if screen.id == screen_type:
            return screen.policy
    return "explore"


# -- 게임 목록 -------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    return " ".join(str(title).casefold().split())


def load_games(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """games.txt를 읽어 {정규화 제목: (제목, 장르)}를 돌려줍니다.

    한 줄 형식은 "게임제목, 장르"입니다. 제목에 쉼표가 있어도 되도록 마지막 쉼표로 나눕니다."""

    path = Path(path) if path is not None else GAMES_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise KnowledgeError(f"지식 파일이 없습니다: {path}") from error

    games: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "," not in line:
            raise KnowledgeError(f"{path} {line_number}번째 줄: '게임제목, 장르' 형식이 아닙니다: {line!r}")
        title, genre = (part.strip() for part in line.rsplit(",", 1))
        if not title or not genre:
            raise KnowledgeError(f"{path} {line_number}번째 줄: 제목이나 장르가 비어 있습니다: {line!r}")
        games[_normalize_title(title)] = (title, genre)
    return games


# -- 게임 프로필 -----------------------------------------------------------------


def _label_tokens(label: str) -> list[str]:
    # 괄호 속 보충 설명("홈 (별 아이콘)")은 빼고 낱말로 나눕니다(canonical_action_label과 같은 규칙).
    return normalize_text(re.sub(r"\([^)]*\)", " ", label or "")).split()


def matches_any_label(label: str, patterns) -> bool:
    """버튼 이름에 패턴이 낱말 단위로 들어 있는지. '플레이'는 '플레이!'·'지금 플레이'와 맞고
    '플레이어'와는 맞지 않습니다. 여러 낱말 패턴('레벨 종료')은 이어진 낱말로 찾습니다."""

    tokens = _label_tokens(label)
    if not tokens:
        return False
    for pattern in patterns:
        wanted = normalize_text(pattern).split()
        if not wanted:
            continue
        size = len(wanted)
        if any(tokens[i:i + size] == wanted for i in range(len(tokens) - size + 1)):
            return True
    return False


@dataclass(frozen=True)
class GameProfile:
    """지금 조사하는 게임의 장르와 UI 사전 지식(장르 공통 + 게임별을 합친 결과)."""

    title: str
    genre: str
    genre_label: str = ""
    known: bool = False
    layout_hints: tuple[str, ...] = ()
    nav_bar_position: str = "none"
    nav_tabs: tuple[str, ...] = ()
    # 탭 이름은 모르지만 탭바가 있을 수 있을 때, 버튼 후보에 더 허용할 탭 수(장르 수준).
    nav_max_tabs: int = 0
    play_entry_labels: tuple[str, ...] = ()
    play_exit_labels: tuple[str, ...] = ()
    home_screen_types: tuple[str, ...] = ()
    play_exit_hint: str = ""
    screen_types: tuple[ScreenType, ...] = ()
    screen_type_rules: tuple[str, ...] = ()

    def is_play_entry(self, label: str) -> bool:
        return matches_any_label(label, self.play_entry_labels)

    def is_play_exit(self, label: str) -> bool:
        return matches_any_label(label, self.play_exit_labels)

    def is_home_type(self, screen_type: str | None) -> bool:
        return screen_type in self.home_screen_types

    @property
    def expected_tabs(self) -> int:
        """탭바에 있을 것으로 보는 탭 수. 탭 이름을 알면 그 수, 아니면 nav_max_tabs."""
        return len(self.nav_tabs) or self.nav_max_tabs

    @property
    def button_limit(self) -> int:
        """프롬프트에 적는 버튼 후보 최대 개수. 탭바 탭이 있을 수 있으면 그만큼 늘립니다
        (지금 선택된 탭 하나는 누를 필요가 없어 뺍니다)."""
        extra = max(0, self.expected_tabs - 1)
        return min(BASE_BUTTON_LIMIT + extra, MAX_BUTTON_LIMIT)

    def summary_line(self) -> str:
        source = "games.txt" if self.known else "games.txt에 없음 → _default"
        if self.nav_tabs:
            where = NAV_POSITIONS.get(self.nav_bar_position, "")
            nav = f"{where} 탭바 {len(self.nav_tabs)}개".strip()
        elif self.nav_bar_position == "varies":
            nav = f"탭바 게임마다 다름(탭 최대 {self.nav_max_tabs}개로 봄)"
        elif self.nav_bar_position in NAV_POSITIONS:
            nav = f"{NAV_POSITIONS[self.nav_bar_position]} 탭바"
        else:
            nav = "탭바 정보 없음"
        return (
            f"{self.title} / 장르 {self.genre_label or self.genre}({self.genre}, {source}) / "
            f"{nav} / 화면 타입 {len(self.screen_types)}개"
        )


def _merge_layers(layers: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "label": "",
        "layout_hints": [],
        "play_entry_labels": [],
        "play_exit_labels": [],
        "home_screen_types": [],
        "nav_bar": {"position": "none", "tabs": []},
        "play_exit_hint": "",
    }
    for layer in layers:
        if not layer:
            continue
        for key in ("layout_hints", "play_entry_labels", "play_exit_labels", "home_screen_types"):
            merged[key] = list(_unique([*merged[key], *layer.get(key, [])]))
        if layer.get("label"):
            merged["label"] = layer["label"]
        if layer.get("play_exit_hint"):
            merged["play_exit_hint"] = layer["play_exit_hint"]
        if isinstance(layer.get("nav_bar"), dict):
            merged["nav_bar"] = {**merged["nav_bar"], **layer["nav_bar"]}
    return merged


def load_game_profile(
    title: str,
    *,
    games_path: Path | None = None,
    ui_path: Path | None = None,
    types_path: Path | None = None,
) -> GameProfile:
    """제목으로 장르를 찾고(games.txt), 장르·게임 UI 지식과 화면 타입을 합칩니다.
    제목이 games.txt에 없으면 장르 _default로 계속합니다(known=False)."""

    games = load_games(games_path)
    found = games.get(_normalize_title(title))
    if found is not None:
        title, genre = found
    else:
        genre = DEFAULT_GENRE

    ui = _read_json(Path(ui_path) if ui_path is not None else UI_KNOWLEDGE_PATH)
    genres = ui.get("genres") or {}
    game_layers = {
        _normalize_title(name): value for name, value in (ui.get("games") or {}).items()
    }
    layers = [genres.get(DEFAULT_GENRE, {})]
    if genre != DEFAULT_GENRE:
        layers.append(genres.get(genre, {}))
    layers.append(game_layers.get(_normalize_title(title), {}))
    merged = _merge_layers(layers)

    position = str(merged["nav_bar"].get("position", "none")).strip() or "none"
    if position not in NAV_POSITION_VALUES:
        raise KnowledgeError(
            f"nav_bar.position {position!r}는 {', '.join(sorted(NAV_POSITION_VALUES))} 중 하나여야 합니다."
        )
    try:
        max_tabs = int(merged["nav_bar"].get("max_tabs", 0) or 0)
    except (TypeError, ValueError) as error:
        raise KnowledgeError("nav_bar.max_tabs는 0 이상의 정수여야 합니다.") from error
    if max_tabs < 0:
        raise KnowledgeError("nav_bar.max_tabs는 0 이상의 정수여야 합니다.")

    if types_path is not None:
        types = load_screen_types(types_path)
        rules = load_screen_type_rules(types_path)
    else:
        types = list(_screen_types_cached(False))
        rules = _screen_type_rules_cached()
    return GameProfile(
        title=title,
        genre=genre,
        genre_label=merged["label"] or genre,
        known=found is not None,
        layout_hints=tuple(merged["layout_hints"]),
        nav_bar_position=position,
        nav_tabs=_unique(merged["nav_bar"].get("tabs", [])),
        nav_max_tabs=max_tabs,
        play_entry_labels=tuple(merged["play_entry_labels"]),
        play_exit_labels=tuple(merged["play_exit_labels"]),
        home_screen_types=tuple(merged["home_screen_types"]),
        play_exit_hint=merged["play_exit_hint"],
        screen_types=tuple(t for t in types if t.applies_to(genre)),
        screen_type_rules=tuple(rules),
    )


def survey_prompt_block(profile: GameProfile) -> str:
    """조사 프롬프트에 넣을 게임 정보·UI 힌트·화면 타입·탭바 규칙."""

    lines = [f"게임 정보: {profile.title} (장르: {profile.genre_label or profile.genre})"]
    if profile.layout_hints:
        lines.extend([
            "",
            "이 장르에서 흔한 UI 형태(게임마다 다를 수 있다. 화면 타입 분류와 버튼 찾기에 참고):",
        ])
        lines.extend(f"- {hint}" for hint in profile.layout_hints)

    lines.extend(["", "화면 타입:"])
    lines.extend(
        f"- {screen.id}: {screen.description}" if screen.description else f"- {screen.id}"
        for screen in profile.screen_types
    )
    if profile.screen_type_rules:
        lines.extend(["", "화면 타입 고르는 원칙:"])
        lines.extend(f"- {rule}" for rule in profile.screen_type_rules)

    if not profile.nav_tabs and profile.nav_max_tabs > 0:
        # 탭 이름은 모르지만(장르 수준 지식) 탭바가 있을 수 있습니다. 탭이 다른 버튼에
        # 밀려 후보에서 빠지지 않게 먼저 넣으라고 하고, 그만큼 후보 개수를 늘립니다.
        lines.extend([
            "",
            "탭바 규칙:",
            "- 네비게이션 바(글자 없는 아이콘 탭 포함)가 보이면 선택되지 않은 탭을 다른 버튼보다 먼저 "
            "button_candidates에 넣고 role=safe_navigation으로 적는다. 이름과 region은 위 UI 형태 설명을 따른다.",
            "- 팝업이 떠서 탭바가 어둡게 가려져 있으면 탭은 넣지 않는다.",
            "- 네비게이션 바가 없으면 이 규칙은 무시한다.",
        ])
    if profile.nav_tabs:
        where = NAV_POSITIONS.get(profile.nav_bar_position, "")
        region = "top_bar" if profile.nav_bar_position == "top" else "bottom_nav"
        lines.extend([
            "",
            "탭바 규칙:",
            f"- {where} 탭바({', '.join(profile.nav_tabs)})가 보이면, 지금 선택된 탭을 뺀 나머지 탭을 "
            f"button_candidates에 빠짐없이 넣고 role=safe_navigation, region={region}로 적는다.",
            "- 탭 label은 위 탭 이름을 그대로 쓴다(이름이 안 보이는 아이콘 탭도 같은 이름으로).",
            "- 팝업이 떠서 탭바가 어둡게 가려져 있으면 탭은 넣지 않는다.",
        ])
    return "\n".join(lines)


# -- 현재 게임 -------------------------------------------------------------------

_ACTIVE_PROFILE: GameProfile | None = None


def active_profile() -> GameProfile:
    """config.GAME_TITLE의 프로필(처음 한 번 읽고 재사용)."""

    global _ACTIVE_PROFILE
    if _ACTIVE_PROFILE is None:
        from config import GAME_TITLE

        _ACTIVE_PROFILE = load_game_profile(GAME_TITLE)
    return _ACTIVE_PROFILE


def set_active_profile(profile: GameProfile | None) -> None:
    """테스트나 다른 게임으로 바꿀 때 씁니다. None이면 다음 호출 때 다시 읽습니다."""

    global _ACTIVE_PROFILE
    _ACTIVE_PROFILE = profile
