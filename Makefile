# 프로젝트 실행 명령의 단일 진입점입니다.
# 필요하면 예: make autodrive STEPS=20 PYTHON=python3

# Windows(PowerShell/cmd)는 .venv/Scripts, 그 외는 .venv/bin을 씁니다.
ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
BOOTSTRAP_PYTHON ?= python
else
PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3
endif

STEPS ?= 40
REPEAT ?= 5
# 조사할 게임(knowledge/games.txt의 제목). 비우면 adb로 감지하고, 못 하면 목록에서 고릅니다.
GAME ?=
GAME_ARG = $(if $(GAME),--game "$(GAME)",)

.PHONY: help setup preview calibrate once auto survey-once autodrive autodrive-repeat autodrive-new survey-auto survey-report metrics debug-points hotkeys test

help:
	@printf '%s\n' \
		'사용법: make <명령> [STEPS=숫자] [GAME="게임제목"] [PYTHON=파이썬경로]' \
		'(Windows는 PYTHON 기본값이 .venv/Scripts/python.exe 입니다)' \
		'' \
		'  setup          가상환경·의존성 설치와 .env 파일 생성' \
		'  preview        캡처 영역과 감지된 격자 미리보기' \
		'  calibrate      마우스로 보드 캡처 영역 보정' \
		'  once           현재 화면을 한 번 분석' \
		'  auto           안정된 새 보드를 계속 분석' \
		'  survey-once    현재 화면의 게임 구조를 한 번 기록' \
		'  autodrive      자동 구조 조사, 실제 탭 허용 (기본 최대 40단계)' \
		'  autodrive-repeat  40단계 세션을 REPEAT번(기본 5) 이어서 실행, 세션마다 지표 갱신' \
		'  autodrive-new  새 run 폴더로 autodrive-repeat 시작' \
		'  metrics        현재 run의 세션별 적중률·누적 화면/전환 계산(metrics.md)' \
		'  debug-points   현재 run의 클릭 위치를 캡처 위에 그려 확인(debug_points/)' \
		'  survey-auto    autodrive의 호환 별칭' \
		'  survey-report  저장된 조사 로그로 보고서 생성' \
		'  hotkeys        F8/F9/ESC 단축키 모드' \
		'  test           단위 테스트 실행' \
		'' \
		'예: make autodrive STEPS=20' \
		'예: make autodrive-new REPEAT=5 STEPS=40' \
		'예: make autodrive GAME="Royal Match"'

setup:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	$(PYTHON) -m pip install -r candy_soda_agent/requirements.txt
	@$(PYTHON) -c "import pathlib, shutil; dst = pathlib.Path('candy_soda_agent/.env'); dst.exists() or shutil.copyfile('candy_soda_agent/.env.example', dst)"

preview:
	$(PYTHON) candy_soda_agent/preview_capture.py

calibrate:
	$(PYTHON) candy_soda_agent/calibrate_region.py

once:
	$(PYTHON) candy_soda_agent/main.py --once $(GAME_ARG)

auto:
	$(PYTHON) candy_soda_agent/main.py --auto $(GAME_ARG)

survey-once:
	$(PYTHON) candy_soda_agent/main.py --survey-once $(GAME_ARG)

autodrive:
	$(PYTHON) candy_soda_agent/main.py --autodrive --survey-taps --autodrive-steps $(STEPS) $(GAME_ARG)

autodrive-repeat:
	$(PYTHON) candy_soda_agent/main.py --autodrive --survey-taps --autodrive-steps $(STEPS) --autodrive-repeat $(REPEAT) $(GAME_ARG)

autodrive-new:
	$(PYTHON) candy_soda_agent/main.py --autodrive --survey-taps --autodrive-steps $(STEPS) --autodrive-repeat $(REPEAT) --new-run $(GAME_ARG)

metrics:
	$(PYTHON) candy_soda_agent/survey_metrics.py

debug-points:
	$(PYTHON) candy_soda_agent/debug_survey_points.py

survey-auto:
	$(PYTHON) candy_soda_agent/main.py --survey-auto --survey-taps --autodrive-steps $(STEPS) $(GAME_ARG)

survey-report:
	$(PYTHON) candy_soda_agent/main.py --survey-report $(GAME_ARG)

hotkeys:
	$(PYTHON) candy_soda_agent/main.py --hotkeys $(GAME_ARG)

test:
	$(PYTHON) -m unittest discover -s candy_soda_agent/tests -v