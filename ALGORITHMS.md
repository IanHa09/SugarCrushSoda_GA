# SugarCrushSoda_GA — 주요 알고리즘·자료구조 정리

> 기준 코드: `candy_soda_agent/` (commit `f82e236`)
> 각 항목의 설명은 **그게 무엇인지 → 이 프로젝트에서 어디에 쓰는지** 순서로 적었습니다.

## 0. 한눈에 보기

**플레이 흐름**: 캡처 → 안정화 대기 → 격자 인식 → LLM 판단 → 로컬 검증 → 실행 → 결과 관찰 → 보상·메모리 저장
**조사(Autodrive) 흐름**: 캡처 → LLM 화면 분류 → 화면 ID 부여 → 그래프·원장 갱신 → 프론티어 계획 → 탭 → 보고서

| 기능 | 핵심 기법 |
| --- | --- |
| 게임 구조 파악 (Autodrive) | 방향 그래프, BFS 최단 경로, 프론티어 탐색, 루트 리셋 백트래킹, 상태 기계 |
| 화면이 같은지 판정 | 정규화 후 SHA-1 해시 ID, 대칭차집합 기반 허용 매칭 |
| 보드 격자 인식 | Sobel 투영 + 자기상관 / 국소 표준편차 + 모폴로지 + 연결 요소 |
| 같은 보드인지 판정 | dHash(지각 해시) + 해밍 거리 |
| 행동 결과 판정 | 프레임 차이 관찰 + 임계값 규칙 분류 + 삼진 보상 |
| LLM 연동 | 구조화 출력(Pydantic 스키마) + 격자 오버레이(시각 프롬프팅) |
| 저장 | JSONL 추가 전용 로그 + 파일 끝부터 역방향 읽기 |

---

## 1. 게임 구조 파악 (Autodrive) — `navigation/`

