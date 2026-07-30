# Candy Crush Soda LLM Agent

## 프로젝트 목표

이 프로젝트는 Candy Crush Soda를 완전히 자동으로 클리어하는 프로그램이 아닙니다.

구현 범위는 다음과 같습니다.

```text
게임 화면
→ MSS 자동 캡처
→ 보드 영역 자르기
→ 행·열 격자 추가
→ Base64 이미지로 OpenAI API 전송
→ swap 또는 wait 행동을 JSON으로 출력
→ 이미지와 결과를 실험 로그로 저장
```

손으로 캡처 도구를 열어 파일을 저장할 필요는 없습니다.

- F8을 누르면 프로그램이 현재 보드를 한 번 캡처합니다.
- F9를 누르면 프로그램이 안정된 새 보드를 자동으로 찾아 분석합니다.
- 실제 마우스 클릭은 현재 버전에 포함하지 않았습니다.

---

## 프로젝트 구조

```text
candy_soda_agent/
├─ main.py                 # 프로그램 실행 및 F8/F9 반복 루프
├─ config.py               # 모니터, 보드 좌표, 행·열, 모델 설정
├─ calibrate_region.py     # 마우스로 게임 보드 영역 선택
├─ preview_capture.py      # API 없이 캡처·격자 확인
├─ capture.py              # MSS 캡처와 화면 차이 계산
├─ image_utils.py          # 격자 추가와 Base64 변환
├─ schemas.py              # LLM JSON 응답 구조
├─ agent.py                # OpenAI API 호출과 행동 검증
├─ usage.py                # API 호출·토큰 예산과 사용량 집계
├─ memory.py               # 영상 경험을 짧은 전략 메모리로 압축
├─ storage.py              # 이미지 및 JSONL 로그 저장
├─ calibrate_video.py      # 영상 속 보드·점수 영역 선택
├─ video_pipeline.py       # 영상 학습·평가 이벤트 추출
├─ evaluate_video.py       # 메모리 크기별 정확도 그래프
├─ tests/                  # API를 호출하지 않는 로컬 테스트
├─ requirements.txt        # 설치할 라이브러리
├─ .gitignore              # API 관련 파일과 결과 제외
└─ output/                 # 실행 후 자동 생성
   ├─ preview_raw.png
   ├─ preview_grid.png
   ├─ runs.jsonl
   └─ captures/
```

---

## 1단계: 프로젝트 폴더 열기

압축을 푼 뒤 PowerShell에서 프로젝트 폴더로 이동합니다.

```powershell
cd C:\Users\사용자이름\Desktop\candy_soda_agent
```

---

## 2단계: 가상환경과 라이브러리 설치

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

다음에 다시 작업할 때는 프로젝트 폴더에서 아래 명령만 먼저 실행합니다.

```powershell
.venv\Scripts\Activate.ps1
```

---

## 3단계: 게임 화면 준비

1. Candy Crush Soda 또는 허가받은 플레이 영상을 띄웁니다.
2. 가능하면 항상 같은 모니터와 같은 창 크기를 사용합니다.
3. 과제 초기에는 한 레벨만 고정합니다.
4. 브라우저 주소창이나 점수 UI보다 실제 퍼즐 보드가 중심이 되게 합니다.

---

## 4단계: 모니터 번호 정하기

`config.py`를 열고 다음 값을 확인합니다.

```python
MONITOR_INDEX = 2
```

듀얼 모니터에서 게임이 두 번째 모니터에 있으면 보통 2입니다.
정확한 번호는 다음 단계의 `calibrate_region.py`가 콘솔에 출력합니다.

---

## 5단계: 게임 보드 영역 선택

```powershell
python calibrate_region.py
```

창이 뜨면 실제 사탕 보드만 드래그한 뒤 Enter 또는 Space를 누릅니다.

콘솔에 다음과 비슷한 값이 출력됩니다.

```python
BOARD_OFFSET = {
    "left": 426,
    "top": 118,
    "width": 612,
    "height": 748,
}
```

이 값을 `config.py`의 기존 `BOARD_OFFSET`과 교체합니다.

창이 엉뚱한 모니터를 캡처했다면 `MONITOR_INDEX`를 바꾸고 다시 실행합니다.

---

## 6단계: 행과 열 설정

고정한 레벨의 바깥쪽 직사각형을 기준으로 칸 수를 셉니다.

```python
ROWS = 9
COLS = 9
```

불규칙한 빈칸이 있어도 바깥쪽 행·열 좌표는 유지합니다.
처음에는 여러 레벨을 섞지 않는 것이 좋습니다.

---

## 7단계: API 없이 캡처 확인

```powershell
python preview_capture.py
```

확인할 내용:

1. `Raw board capture` 창에 보드만 들어오는지
2. `Grid sent to LLM`의 격자선이 실제 칸 경계와 비슷한지
3. 행 번호가 위에서 아래로 증가하는지
4. 열 번호가 왼쪽에서 오른쪽으로 증가하는지

결과는 다음 파일로도 저장됩니다.

```text
output/preview_raw.png
output/preview_grid.png
```

이 단계가 틀리면 API를 연결하기 전에 `BOARD_OFFSET`, `ROWS`, `COLS`를 고칩니다.

---

## 8단계: OpenAI API Key 설정

프로젝트의 `.env` 파일에 다음처럼 저장할 수 있습니다.

```dotenv
OPENAI_API_KEY=발급받은_API_KEY
OPENAI_MODEL=gpt-4o-mini-2024-07-18
OPENAI_IMAGE_DETAIL=low
```

