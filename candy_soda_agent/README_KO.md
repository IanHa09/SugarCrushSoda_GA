# Candy Crush Soda LLM Agent

게임 화면을 캡처해 OpenAI 비전 모델로 분석하고, 안전 검증을 통과한 `swap`만 선택적으로 실행하는 실험용 에이전트입니다.

## 동작 범위

- F8: 현재 화면 한 번 분석
- F9: 안정된 새 보드를 자동 분석 시작/중지
- ESC: 프로그램 종료
- `playing + stable` 상태의 인접 셀 `swap`만 허용
- 팝업, 튜토리얼, 애니메이션, 불확실한 화면에서는 `wait`
- 클릭 직전 최신 보드, 중지 요청, 활성 게임 창을 재검증
- 행동 전후 화면 변화로 보상 추정 후 최근 메모리를 다음 분석에 반영
- 실행, 메모리, 종료 이유를 JSONL로 저장

기본값은 `DRY_RUN=true`이므로 API 분석은 실행하지만 마우스는 움직이지 않습니다.

현재 지원하지 않는 기능:

- 팝업·튜토리얼 자동 닫기
- 게임 규칙 기반의 정확한 성공 판정
- 모든 레벨의 완전 자동 클리어

보상은 실제 점수나 목표 달성이 아니라 행동 후 화면 변화량에 기반한 추정치입니다.

## 실행 구조

```text
화면 캡처 → LLM 분석 → 안전 검증 → wait 또는 드래그
    ↑                                  ↓
    └──── 최근 메모리 ← 보상·로그 ← 결과 캡처
```

## 설치

```bash
cd candy_soda_agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 설정

1. `config.py`에서 `BOARD_OFFSET`, `ROWS`, `COLS`를 게임에 맞춥니다.
2. `.env.example`을 참고해 환경변수를 설정합니다.

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
MONITOR_INDEX=1
CAPTURE_MODE=window
DRY_RUN=true
STORE_FULL_CAPTURE=false
GAME_WINDOW_TITLE=
```

`CAPTURE_MODE`는 `board`, `window`, `monitor`만 허용합니다. `monitor`는 전체 모니터를 API로 전송하므로 주의하세요.

실제 클릭은 `DRY_RUN=false`와 `GAME_WINDOW_TITLE`이 모두 필요합니다. macOS에서는 화면 기록과 손쉬운 사용 권한도 필요합니다.

## 실행

```bash
python calibrate_region.py  # 보드 좌표 확인
python preview_capture.py   # API 없이 캡처·격자 확인
python main.py              # 에이전트 실행
```

실제 클릭 전에는 반드시 `DRY_RUN=true`로 F8 분석과 좌표를 먼저 확인하세요.

## 출력

실행 위치와 관계없이 `candy_soda_agent/output/`에 저장됩니다.

```text
captures/       분석 전후 이미지
runs.jsonl      LLM 판단과 검증 결과
memory.jsonl    행동, 화면 변화, 보상, 학습 요약
sessions.jsonl  시작·종료·오류·API 한도 상태
```

## 파일 구조

```text
candy_soda_agent/
├─ main.py                 실행 루프
├─ agent.py, schemas.py    LLM 분석·응답 검증
├─ capture*.py             화면 캡처
├─ safety_guard.py         행동 안전 검사
├─ action_executor.py      마우스 드래그
├─ reward.py, storage.py   보상·메모리·로그
├─ config.py               좌표·실행 설정
└─ output/                 실행 결과
```

## 검증 상태

- 단위 테스트 13개 통과
- Python 문법, 전체 모듈 import, Pydantic/OpenAI 스키마 검증 통과
- 실제 캡처는 OS 화면 권한과 좌표 설정 필요
- 실제 API 호출과 마우스 드래그는 사용자 환경에서 별도 확인 필요