> **백트래킹에 대해**: 재귀 DFS식 백트래킹이 아니라, 경로가 어긋나면 **루트 화면으로 되돌아간 뒤 BFS로 경로를 다시 짜는** 방식입니다. 코드의 전략 이름은 `frontier_backtracking`입니다.

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 방향 그래프 | `schemas.NavigationGraph`, `graph.GraphStore` | 노드와 방향 있는 간선으로 관계를 표현하는 자료구조. 화면을 노드, 버튼 탭으로 생긴 전환을 간선으로 저장 (노드는 `dict`, 간선은 `list`). UI에 순환(사이클)이 있어 트리 대신 그래프를 씀 |
| 인접 리스트 | `graph._adjacency` | 노드마다 나가는 간선 목록을 모아 둔 것. BFS 직전에 간선 리스트에서 `dict[출발 노드, list[간선]]`로 만듦 |
| BFS (너비 우선 탐색) | `graph.bfs_nearest_matching`, `bfs_path` | 가까운 노드부터 차례로 방문하는 탐색. 가중치 없는 그래프에서 **간선 수 기준 최단 경로**를 보장함. `deque`(앞에서 꺼내기 O(1))와 `visited` 집합을 쓰고, 목표 조건을 함수(`is_target`)로 받아 "안 눌러본 버튼이 남은 가장 가까운 화면"을 찾음 |
| BFS 스패닝 트리 | `graph.bfs_spanning_tree` | 그래프에서 BFS가 처음 지나간 간선만 남긴 트리. 나머지 간선은 교차 링크로 따로 분리. 트리 깊이가 곧 루트에서 몇 번 눌러야 도달하는지이며, 보고서의 화면 계층에 사용 |
| 프론티어 기반 탐색 | `frontier.FrontierWalker.decide` | 아직 안 가 본 경계(프론티어)를 하나씩 없애 나가는 탐색. 여기서 프론티어는 **안 눌러본 (화면, 버튼) 쌍**. 현재 화면에 있으면 바로 누르고, 없으면 BFS로 가까운 프론티어 화면까지 이동. 전체 프론티어가 비면 종료(`converged`) |
| 백트래킹 (루트 리셋 + 재계획) | `frontier.report_replay_step`, `report_reset_step` | 실패하면 되돌아가 다른 방법을 시도하는 기법. 경로를 따라가다 팝업·광고 때문에 예상과 다른 화면에 도착하면 남은 경로를 버리고, home → back 순으로 루트까지 복귀한 뒤 BFS로 다시 계획. 재시도 상한(`FRONTIER_MAX_REPLAN_ATTEMPTS`, `HOME_RESET_MAX_BACK_TAPS`)을 넘으면 그 목표는 `blocked` 처리(가지치기) |
| 유한 상태 기계 (FSM) | `FrontierWalker`, `StepGoal.kind` | 정해진 몇 가지 상태 사이를 규칙에 따라 오가는 모델. `frontier / replay / reset / reobserve` 네 상태를 스텝마다 전환. 실제 탭은 하지 않는 순수 계획 로직이라 단위 테스트가 쉬움 |
| 큐로 경로 재생 | `FrontierWalker._replay_queue` | 선입선출 목록. BFS로 구한 간선을 앞에서부터 하나씩 실행하고 도착 화면을 검증 |
| 행동 원장 (해시 맵) | `ledger.LedgerStore` | 키로 값을 바로 찾는 사전 구조. 키 `"screen_id:action_key"` → `LedgerEntry`. **눌렀는지(`attempts`)와 결과(`verdict`)를 분리**해서, 눌러도 화면이 안 바뀌는 버튼이 계속 프론티어로 뽑히는 문제를 막음. 커버리지 = 눌러본 항목 / 전체 항목 |
| 연속 미관측 카운터 | `ledger.note_visit` | 연속 실패 횟수를 세어 일시적 누락을 무시하는 방식. LLM이 버튼을 가끔 빠뜨리므로 `FRONTIER_MAX_MISSES`번 연속 안 보일 때만 `not_visible`로 정리 |
| 선기록·후확정 + 복구 | `mark_attempted` → `finalize`, `LedgerStore.__init__` | 탭 직전에 시도 횟수를 먼저 저장하고, 결과는 다음 관찰 때 채움. 결과 없이 프로그램이 끊긴 항목은 재시작할 때 "안 눌러 봄"으로 되돌림 |
| 결과 지연 확정 | `navigator._Journey`, `_finalize_previous_attempt` | 탭 결과(`new_screen` / `known_screen` / `no_change`)는 다음 화면을 봐야 알 수 있으므로, 대기 중인 행동 정보를 다음 스텝까지 들고 가서 간선 연결과 판정에 씀 |

## 2. 화면이 같은지 판정 — `navigation/observer.py`, `policy.py`, `graph.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 정규화 + 콘텐츠 해시 ID | `observer.screen_id` | 내용으로 고유 ID를 만드는 방식. `screen_type`과 정렬한 버튼 라벨을 SHA-1로 해시해 앞 12자리를 화면 ID로 씀. 내용이 같으면 항상 같은 ID |
| 싱글턴 화면 타입 | `observer.SINGLETON_SCREEN_TYPES` | 지도·설정·상점·부스터 화면은 타입 하나당 노드 하나로 고정해서 같은 화면이 여러 노드로 쪼개지지 않게 함 |
| 라벨 정규화 (정규식 + 별칭 표) | `policy.canonical_action_label` | 표기가 다른 같은 뜻을 하나로 통일. 괄호 제거, 숫자를 `#`으로 치환, "gear / cog / 설정"을 모두 `settings`로 바꿈 |
| 허용 오차 매칭 (집합 연산) | `graph.find_tolerant_match`, `buttons_within_tolerance` | 두 버튼 집합에 공통 원소가 있고 대칭차집합(한쪽에만 있는 원소) 크기가 `SCREEN_MATCH_MAX_BUTTON_DIFF` 이하면 같은 화면으로 봄. 후보가 여럿이면 튜플 `(차이, -방문 수, 생성 순서)`를 사전식으로 비교해 선택 |
| ID 재매핑 + 간선 중복 제거 | `GraphStore._normalize_graph` | 옛 ID → 새 ID 사전으로 노드를 합치고, `(출발, 도착, action_key)` 튜플 집합으로 중복 간선을 제거 |