현재 PowerShell 창에서만 사용할 때:

```powershell
$env:OPENAI_API_KEY="발급받은_API_KEY"
```

모델을 바꾸고 싶을 때:

```powershell
$env:OPENAI_MODEL="gpt-4o-mini-2024-07-18"
```

`.env`는 `.gitignore`에 포함되어 있습니다. 그래도 API Key를
Python 파일에 직접 작성하거나 GitHub에 올리지 마세요.

---

## 9단계: F8 한 번 분석부터 시험

```powershell
python main.py
```

게임 보드의 애니메이션이 멈춘 순간 F8을 누릅니다.

프로그램이 자동으로 다음 작업을 합니다.

```text
현재 보드 캡처
→ 격자 추가
→ Base64 변환
→ OpenAI API 요청
→ JSON 출력
→ 좌표 범위와 인접 여부 검증
→ output 폴더에 기록
```

예상 출력:

```json
{
  "board_status": "stable",
  "board_summary": "9×9 보드가 안정된 상태다.",
  "move_type": "normal_match",
  "visible_elements": ["파란 사탕", "보라 사탕", "장애물"],
  "action": "swap",
  "source": {"row": 4, "col": 5},
  "target": {"row": 4, "col": 6},
  "confidence": 0.78,
  "reason": "교환하면 가로 방향의 세 칸 매치가 만들어질 가능성이 높다."
}
```

F8 분석이 5~10회 정상적으로 된 뒤에 자동 모드로 넘어갑니다.

---

## 10단계: F9 자동 분석

프로그램 실행 중 F9을 누르면 자동 모드가 시작됩니다.

자동 모드는 다음 조건을 모두 만족할 때만 API를 호출합니다.

1. 연속 화면 변화가 작음
2. 설정한 횟수만큼 화면이 안정됨
3. 직전에 분석한 보드와 다름
4. 이전 API 요청 후 일정 시간이 지남

다시 F9을 누르면 자동 모드가 멈춥니다.
ESC를 누르면 프로그램이 종료됩니다.

F8 수동 분석도 같은 예산에 포함됩니다. 기본값은 120회 또는
누적 450,000토큰이며 먼저 도달한 한도에서 추가 호출을 막습니다.

```python
MAX_API_CALLS = 120
MAX_TOTAL_TOKENS = 450_000
```

---

## 11단계: 임계값 조절

### 애니메이션이 멈췄는데도 분석하지 않을 때

`config.py`에서 조금 높입니다.

```python
STABLE_THRESHOLD = 0.04
```

### 같은 화면을 반복 분석할 때

```python
NEW_BOARD_THRESHOLD = 0.06
```

### API 호출 간격을 늘리고 싶을 때

```python
API_COOLDOWN = 30.0
```

처음부터 세 값을 모두 바꾸지 말고 한 번에 하나씩 조절합니다.

---

## 12단계: 결과 확인

```text
output/captures/
```

각 분석마다 다음 두 이미지가 저장됩니다.

- `_raw.png`: 실제 캡처
- `_grid.png`: LLM에 보낸 격자 이미지

```text
output/runs.jsonl
```

각 줄에는 이미지 경로, 모델, LLM 응답, 검증 결과와 실제
입력·출력·reasoning 토큰 및 예상 비용이 저장됩니다.

발표에서는 다음 자료로 사용할 수 있습니다.

- 성공한 swap 사례
- wait가 나온 불확실한 사례
- 인접하지 않은 좌표를 검증에서 거른 사례
- crop 전후 또는 격자 전후 정확도 비교

---

## 권장 작업 순서 요약

```text
1. 라이브러리 설치
2. 게임 또는 영상 화면 고정
3. calibrate_region.py로 보드 선택
4. config.py에 좌표·행·열 입력
5. preview_capture.py로 확인
6. API Key 설정
7. main.py 실행
8. F8로 한 장씩 시험
9. 프롬프트와 좌표 수정
10. F9 자동 모드 시험
11. runs.jsonl을 이용해 결과 평가
```

---

## 영상 학습·평가

제공된 영상은 시작 부분에 로그인 화면이 있으므로 기본 보정 시점은
30초입니다. 처리할 레벨이 보이는 시점으로 바꿀 수 있습니다.

```powershell
python calibrate_video.py --time 30
```

출력된 `VIDEO_BOARD_ROI`, `VIDEO_SCORE_ROI`를 `config.py`에
복사합니다. 고정 ROI와 9×9 격자를 사용하므로 서로 다른 레벨은
`--start`, `--end`로 나눠 처리하는 것이 안전합니다.

학습 경험 추출:

```powershell
python video_pipeline.py --mode train --start 20 --end 120 --max-events 10 --label level_1
```

메모리를 사용한 평가:

```powershell
python video_pipeline.py --mode evaluate --start 20 --end 120 --max-events 10 --memory-limit 20 --label memory_20
python evaluate_video.py
```

영상 모드는 유효 이벤트 수와 별도로 다음 두 한도를 적용합니다.

```python
MAX_VIDEO_API_CALLS = 60
MAX_VIDEO_TOTAL_TOKENS = 500_000
```

무효 이벤트도 API 호출을 소비합니다. 실행별 호출 수, 실제 토큰,
예상 비용과 종료 사유는 `output/video_usage.jsonl`에 저장됩니다.

API를 호출하지 않는 로컬 테스트:

```powershell
python -m unittest discover -s tests -v
```
