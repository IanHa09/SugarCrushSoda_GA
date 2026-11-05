"""실행할 게임을 고르고(output/current_game.txt에 기록), 마지막에 고른 게임을 알려 줍니다.

고르는 순서(main.py가 config를 import하기 전에 select_game을 부릅니다):
  1. --game "제목"
  2. adb로 지금 앞에 떠 있는 앱의 패키지를 읽어, games.txt의 패키지 칸이 같은 게임
  3. 둘 다 안 되면 games.txt 목록에서 번호로 고름(Enter = 지난번 게임)

config.py와 survey_metrics.py는 current_game()으로 마지막에 고른 게임을 읽습니다.
그래서 지표·클릭 위치 도구를 따로 돌려도 같은 게임 폴더(output/<slug>)를 봅니다.
config를 import하지 않습니다(config가 이 모듈을 import합니다).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from game_knowledge import GameEntry, find_game, load_games

REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_GAME_PATH = REPO_ROOT / "output" / "current_game.txt"
# dumpsys window의 포커스 줄에서 패키지를 읽습니다.
#   mCurrentFocus=Window{1a2b u0 com.king.candycrushsodasaga/com.king.….MainActivity}
#   mFocusedApp=ActivityRecord{3c4d u0 com.king.candycrushsodasaga/.MainActivity t12}
# 팝업이 포커스를 가져가면 mCurrentFocus에 패키지가 없을 수 있어 mFocusedApp도 봅니다.
_FOCUS_PACKAGE = re.compile(r"(?:mCurrentFocus|mFocusedApp)=.*?([A-Za-z]\w*(?:\.\w+)+)/")
_ADB_TIMEOUT_SECONDS = 10
# 예전에 .env에서 게임을 정하던 값입니다. 남아 있으면 지워도 된다고 알려 줍니다.
_OLD_ENV_KEYS = ("GAME_TITLE", "GAME_SLUG", "GAME_PACKAGE")


def current_game(games: dict[str, GameEntry] | None = None) -> GameEntry:
    """마지막에 고른 게임. 기록이 없거나 그 게임이 목록에서 빠졌으면 games.txt의 첫 게임."""

    games = games if games is not None else load_games()
    if not games:
        raise SystemExit("knowledge/games.txt에 게임이 없습니다. '게임제목, 장르, 패키지' 한 줄을 더하세요.")
    if CURRENT_GAME_PATH.exists():
        found = find_game(games, CURRENT_GAME_PATH.read_text(encoding="utf-8").strip())
        if found is not None:
            return found
    return next(iter(games.values()))


def foreground_package() -> str | None:
    """adb로 지금 앞에 떠 있는 앱의 패키지 이름을 읽습니다. 못 읽으면 None."""

    adb_path = os.getenv("ADB_PATH", "").strip().strip('"').strip("'")
    if not adb_path:
        print("[GAME] ADB_PATH가 비어 있어 지금 게임을 자동으로 알아낼 수 없습니다.")
        return None
    serial = os.getenv("ADB_SERIAL", "").strip()
    command = [adb_path, *(["-s", serial] if serial else []), "shell", "dumpsys", "window"]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_ADB_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"[GAME] adb 실행 실패: {error}")
        return None
    match = _FOCUS_PACKAGE.search(result.stdout or "")
    return match.group(1) if match else None


def ask_game(games: dict[str, GameEntry], default: GameEntry) -> GameEntry:
    """games.txt 목록을 번호로 보여 주고 고르게 합니다. 입력을 받을 수 없으면 default."""

    if sys.stdin is None or not sys.stdin.isatty():
        print(f"[GAME] 입력을 받을 수 없어 지난번 게임으로 진행합니다: {default.title}")
        return default
    entries = list(games.values())
    print("[GAME] 어떤 게임을 조사할까요? (Enter = * 표시한 지난번 게임)")
    for number, entry in enumerate(entries, start=1):
        mark = " *" if entry == default else ""
        print(f"  {number}. {entry.title} ({entry.genre}){mark}")
    while True:
        answer = input("번호: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(entries):
            return entries[int(answer) - 1]
        print(f"1~{len(entries)} 사이 번호를 적으세요.")


def _option(argv: list[str], name: str) -> str | None:
    """argparse보다 먼저 '--game 제목' 또는 '--game=제목'을 읽습니다."""

    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def select_game(argv: list[str]) -> GameEntry:
    """--game → adb 감지 → 목록에서 고르기 순서로 게임을 정하고 current_game.txt에 적습니다."""

    games = load_games()
    stale = [key for key in _OLD_ENV_KEYS if os.getenv(key)]
    if stale:
        print(f"[GAME] .env의 {', '.join(stale)}는 이제 쓰지 않습니다(games.txt와 --game으로 옮김). 지워도 됩니다.")

    title = _option(argv, "--game")
    if title:
        entry = find_game(games, title)
        if entry is None:
            raise SystemExit(f"games.txt에 없는 게임입니다: {title!r}. knowledge/games.txt에 한 줄을 더하세요.")
        how = "--game"
    else:
        package = foreground_package()
        entry = next((game for game in games.values() if package and game.package == package), None)
        how = "adb 감지"
        if entry is None:
            if package:
                print(f"[GAME] 지금 앞에 떠 있는 앱 {package}는 games.txt에 없습니다.")
            entry = ask_game(games, current_game(games))
            how = "목록"

    CURRENT_GAME_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_GAME_PATH.write_text(entry.title, encoding="utf-8")
    print(f"[GAME] {entry.title} ({how}) -> output/{entry.slug}/")
    return entry