## 3. 보드 격자 인식 — `grid_detector.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| Sobel 엣지 + 1차원 투영 | `_estimate_axis` | Sobel은 밝기 변화(경계)를 찾는 필터. 경계 세기를 한 축으로 평균 내서 "경계가 몰린 위치" 신호를 만듦 |
| 추세 제거 + 표준화 | `_estimate_axis` | 가우시안 블러 결과를 빼서 배경의 느린 밝기 변화를 없애고, 평균 0·표준편차 1로 맞춤 |
| 자기상관 | `_estimate_axis` → `detect_grid_shape` | 신호를 일정 간격만큼 밀어 원래 신호와 곱한 평균. 이 값이 가장 큰 간격이 곧 칸 간격. 4~12칸 후보를 모두 비교하고 1·2위 점수 차로 신뢰도를 계산 (조사 모드 기록에 사용) |
| 국소 표준편차 (박스 필터) | `detect_board_geometry` | 픽셀 주변의 밝기 변동량. `blur(x²) − blur(x)²`로 한 번에 계산. 사탕이 있는 곳은 무늬가 많아 값이 큼 |
| 이진화 + 모폴로지 닫힘·열림 | `detect_board_geometry` | 모폴로지는 흑백 영역의 모양을 다듬는 연산. 닫힘으로 사탕 사이 틈을 메우고 열림으로 작은 잡티를 지움 |
| 연결 요소 레이블링 (8-연결) | `detect_board_geometry` (`cv2.connectedComponentsWithStats`) | 붙어 있는 픽셀을 덩어리별로 번호 매기는 알고리즘. 가장 넓은 덩어리를 보드로 보고, 한 칸 크기(`CELL_SIZE_PX`)로 나눠 행·열 수를 계산 (플레이 모드에 사용) |
| 폴백 패턴 | 두 함수 공통 | 칸 수가 범위 밖이거나 정사각형이 아니거나 예외가 나면 설정값(`ROWS`, `COLS`)과 실패 이유를 반환. 플레이 모드는 폴백이면 swap을 막음 |

## 4. 화면 캡처·안정화 — `capture.py`, `run_loop.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 프레임 차이 (평균 절대 차) | `capture.frame_difference` | 두 화면을 흑백 160×160으로 줄인 뒤 픽셀 차이의 평균을 0~1로 계산. "화면이 바뀌었나" 판단의 기본 신호 |
| 연속 안정 카운터 (디바운싱) | `AutoCaptureState.observe` | 짧은 흔들림을 무시하는 기법. 차이가 `STABLE_THRESHOLD` 미만인 상태가 `STABLE_FRAME_COUNT`번 이어져야 분석해서 애니메이션 중 호출을 막음 |
| 중복 억제·쿨다운·호출 예산 (스로틀링) | `AutoCaptureState.api_skip_reason` | 호출 빈도를 제한하는 기법. 직전에 보낸 보드와 비슷하면 건너뛰고, 최소 간격(`API_COOLDOWN`)과 최대 호출 수(`MAX_API_CALLS`)로 비용을 제한 |
| 좌표계 변환 | `capture.make_absolute_region` | 모니터 기준 상대 좌표에 모니터 원점을 더해 전체 화면 절대 좌표로 바꿈 |

