# 프로젝트 실행 명령의 단일 진입점입니다.
# 필요하면 예: make autodrive STEPS=20 PYTHON=python3

PYTHON ?= .venv/bin/python
STEPS ?= 40

.PHONY: help setup preview calibrate once auto survey-once autodrive survey-auto survey-report hotkeys test

help:
	@printf '%s\n' \
		'사용법: make <명령> [STEPS=숫자] [PYTHON=파이썬경로]' \
		'' \
		'  setup          가상환경·의존성 설치와 .env 파일 생성' \
		'  preview        캡처 영역과 감지된 격자 미리보기' \
		'  calibrate      마우스로 보드 캡처 영역 보정' \
		'  once           현재 화면을 한 번 분석' \
		'  auto           안정된 새 보드를 계속 분석' \
		'  survey-once    현재 화면의 게임 구조를 한 번 기록' \
		'  autodrive      자동 구조 조사, 실제 탭 허용 (기본 최대 40단계)' \
		'  survey-auto    autodrive의 호환 별칭' \
		'  survey-report  저장된 조사 로그로 보고서 생성' \
		'  hotkeys        F8/F9/ESC 단축키 모드' \
		'  test           단위 테스트 실행' \
		'' \
		'예: make autodrive STEPS=20'

setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r candy_soda_agent/requirements.txt
	@test -f candy_soda_agent/.env || cp candy_soda_agent/.env.example candy_soda_agent/.env

preview:
	$(PYTHON) candy_soda_agent/preview_capture.py

calibrate:
	$(PYTHON) candy_soda_agent/calibrate_region.py

once:
	$(PYTHON) candy_soda_agent/main.py --once

auto:
	$(PYTHON) candy_soda_agent/main.py --auto

survey-once:
	$(PYTHON) candy_soda_agent/main.py --survey-once

autodrive:
	$(PYTHON) candy_soda_agent/main.py --autodrive --survey-taps --autodrive-steps $(STEPS)

survey-auto:
	$(PYTHON) candy_soda_agent/main.py --survey-auto --survey-taps --autodrive-steps $(STEPS)

survey-report:
	$(PYTHON) candy_soda_agent/main.py --survey-report

hotkeys:
	$(PYTHON) candy_soda_agent/main.py --hotkeys

test:
	$(PYTHON) -m unittest discover -s candy_soda_agent/tests -v