## 5. LLM 연동 — `llm_request.py`, `image_utils.py`, `schemas.py`, `agent.py`, `survey.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 시각 프롬프팅 (격자 오버레이) | `image_utils.add_grid_overlay` | 이미지 위에 표시를 그려 모델의 답을 유도하는 기법. 보드에 빨간 격자와 행·열 번호를 그려 LLM이 픽셀 대신 (행, 열)로 답하게 함 |
| Base64 data URL | `image_utils.image_to_data_url` | 이미지를 문자열로 바꾸는 인코딩. 디스크에 저장하지 않고 PNG를 바로 API에 보냄 |
| 구조화 출력 | `llm_request.request_structured_vision` | 모델 응답을 정해진 JSON 스키마로 받는 기능. `client.responses.parse(text_format=Pydantic 모델)`을 호출하면 파싱된 객체가 반환됨 |
| Pydantic 스키마 검증 | `schemas.py` | 데이터 형식을 선언하고 자동 검증하는 라이브러리. `Literal`로 허용 값을 나열하고, `Field(ge=, le=)`로 범위를 제한하며, `@field_validator(mode="after")`로 너무 긴 값은 에러 대신 잘라냄 |
| 최근 기록 요약 주입 | `agent._format_memory_context`, `survey._format_survey_context` | 최근 실패 수나 조사 기록을 짧은 JSON으로 요약해 프롬프트에 넣음 |

## 6. 행동 결정·안전 검증 — `agent.py`, `safety_guard.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 맨해튼 거리 = 1 | `validate_decision`, `_promote_best_candidate` | 가로·세로 차이의 합. `abs(Δ행) + abs(Δ열) == 1`이면 상하좌우로 붙은 칸(대각선 제외) |
| 방향 무관 정규화 튜플 | `validate_decision`, `storage.load_failed_moves_for_board` | `(*min(a, b), *max(a, b))`로 A↔B와 B↔A를 같은 수로 취급. 금지 수는 `set`에 담아 O(1)로 조회 |
| 탐욕 선택 (greedy) | `_promote_best_candidate` | 매 순간 가장 좋아 보이는 것을 고르는 방식. LLM이 wait를 골랐어도 후보를 확신도 내림차순으로 정렬해 조건을 통과한 첫 swap을 채택 |
| 가드 절 (조기 반환) | `validate_decision` | 조건을 하나씩 검사하다 실패하면 바로 반환. UI 상태 → 보드 안정 → 좌표 범위 → 인접성 → 실패 이력 → 확신도 순 |
| 협조적 취소 | `safety_guard.make_cancel_check`, `action_executor` | 작업이 스스로 중지 요청을 확인하고 멈추는 방식. 실행 직전 여러 지점에서 콜백으로 확인하고, 요청이 있으면 예외를 냄 |

## 7. 실행 — `coordinate_mapper.py`, `action_executor.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 셀 → 픽셀 선형 변환 | `coordinate_mapper.cell_center` | `left + (col − 0.5) × 칸 너비`로 칸 중심의 화면 좌표를 계산 |
| 정규화 좌표 역변환 | `execute_button_tap` | LLM이 준 0~1 좌표에 캡처 영역 크기를 곱하고 원점을 더해 실제 좌표로 바꿈 |
| 타임아웃 폴링 | `_wait_until_active` | 조건이 맞을 때까지 짧게 반복 확인하되 제한 시간(`time.monotonic()` 기준)을 둠 |
| 다단계 폴백 | `activate_application` | 앞 방법이 실패하면 다음 방법을 시도. AppKit → `open -b/-a` → `osascript` 순서로 게임 창을 앞으로 가져옴 |
| 경계 검사 + FAILSAFE | `execute_decision`, `execute_button_tap` | 계산한 좌표가 영역 밖이면 차단하고, `pyautogui.FAILSAFE`로 마우스를 화면 모서리로 옮기면 비상 정지 |

## 8. 결과 관찰·보상 — `play_session.py`, `reward.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 관찰 루프 (최댓값 추적) | `observe_action_result` | 드래그 후 일정 간격으로 캡처하며 `peak_change`(가장 크게 바뀐 정도), `final_change`(최종 변화), `settled`(안정 여부)를 기록 |
| 임계값 규칙 분류 | `reward.classify_action_outcome` | 기준값과 비교하는 if 규칙으로 분류. 최종 변화가 크면 accepted, 바뀌었다 돌아오면 rejected, 거의 안 바뀌면 no_change, 그 외는 unclear |
| 삼진 보상 (−1 / 0 / +1) | `reward.evaluate_reward` | 확실한 성공과 실패만 ±1로 두고, 환경 오류나 불확실한 경우는 0으로 두어 잘못 학습하지 않게 함 |

## 9. 같은 보드 기억 — `image_utils.py`, `storage.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| dHash (차이 해시) | `image_utils.board_fingerprint` | 비슷한 이미지면 비슷한 값이 나오는 지각 해시의 한 종류. 흑백 17×16으로 줄이고 옆 픽셀보다 밝은지 비교해 256비트를 만든 뒤 16진수 문자열로 저장. 작은 반짝임에는 거의 영향을 받지 않음 |
| 해밍 거리 (XOR + 비트 세기) | `image_utils.fingerprint_distance` | 서로 다른 비트 수. 전체 비트 수로 나눠 0~1로 정규화. `int.bit_count`가 없으면 `bin().count("1")`로 대체 |
| 근사 매칭 선형 탐색 | `storage.load_failed_moves_for_board` | 최근 기록을 최신순으로 훑어 거리가 `BOARD_FINGERPRINT_MAX_DISTANCE` 이하인 보드의 실패 수(rejected, no_change)만 모아 금지 목록을 만듦 |

## 10. 조사 기록·중복 제거 — `survey_utils.py`, `storage.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 텍스트 정규화 | `survey_utils.normalize_text` | `casefold`와 정규식으로 대소문자, 기호, 공백 차이를 없앰 |
| 콘텐츠 해시 키 | `stable_hash`, `element_key`, `button_key` | 정규화한 문자열 조각을 구분자로 이어 SHA-1로 해시. 같은 요소는 항상 같은 키 |
| 화면 시그니처 | `survey_utils.screen_signature` | 화면 타입, 정렬한 텍스트, 요소·버튼 키를 합쳐 해시한 화면 대표값 |
| 집합으로 신규·중복 분리 | `storage._known_survey_keys`, `save_survey_record` | 최근 기록의 키를 `set`에 모아 두고, 이번 키가 들어 있는지로 새 항목과 중복 항목을 나눔 |
| 허용 목록 필터 + 다중 키 정렬 | `survey_utils.safe_button_candidates` | 안전한 역할의 버튼만 남기고 `(역할 우선순위, −확신도)` 순으로 정렬. 결제·광고·로그인 버튼은 기록만 하고 누르지 않음 |

## 11. 저장 — `storage.py`, `config.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| JSONL 추가 전용 로그 | `storage._append_jsonl` | 한 줄에 JSON 하나씩 쓰는 형식. 덮어쓰지 않고 뒤에 붙이기만 해서 중간에 끊겨도 앞 기록은 안전 |
| 파일 끝부터 역방향 읽기 | `storage._read_tail_lines` | 파일 끝에서 64KB씩 거꾸로 읽어 마지막 N줄만 가져옴. 파일이 커져도 필요한 만큼만 읽음 |
| 손상된 줄 건너뛰기 | `storage._parse_jsonl_records` | `JSONDecodeError`가 나면 경고만 출력하고 다음 줄로 넘어감 |
| 포인터 파일로 이어서 실행 | `config._resolve_run_id` | `current_run.txt`에 run_id를 적어 두어, 여러 번 실행해도 같은 그래프와 원장에 이어서 기록 |

## 12. 보고서 생성 — `survey_report.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 재귀 전위 순회 (DFS) | `_append_screen_hierarchy._walk` | 부모를 먼저 출력하고 자식으로 내려가는 순회. BFS 스패닝 트리를 깊이만큼 들여쓴 목록으로 출력 |
| Mermaid 다이어그램 생성 | `_append_game_structure_diagram` | 텍스트로 그리는 다이어그램 문법. 트리 간선은 실선 `-->`, 교차 링크는 점선 `-.->`로 그리고, 노드가 `MERMAID_MAX_NODES`를 넘으면 생략 |
| 동시출현 집계 | `generate_survey_markdown` | 어떤 요소가 어느 화면들에 함께 나왔는지 세는 것. `defaultdict(dict)`와 `set`으로 요소별 등장 화면을 모두 모음 |
| 그룹핑 + 커버리지 | `_append_frontier_section` | `defaultdict(list)`로 화면별 미시도 / blocked / not_visible 항목을 나누고 탐색 비율을 표시 |

## 13. 실행 제어·설정 — `hotkeys.py`, `run_loop.py`, `cli.py`, `config.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| `threading.Event` 플래그 | `hotkeys.py` | 스레드 사이에 켜짐·꺼짐 상태를 안전하게 공유하는 객체. 키보드 콜백과 메인 루프가 시작·중지·종료 상태를 공유 |
| 이벤트 폴링 메인 루프 | `run_loop.run` | `while not EXIT_REQUESTED` 안에서 단발 분석과 자동 분석 요청을 확인하며 반복 |
| 클로저 콜백 주입 | `safety_guard.make_cancel_check` | 필요한 값을 묶어 둔 함수(`lambda`)를 넘겨, 하위 함수가 전역 상태를 몰라도 취소 여부를 확인할 수 있게 함 |
| 옵션 조합 검증 | `cli.build_run_plan` | `argparse`로 읽은 옵션 중 함께 쓸 수 없는 조합을 막고 불변 `RunPlan`으로 확정 |
| 환경변수 설정 + 즉시 검증 | `config.py` | `.env` 값을 `os.getenv`로 읽음. `_env_bool` 등은 잘못된 값이면 import 시점에 바로 에러를 냄(fail-fast) |

## 14. 자주 쓰인 파이썬 문법

| 문법 | 예시 위치 | 설명 |
| --- | --- | --- |
| `@dataclass(frozen=True)` | `GridShape`, `CaptureBundle`, `RewardResult` | 값만 담는 불변 객체를 짧게 정의 |
| `field(default_factory=...)` | `FrontierWalker` | 리스트·사전 같은 가변 기본값을 인스턴스마다 새로 만듦 |
| 타입 힌트 (`Literal`, `Callable`, 유니온) | 대부분의 파일 | `from __future__ import annotations`와 함께 써서 함수 입출력 형식을 명시 |
| 키워드 전용 인자 `*` | `detect_grid_shape(..., *, enabled=...)` | `*` 뒤 인자는 이름을 붙여야만 넘길 수 있어 순서 실수를 막음 |
| `next((... for ...), None)` | `navigator._button_for_action_key` | 조건에 맞는 첫 항목을 찾고, 없으면 `None` 반환 |
| 집합 연산 `&`, `^`, `.difference()` | `buttons_within_tolerance`, `cell_center` | 교집합, 대칭차집합, 빠진 키 검사 |
| 튜플 정렬 키 | `safe_button_candidates`, `find_tolerant_match` | 여러 기준을 앞에서부터 차례로 비교 |
| 지연 import | `pyautogui`, `keyboard`, `AppKit` | 실제로 필요할 때만 불러와 플랫폼 의존성과 macOS segfault를 피함 |
| `with`, `try / finally` | `mss.MSS()`, `release_hotkeys` | 오류가 나도 자원 정리를 보장 |

## 15. 테스트 — `tests/`

| 기법 | 설명 |
| --- | --- |
| `unittest` | 파이썬 기본 테스트 프레임워크 (`make test`로 실행) |
| `unittest.mock.patch`, `patch.object` | 설정값, API, 마우스 호출을 가짜로 바꿔 네트워크나 실제 입력 없이 검증 |
| `types.SimpleNamespace` | 속성만 가진 간단한 가짜 객체. LLM 응답 같은 입력을 흉내 냄 |
| `tempfile.TemporaryDirectory` | 파일 저장 테스트를 임시 폴더에서 실행해 실제 기록과 섞이지 않게 함 |
