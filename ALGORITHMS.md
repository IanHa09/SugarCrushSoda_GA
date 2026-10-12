# SugarCrushSoda_GA — 주요 알고리즘·자료구조 정리

> 기준 코드: `candy_soda_agent/` (2026-10-11 작업본)
> 각 항목의 설명은 **그게 무엇인지 → 이 프로젝트에서 어디에 쓰는지** 순서로 적었습니다.
> 2026-09-18판(commit `f82e236`) 이후 추가: 클릭 정확도(3장), 탈출 사다리·플레이 회피(4장), 공통 UI(5장), 게임 지식 파일(6장), HOG 화면 매칭(2장), 탐색 지표(17장). 빠진 것: 싱글턴 화면 타입(타입 이름으로 노드 묶기). 화면 판정의 1순위는 이미지(픽셀 → HOG → dHash)로 바뀌었습니다.

## 0. 한눈에 보기

**플레이 흐름**: 캡처 → 안정화 대기 → 격자 인식 → LLM 판단 → 로컬 검증 → 실행 → 결과 관찰 → 보상·메모리 저장
**조사(Autodrive) 흐름**: 캡처 → LLM 화면 분류(게임 지식 프롬프트) → 화면 식별(픽셀 → HOG → dHash → 버튼) → 그래프·원장 갱신 → 프론티어 계획 → 확대 재확인 → 탭 → 화면 안정 대기·탭 효과 판정 → (갇히면 탈출 사다리, 플레이 화면이면 나오기) → 보고서·지표

| 기능 | 핵심 기법 |
| --- | --- |
| 게임 구조 파악 (Autodrive) | 방향 그래프, BFS 최단 경로(회피 노드·지름길 간선), 프론티어 탐색, 루트 리셋 백트래킹, 상태 기계 |
| 화면이 같은지 판정 | 픽셀 차이 → HOG 특징 + 코사인 거리(행렬·벡터 곱) → dHash 해밍 거리 → 해시 ID·대칭차집합, 지문 거부권 |
| 클릭 정확도 | 픽셀 bbox, 잘라 확대 + 체스판 격자(시각 프롬프팅), 칸·픽셀 교차 검증, 탭 뒤 안정 대기 |
| 갇힘 탈출·플레이 회피 | 단계 순서 상태 기계(탈출 사다리), 낱말 단위 연속 일치, adb 명령 |
| 공통 UI (네비게이션 바) | 행 프로파일 비교 + 런 길이, k번째 큰 값, 중앙값 기준 위치 군집, 지름길 간선 |
| 게임 지식 파일 | 계층 병합(기본 → 장르 → 게임), 동적 스키마(`Literal`), 즉시 검증 |
| 보드 격자 인식 | Sobel 투영 + 자기상관 / 국소 표준편차 + 모폴로지 + 연결 요소 |
| 같은 보드인지 판정 | dHash(지각 해시) + 해밍 거리 |
| 행동 결과 판정 | 프레임 차이 관찰 + 임계값 규칙 분류 + 삼진 보상 |
| LLM 연동 | 구조화 출력(Pydantic 스키마) + 격자 오버레이(시각 프롬프팅), 모델 이원화 |
| 저장·지표 | JSONL 추가 전용 로그 + 역방향 읽기, run 폴더 분리, 세션별 적중률·누적 집합 |

---

## 1. 게임 구조 파악 (Autodrive) — `navigation/`

> **백트래킹에 대해**: 재귀 DFS식 백트래킹이 아니라, 경로가 어긋나면 **루트 화면으로 되돌아간 뒤 BFS로 경로를 다시 짜는** 방식입니다. 코드의 전략 이름은 `frontier_backtracking`입니다.

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 방향 그래프 | `schemas.NavigationGraph`, `graph.GraphStore` | 노드와 방향 있는 간선으로 관계를 표현하는 자료구조. 화면을 노드, 버튼 탭으로 생긴 전환을 간선으로 저장 (노드는 `dict`, 간선은 `list`). UI에 순환(사이클)이 있어 트리 대신 그래프를 씀 |
| 인접 리스트 | `graph._adjacency` | 노드마다 나가는 간선 목록을 모아 둔 것. BFS 직전에 간선 리스트에서 `dict[출발 노드, list[간선]]`로 만듦. 그래프 파일에 없는 지름길 간선(`extra_edges`, 5장)을 뒤에 붙일 수 있음 |
| BFS (너비 우선 탐색) | `graph.bfs_nearest_matching`, `bfs_path` | 가까운 노드부터 차례로 방문하는 탐색. 가중치 없는 그래프에서 **간선 수 기준 최단 경로**를 보장함. `deque`(앞에서 꺼내기 O(1))와 `visited` 집합을 쓰고, 목표 조건을 함수(`is_target`)로 받아 "안 눌러본 버튼이 남은 가장 가까운 화면"을 찾음. `avoid` 함수가 True인 노드(플레이 화면, 플레이에서 나오는 도중 본 화면)는 지나가지도 도착하지도 않음 |
| BFS 스패닝 트리 | `graph.bfs_spanning_tree` | 그래프에서 BFS가 처음 지나간 간선만 남긴 트리. 나머지 간선은 교차 링크로 따로 분리. 트리 깊이가 곧 루트에서 몇 번 눌러야 도달하는지이며, 보고서의 화면 계층에 사용 |
| 도달 불가 노드 | `graph.unreached_from` | 스패닝 트리에 못 들어간 노드 = 루트에서 가는 길을 모르는 화면. 보고서와 지표의 진단에 씀 |
| 프론티어 기반 탐색 | `frontier.FrontierWalker.decide` | 아직 안 가 본 경계(프론티어)를 하나씩 없애 나가는 탐색. 여기서 프론티어는 **안 눌러본 (화면, 버튼) 쌍**. 현재 화면에 있으면 바로 누르고, 없으면 BFS로 가까운 프론티어 화면까지 이동. 미시도 항목이 원장에만 있고 이번 후보에 안 보이면 이동 대신 `reobserve`(그 자리에서 다시 보기). 공통 버튼(5장)은 `__global__` 항목 하나로 세고, 아직 안 눌렀으면 그 버튼이 보였던 화면들을 목표로 삼음. 전체 프론티어가 비면 `converged`, 남았는데 길을 모르면 `unreachable` |
| 백트래킹 (루트 리셋 + 재계획) | `frontier.report_replay_step`, `report_reset_step` | 실패하면 되돌아가 다른 방법을 시도하는 기법. 경로를 따라가다 팝업·광고 때문에 예상과 다른 화면에 도착하면 남은 경로를 버리고, home → back 순으로 루트까지 복귀한 뒤 BFS로 다시 계획. 홈/뒤로 버튼이 안 보이면 탈출 사다리(4장)를 씀. 재시도 상한(`FRONTIER_MAX_REPLAN_ATTEMPTS`, `HOME_RESET_MAX_BACK_TAPS`)을 넘으면 그 목표는 `blocked` 처리(가지치기)하고 `_abandoned` 집합에 넣어 다시 고르지 않음 |
| 시작 화면 정렬 | `FrontierWalker.begin_reset_to_root` | 이어서 하는 run(또는 게임 재시작 직후)이 루트가 아닌 화면에서 시작하면 목표 없이 루트로 먼저 돌아감. 루트와 이어지지 않는 섬이 생기는 것을 막음 |
| 유한 상태 기계 (FSM) | `FrontierWalker`, `StepGoal.kind` | 정해진 몇 가지 상태 사이를 규칙에 따라 오가는 모델. `frontier / replay / reset / reobserve` 네 상태를 스텝마다 전환. 실제 탭은 하지 않는 순수 계획 로직이라 단위 테스트가 쉬움 |
| 큐로 경로 재생 | `FrontierWalker._replay_queue` | 선입선출 목록. BFS로 구한 간선을 앞에서부터 하나씩 실행하고 도착 화면을 검증 |
| 행동 원장 (해시 맵) | `ledger.LedgerStore`, `is_open_entry` | 키 `"screen_id:action_key"` → `LedgerEntry`. **눌렀는지(`attempts`)와 결과(`verdict`)를 분리**해서, 눌러도 화면이 안 바뀌는 버튼이 계속 프론티어로 뽑히는 문제를 막음. 프론티어 = 아직 안 눌렀고 탭 없이 닫히지도 않은 항목(`CLOSED_WITHOUT_TAP` 밖) 또는 빗나감(`missed`) 재시도 대기 항목. verdict는 `new_screen / known_screen / no_change / blocked / failed / not_visible / missed / not_found / global / play_entry` |
| 빗나감 재시도 | `ledger.record_miss` | 눌렀는데 픽셀이 그대로면 바로 닫지 않고 `missed`(프론티어 유지)로 두었다가, `TAP_MISS_RETRIES`번 넘게 그대로면 `no_change`로 닫음 |
| 실패 항목 재시도 (상한 카운터) | `FrontierWalker.reopen_failed`, `ledger.reopen_failed` | 프론티어가 비어 `converged`가 나오면 끝내기 전에 실패로 닫힌 항목(`no_change / not_found / blocked`) 중 지금 화면에서 BFS 경로가 있는 것만 다시 엶(`attempts=0`). 항목마다 `retries`를 원장에 세어 `FRONTIER_RETRY_MAX`번까지만 열므로 run을 넘어도 무한 반복하지 않음. 남은 실패 수는 종료 메시지에 표시 |
| 연속 미관측 카운터 | `ledger.note_visit` | 연속 실패 횟수를 세어 일시적 누락을 무시하는 방식. LLM이 버튼을 가끔 빠뜨리므로 `FRONTIER_MAX_MISSES`번 연속 안 보일 때만 `not_visible`로 정리 |
| 선기록·후확정 + 복구 | `mark_attempted` → `finalize`, `LedgerStore.__init__` | 탭 직전에 시도 횟수를 먼저 저장하고, 결과는 다음 관찰 때 채움. 결과 없이 프로그램이 끊긴 항목은 재시작할 때 "안 눌러 봄"으로 되돌림 |
| 결과 지연 확정 | `navigator._Journey`, `_finalize_previous_attempt` | 탭 결과는 다음 화면을 봐야 알 수 있으므로, 대기 중인 행동 정보를 다음 스텝까지 들고 가서 간선 연결과 판정에 씀. 다음 캡처가 직전 캡처(`prev_image`)와 픽셀로 같으면 간선을 만들지 않고 빗나감으로 처리하며, 경로 재생 중 빗나갔으면 원장 판정을 탭 전 값으로 되돌림 |
| 집합 차집합으로 옛 run 차단 | `navigator.run_autodrive` | (그래프에 있는 화면 타입 집합) − (지금 분류표 타입 집합)이 비어 있지 않으면 예전 타입으로 만든 run이라, API를 쓰기 전에 멈추고 새 run을 안내 |

## 2. 화면이 같은지 판정 — `navigation/graph.py`, `screen_index.py`, `observer.py`, `policy.py`

새 관찰은 아래 순서로 기존 노드에 붙이고, 어디에도 안 맞으면 새 노드를 만듭니다(`GraphStore.observe`, 0번만 `navigator`).

| 순서 | 기준 | 비교 대상 | 위치 |
| --- | --- | --- | --- |
| 0 | 직전 캡처와 픽셀 차이 < `SAME_SCREEN_PIXEL_THRESHOLD`(0.01) | 직전 노드 | `tap_effect.same_screen` → `GraphStore.revisit` |
| 1 | HOG 코사인 거리 ≤ `SCREEN_HOG_MAX_DISTANCE`(0.15) | 모든 노드 (타입 무관) | `GraphStore._hog_match` |
| 2 | dHash 해밍 거리 ≤ `SCREEN_FINGERPRINT_MAX_DISTANCE`(0.05) | 같은 화면 타입 | `find_fingerprint_match` |
| 3 | 화면 타입 + 버튼 목록 해시 ID 일치 (지문이 멀면 거부) | 같은 ID | `observer.screen_id` |
| 4 | 버튼 1개 차이 허용 (지문이 멀면 거부) | 같은 화면 타입 | `find_tolerant_match` |

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| HOG (방향 그래디언트 히스토그램) | `screen_index.hog_feature` | 이미지의 "모양"을 벡터로 바꾸는 특징 추출법. 흑백 128×256으로 줄이고 Sobel(ksize=1)로 픽셀마다 밝기 변화의 세기와 방향(부호 없는 0~180도)을 구함 → 16px 칸마다 방향 9칸 히스토그램(`np.add.at`으로 한 번에 누적) → 이웃 2×2 칸을 이어 붙여 블록마다 L2 정규화 → 길이 3780 벡터를 다시 길이 1로 정규화. 블록 정규화 덕분에 화면 전체가 어두워져도 값이 거의 안 바뀜 |
| 코사인 거리 + 행렬·벡터 곱 최근접 탐색 | `screen_index.ScreenIndex.nearest` | 길이 1인 벡터끼리는 내적 = 코사인 유사도. 대표 화면 벡터를 (노드 수 × 3780) 행렬로 쌓아 두고 새 벡터와 한 번 곱해 모든 노드와의 거리(1 − 유사도)를 구한 뒤 `argmin`. 화면 타입을 보지 않아서 LLM이 같은 화면을 다른 타입으로 불러도 같은 노드로 모임 |
| 지연 생성 색인 + 무효화 | `GraphStore._screen_index`, `set_chrome_bands` | 처음 쓸 때 대표 이미지들로 색인을 만들고(장당 약 5ms), 새 노드는 행을 추가. 공통 띠가 바뀌면 비교 영역이 달라지므로 색인을 지워 다음에 다시 만듦 |
| 임계값 실측 | `config.SCREEN_HOG_MAX_DISTANCE` | run_2026-10-02_08-33-21 캡처 31장: 같은 노드끼리 최대 0.071, 다른 화면끼리 최소 0.272(지도 vs 지도 위 하트 팝업) → 사이의 0.15. 기록을 다시 넣어 보면 지문만 쓰면 노드 10개(지도가 둘로 갈림), HOG를 켜면 9개. 거리는 `journeys.jsonl`의 `hog_nearest`에 남음 |
| dHash 화면 지문 | `find_fingerprint_match` | 13장의 dHash를 화면 전체에 적용. 같은 화면 타입 노드끼리만 비교해서, "팝업과 그 배경"처럼 이미지는 비슷하지만 다른 화면을 타입이 걸러 줌 |
| 지문 거부권 | `fingerprint_conflicts` | 버튼 기준으로는 같아 보여도 지문이 멀면 다른 화면으로 봄(실측: 상점 4곳이 버튼 'close' 하나로 합쳐졌지만 지문 거리 0.46). 지문을 합치는 데뿐 아니라 나누는 데도 씀 |
| 고정 기준(앵커) 비교 | `ScreenNode.buttons`, `ScreenNode.fingerprint` | 노드가 처음 관찰될 때 저장한 버튼 목록·지문만 비교 기준으로 씀. 조금씩 다른 관찰이 이어져도 기준이 떠내려가지 않음 |
| 공통 띠 제외 비교 | `GraphStore._content` | 네비게이션 바 같은 공통 띠(5장)를 잘라 낸 본문으로 지문·HOG를 비교해, 탭 강조·배지 숫자 차이로 노드가 갈리지 않게 함 |
| 정규화 + 콘텐츠 해시 ID | `observer.screen_id`, `screen_buttons` | `screen_type`과 정렬한 버튼 라벨(안전 역할·확신도 기준 이상만)을 SHA-1로 해시해 앞 12자리를 ID로 씀. `visible_text`는 관찰마다 달라 뺐고, 이 ID는 "정확히 같은 관찰"을 빠르게 찾는 용도 |
| 충돌 회피 ID | `graph.unique_node_id` | 자연 ID를 이미 다른 화면이 쓰고 있으면 지문 앞 6자리(+일련번호)를 붙여 새 ID를 만듦 |
| 라벨 정규화 (정규식 + 별칭 표) | `policy.canonical_action_label` | 표기가 다른 같은 뜻을 하나로 통일. 괄호 제거, 숫자를 `#`으로 치환, 끝에 붙은 UI 명사(tab, button, icon, 탭, 버튼 등) 제거(`_strip_ui_suffix_tokens`), "gear / cog / 설정"을 모두 `settings`로 바꾸는 별칭 표. 별칭은 라벨의 모든 낱말이 별칭이거나 꾸밈 낱말(`_ALIAS_DECORATIONS`: 아이콘·화살표·x·가기 등)일 때만 적용("친구 더 추가"는 `friends`가 아님). 붙여 쓴 낱말은 별칭 + 꼬리(`가기`, `창`, `으로` 등)만 인정 |
| 허용 오차 매칭 (집합 연산) | `graph.find_tolerant_match`, `buttons_within_tolerance` | 두 버튼 집합에 공통 원소가 있고 대칭차집합(한쪽에만 있는 원소) 크기가 `SCREEN_MATCH_MAX_BUTTON_DIFF` 이하면 같은 화면으로 봄. 후보가 여럿이면 튜플 `(차이, -방문 수, 생성 순서)`를 사전식으로 비교해 선택 |
| 간선 중복 제거 | `GraphStore._normalize_graph` | `(출발, 도착, action_key)` 튜플 집합으로 중복 간선을 제거. 예전의 타입별 노드 합치기(싱글턴 화면 타입)는 상점처럼 실제로 다른 화면을 과병합해서 없앰 |
| 파편화 진단 | `graph.fragmented_screen_types` | 같은 타입 노드가 `SCREEN_FRAGMENTATION_WARN_COUNT`(3)개 이상이면 실행 로그·보고서에 경고만 함(자동 병합은 안 함) |

## 3. 클릭 정확도 — `tap_refine.py`, `tap_effect.py`, `survey.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 픽셀 bbox로 좌표 받기 | `survey.apply_bbox_centers`, `schemas.PixelBox` | 0~1 좌표로 받으면 모델이 어림값을 줌(실측 좌표 78개 중 45개가 x=0.5). 버튼 사각형을 픽셀로 받아 중심을 다시 계산하고, 이미지 밖은 안으로 잘라 넣고(clamp) 뒤집히거나 넓이 0인 bbox는 버림 |
| 잘라 확대 + 체스판 격자 | `tap_refine.crop_box`, `build_grid_view` | 시각 프롬프팅의 한 종류. 예상 위치를 가운데로 한 정사각형(긴 변의 40%)을 이미지 안으로 밀어 넣어 자르고, 긴 변 1024px까지(최대 3배) 확대한 뒤 반투명 격자와 가장자리 열 글자(A, B, …)·행 숫자를 그림. 글자를 가장자리에만 붙여 칸 안쪽 버튼을 가리지 않음 |
| 26진법 열 이름 | `column_name`, `parse_cell` | 0 → A, 25 → Z, 26 → AA(엑셀 열 이름). "C5" ↔ (열 2, 행 4)로 바꾸고, 형식이 틀리거나 격자 밖이면 None |
| 좌표 역변환 | `GridView.to_source` | 확대 이미지 좌표를 원본 좌표로: `x0 + x / scale` (배율을 되돌리고 평행이동) |
| 두 신호 교차 검증 | `resolve_location` | 모델에게 칸 이름과 픽셀을 둘 다 받음. 픽셀이 칸에서 한 칸 이내면 픽셀(더 정밀), 어긋나면 칸 중심(덜 틀림), 둘 다 못 쓰면 None |
| 2단계 폴백 (확대 → 전체) | `refine_tap_target` | 확대본에서 못 찾거나 확신도가 낮으면 전체 화면에서 한 번 더 물음(`TAP_REFINE_FULL_FALLBACK`, 실측에서 엉뚱한 좌표가 나와 기본 끔). 못 찾으면 누르지 않음(`not_found`). API 오류면 탐색을 멈추지 않고 처음 좌표로 누름 |
| 화면 안정 대기 (폴링 + 디바운싱) | `tap_effect.wait_for_settle` | 탭 뒤 최소 대기 후 일정 간격으로 캡처해, 직전 프레임과 차이가 기준 미만인 상태가 `POST_TAP_STABLE_FRAMES`번 이어지면 멈췄다고 봄. 제한 시간이 지나면 그 시점 화면으로 판정. 탭 전 화면 대비 최대 변화(`peak_change`)와 최종 변화(`final_change`)를 같이 기록 |
| 탭 효과 판정 (임계값) | `TapEffect.changed`, `same_screen` | 실측: 같은 화면 캡처끼리 0.0000~0.0038, 실제 전환은 0.054 이상 → 0.01 미만이면 "화면 그대로" = 빗나감 의심 |

## 4. 탈출 사다리·플레이 회피 — `navigation/navigator.py`, `action_executor.py`, `tap_refine.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 탈출 사다리 (단계 순서 상태 기계) | `navigator._plan_escape`, `ESCAPE_STAGES` | 정해진 단계를 순서대로 시도: Android 뒤로 → 나가기 요소 찾기 → 닫기류 버튼 다시 누르기 → 다시 분석(`resurvey`, 화면당 1번) → 다른 버튼 눌러보기(`other_buttons`, 화면당 `STUCK_OTHER_BUTTONS_MAX`개) → 게임 재시작. 원장에서 이 화면의 `not_found`·`no_change` 행동과 이번 세션에 못 찾은 행동(`_Journey.dead_keys`)은 3·5단계와 리셋에서 건너뜀. 상태는 정수 두 개(`escape_stage` 지금 단계, `escape_tries` 이 단계 시도 수). 쓸 수 없거나 상한을 넘은 단계는 건너뛰고, 화면을 바꾼 단계는 시도 수만 0으로 돌려 계속 씀. 정상 탭 없이 탈출이 `ESCAPE_MAX_STREAK`(8)번 이어지면 재시작 단계로 바로 감 |
| 단계 순서 교체 | `PLAY_FLOW_POPUP_STAGES` | 같은 사다리를 순서 튜플만 바꿔 재사용. 플레이 중 확인창에서는 뒤로를 먼저 보내면 창이 닫혀 보드로 돌아가므로 나가기 요소 찾기를 먼저 씀 |
| 나가기 요소 찾기 | `tap_refine.find_exit_target` | 화면 전체에 격자(3장)를 그려 X/닫기/뒤로/홈 탭을, 없으면 팝업 바깥 어두운 곳 한 점을 물음. 효과 없던 좌표는 프롬프트에 넣어 다시 고르지 않게 함 |
| 위험 낱말 차단 목록 | `tap_refine.is_risky_exit_label` | 구매·광고·로그인·상점 같은 낱말이 들어간 라벨은 나가기 요소로 고르지 않음 |
| 순환 감지 (슬라이딩 윈도 + 집합 크기) | `_Journey.idle_nodes`, `_is_cycling`, `stuck_cycle` | 마지막 진전(새 노드 또는 프론티어 탭) 이후 관찰한 노드 목록을 쌓고, 최근 `STUCK_CYCLE_STEPS`개의 서로 다른 노드가 2개 이하면 순환. 순환 노드 집합을 `stuck_cycle`로 기억해 그 밖으로 나가거나 진전이 있을 때까지 재생·리셋 대신 사다리 4단계부터 씀. 5단계는 원장에서 순환 노드로 가는 행동(`_keys_leading_to`)을 뺌 |
| 탭바 전용 찾기 (잘라 보기 + 격자) | `tap_refine.find_nav_tabs`, `navigator._add_nav_tabs` | 새 화면 첫 분석 때 화면 아래 16% 띠만 잘라 격자를 그려 고해상도로 탭을 왼쪽부터 모두 묻고(칸 + 픽셀, `resolve_location`으로 원본 좌표 복원), 조사 모델의 `bottom_nav` 후보를 그 결과로 교체. 선택된 탭·플레이 진입 탭 제외, 팝업에 가려졌으면(`covered`) 그대로 둠 |
| 막힘 재분석 (병합) | `run_resurvey`, `_stuck_hint`, `_merge_candidates` | 눌러 본·화면에 없는 버튼 목록과 "탭바가 또렷하면 팝업 아님" 지시를 프롬프트에 붙여 다시 분석하고, 새 행동 키만 앞에 붙여(후보 상한 10개에 안 잘리게) `save_decision(overwrite=True)`. 다음 스텝에 원장이 새 키를 프론티어로 등록 |
| 플레이 흐름 (상태 플래그) | `_Journey.play_flow`, `_play_flow_finished` | 플레이 화면(`policy=leave`)에 들어가면 켜지고, 루트·홈 타입 화면·흐름 전부터 알던 화면(집합)에 오면 꺼짐. 켜진 동안은 프론티어 대신 나가기 버튼 → 탈출 사다리만 실행. 흐름 중 처음 본 화면은 `play_flow` 표시로 프론티어와 경로 계획에서 뺌. `PLAY_EXIT_MAX_STEPS`를 넘기면 재시작, 그것도 못 하면 `play_stuck`으로 멈춤 |
| 플레이 진입 차단 | `_split_play_entries`, `ledger.close_play_entry` | 안전 후보를 (탐색할 것, 플레이 진입)으로 나눠, 진입 버튼은 원장에 `play_entry`로 닫고 누르지 않음 |
| adb 명령 | `action_executor.execute_back_key`, `restart_game` | 좌표 없이 에뮬레이터에 직접 명령을 보냄: 뒤로 `input keyevent 4`, 재시작 `am force-stop` → `monkey -p <패키지> 1`. `subprocess`에 제한 시간을 걸고 반환 코드·출력으로 실패를 판정 |
| 사전 점검 (fail-fast) | `check_adb`, `check_game_package` | 탐색 시작 전 `adb get-state`와 `pm list packages`로 연결·설치를 확인. adb가 안 되면 API를 쓰기 전에 멈추고, 패키지만 없으면 재시작 단계만 끔 |
| 3단 폴백 선택 | `back_method` | `ADB_PATH`가 있으면 adb, 없고 `BACK_KEY`가 있으면 키보드, 둘 다 없으면 끔 |

## 5. 공통 UI (네비게이션 바) — `ui_chrome.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 행 프로파일 비교 | `_profile`, `_similar_rows` | 대표 이미지를 폭 96px 흑백으로 줄이고 한 줄씩 비교. 줄 안에서 밝기 차가 0.1 미만인 칸이 절반 이상이면 같은 줄. 두 이미지 모두 그 줄의 표준편차가 0.05 이상일 때만 비교해 무늬 없는 어두운 팝업 배경을 뺌 |
| 런 길이 + 틈 허용 | `_run_length` | 위/아래 가장자리부터 같은 줄이 몇 줄 이어지는지 셈. 사이에 다른 줄이 2줄까지는 이어진다고 봄(선택된 탭 강조 등) |
| k번째 큰 값으로 "k개 화면이 공유" 판정 | `detect_from_profiles` | 화면 i마다 다른 화면들과의 런 길이를 내림차순 정렬해 (min_screens−1)번째 값을 "i를 포함해 min_screens개가 공유하는 띠"로 보고, 모든 i 중 최댓값을 띠 높이로 씀. 전체 차이가 0.02 미만인 쌍(같은 화면이 쪼개진 것)은 근거에서 빼고, 높이의 3%~25%로 제한 |
| 중앙값 기준 위치 군집 | `UiChromeStore.is_global` | 같은 action_key 버튼 좌표들의 중앙값(이상치에 강함)에서 ±30px 안에 든 화면 수로 판정. 공통 버튼 조건: ① 서로 다른 3개 화면에서 같은 자리, ② 공통 띠 안에서 2개 화면, ③ LLM `region`이 2개 화면에서 `bottom_nav`/`top_bar`이고 실제로 화면 맨 아래(75% 아래)·맨 위(20% 위). 닫기·뒤로·확인류는 화면마다 하는 일이 달라 제외 |
| 공통 항목으로 승격 | `ledger.promote_to_global` | 공통 버튼으로 판정되면 화면별 미시도 항목을 닫고(`global`) `__global__` 항목 하나로 합침. 판정 전 어느 화면에서 이미 눌러 본 결과가 있으면 가장 최근 것을 옮겨 적어 다시 누르지 않음 |
| 지름길 간선 (가상 간선) | `teleport_edges` | 공통 버튼을 눌러 간 곳을 알면, 그 버튼이 보이는 모든 화면 → 그곳으로 가는 간선을 만들어 BFS에만 씀(그래프 파일에는 저장 안 함) |
| 재계산 생략 + 캐시 | `update_bands`, `_profile_cache` | 노드 ID 집합이 지난번과 같으면 다시 계산하지 않고, 줄인 이미지는 노드별로 캐시(대표 이미지는 처음 저장한 뒤 바뀌지 않음) |

## 6. 게임 지식 파일 — `game_knowledge.py`, `knowledge/`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 계층 병합 (기본 → 장르 → 게임) | `_merge_layers`, `load_game_profile` | `_default` → 장르 → 게임 순서로 겹쳐 씀. 목록(`layout_hints`, `*_labels`, `home_screen_types`)은 순서를 지키며 이어 붙이고 중복 제거, `nav_bar`·`play_exit_hint`는 뒤의 값으로 덮어씀. `games.txt`에 없는 제목이면 `_default`만 씀 |
| 마지막 구분자로 나누기 | `load_games` | `"게임제목, 장르"`를 `rsplit(",", 1)`로 나눠 제목에 쉼표가 있어도 됨 |
| 낱말 단위 연속 일치 (슬라이딩 윈도우) | `matches_any_label` | 라벨과 패턴을 낱말로 나눠, 패턴 길이만큼의 창을 한 칸씩 밀며 같은지 비교. '플레이'는 '지금 플레이'와 맞고 '플레이어'와는 안 맞음. 여러 낱말 패턴('레벨 종료')도 그대로 처리 |
| 동적 스키마 | `schemas.SurveyScreenType` | `Literal[screen_type_ids()]`로 JSON 파일의 타입 id 목록을 응답 스키마의 허용 값으로 씀. 분류 기준을 바꿀 때 코드 대신 파일을 고침 |
| 화면 처리 방식 표 | `screen_policy` | 타입마다 `explore`(버튼 탐색) / `leave`(기록만 하고 나옴, 플레이) / `wait`(기다렸다 다시 캡처, 로딩). 모르는 타입은 `explore` |
| 지식 → 프롬프트 블록 | `survey_prompt_block`, `GameProfile.button_limit` | 게임·장르, UI 힌트, 화면 타입과 고르는 원칙(`rules`), 탭바 규칙을 프롬프트 문자열로 만듦. 탭바 탭 수만큼 버튼 후보 상한을 6개에서 최대 10개로 늘림(선택된 탭 1개는 뺌) |
| 형식 검증 + 위치 알림 | `load_screen_types`, `load_games`, `KnowledgeError` | 정규식 `^[a-z][a-z0-9_]*$`로 id를 검사하고 중복 id·잘못된 policy·`unknown` 누락을 막음. JSON 오류는 몇째 줄 몇째 글자인지 담아 시작 전에 멈춤 |
| 메모이제이션 + 지연 로딩 | `@lru_cache`, `active_profile` | 파일은 처음 한 번만 읽고 재사용. `config`를 맨 위가 아니라 함수 안에서 불러 순환 import를 피함 |

## 7. 보드 격자 인식 — `grid_detector.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| Sobel 엣지 + 1차원 투영 | `_estimate_axis` | Sobel은 밝기 변화(경계)를 찾는 필터. 경계 세기를 한 축으로 평균 내서 "경계가 몰린 위치" 신호를 만듦 |
| 추세 제거 + 표준화 | `_estimate_axis` | 가우시안 블러 결과를 빼서 배경의 느린 밝기 변화를 없애고, 평균 0·표준편차 1로 맞춤 |
| 자기상관 | `_estimate_axis` → `detect_grid_shape` | 신호를 일정 간격만큼 밀어 원래 신호와 곱한 평균. 이 값이 가장 큰 간격이 곧 칸 간격. 4~12칸 후보를 모두 비교하고 1·2위 점수 차로 신뢰도를 계산 (조사 모드 기록에 사용) |
| 국소 표준편차 (박스 필터) | `detect_board_geometry` | 픽셀 주변의 밝기 변동량. `blur(x²) − blur(x)²`로 한 번에 계산. 사탕이 있는 곳은 무늬가 많아 값이 큼 |
| 이진화 + 모폴로지 닫힘·열림 | `detect_board_geometry` | 모폴로지는 흑백 영역의 모양을 다듬는 연산. 닫힘으로 사탕 사이 틈을 메우고 열림으로 작은 잡티를 지움 |
| 연결 요소 레이블링 (8-연결) | `detect_board_geometry` (`cv2.connectedComponentsWithStats`) | 붙어 있는 픽셀을 덩어리별로 번호 매기는 알고리즘. 가장 넓은 덩어리를 보드로 보고, 한 칸 크기(`CELL_SIZE_PX`)로 나눠 행·열 수를 계산 (플레이 모드에 사용) |
| 폴백 패턴 | 두 함수 공통 | 칸 수가 범위 밖이거나 정사각형이 아니거나 예외가 나면 설정값(`ROWS`, `COLS`)과 실패 이유를 반환. 플레이 모드는 폴백이면 swap을 막음 |

## 8. 화면 캡처·안정화 — `capture.py`, `run_loop.py`, `config.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 프레임 차이 (평균 절대 차) | `capture.frame_difference` | 두 화면을 흑백 160×160으로 줄인 뒤 픽셀 차이의 평균을 0~1로 계산. "화면이 바뀌었나" 판단의 기본 신호(탭 효과 판정, 3장에도 사용) |
| 연속 안정 카운터 (디바운싱) | `AutoCaptureState.observe` | 짧은 흔들림을 무시하는 기법. 차이가 `STABLE_THRESHOLD` 미만인 상태가 `STABLE_FRAME_COUNT`번 이어져야 분석해서 애니메이션 중 호출을 막음 |
| 중복 억제·쿨다운·호출 예산 (스로틀링) | `AutoCaptureState.api_skip_reason` | 호출 빈도를 제한하는 기법. 직전에 보낸 보드와 비슷하면 건너뛰고, 최소 간격(`API_COOLDOWN`)과 최대 호출 수(`MAX_API_CALLS`)로 비용을 제한 |
| 좌표계 변환 | `capture.make_absolute_region` | 모니터 기준 상대 좌표에 모니터 원점을 더해 전체 화면 절대 좌표로 바꿈 |
| DPI 인식 | `config._enable_windows_dpi_awareness` | Windows 화면 배율(예: 125%)이 있어도 MSS 캡처와 PyAutoGUI 클릭이 같은 물리 픽셀 좌표를 쓰게 프로세스를 DPI 인식으로 설정 |

## 9. LLM 연동 — `llm_request.py`, `image_utils.py`, `schemas.py`, `agent.py`, `survey.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 시각 프롬프팅 (격자 오버레이) | `image_utils.add_grid_overlay`, `tap_refine.build_grid_view` | 이미지 위에 표시를 그려 모델의 답을 유도하는 기법. 플레이는 보드에 빨간 격자와 행·열 번호를 그려 (행, 열)로 답하게 하고, 탭 재확인은 확대 화면에 체스판 격자를 그려 칸 이름으로 답하게 함(3장) |
| Base64 data URL | `image_utils.image_to_data_url` | 이미지를 문자열로 바꾸는 인코딩. 디스크에 저장하지 않고 PNG를 바로 API에 보냄 |
| 구조화 출력 | `llm_request.request_structured_vision` | 모델 응답을 정해진 JSON 스키마로 받는 기능. `client.responses.parse(text_format=Pydantic 모델)`을 호출하면 파싱된 객체가 반환됨. 요청마다 이미지 detail을 바꿀 수 있어 탭 재확인·나가기 찾기는 항상 `high` |
| 모델 이원화 | `config.SURVEY_MODEL`, `TAP_MODEL` | 화면 전체 분류는 싼 모델(기본 gpt-4o-mini), 누를 버튼 하나의 위치 확인만 위치 인식이 나은 모델(기본 gpt-4.1-mini). 비용이 큰 모델을 꼭 필요한 곳에만 씀 |
| Pydantic 스키마 검증 | `schemas.py` | 데이터 형식을 선언하고 자동 검증하는 라이브러리. `Literal`로 허용 값을 나열하고(화면 타입은 파일에서 읽음, 6장), `Field(ge=, le=)`로 범위를 제한하며, `@field_validator(mode="after")`로 너무 긴 값은 에러 대신 잘라냄 |
| 지식·기록 주입 | `survey.analyze_survey_screen`, `agent._format_memory_context`, `survey._format_survey_context` | 조사 프롬프트에 게임 지식 블록(6장)을 넣고, 최근 실패 수나 조사 기록을 짧은 JSON으로 요약해 덧붙임 |

## 10. 행동 결정·안전 검증 — `agent.py`, `safety_guard.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 맨해튼 거리 = 1 | `validate_decision`, `_promote_best_candidate` | 가로·세로 차이의 합. `abs(Δ행) + abs(Δ열) == 1`이면 상하좌우로 붙은 칸(대각선 제외) |
| 방향 무관 정규화 튜플 | `validate_decision`, `storage.load_failed_moves_for_board` | `(*min(a, b), *max(a, b))`로 A↔B와 B↔A를 같은 수로 취급. 금지 수는 `set`에 담아 O(1)로 조회 |
| 탐욕 선택 (greedy) | `_promote_best_candidate` | 매 순간 가장 좋아 보이는 것을 고르는 방식. LLM이 wait를 골랐어도 후보를 확신도 내림차순으로 정렬해 조건을 통과한 첫 swap을 채택 |
| 가드 절 (조기 반환) | `validate_decision` | 조건을 하나씩 검사하다 실패하면 바로 반환. UI 상태 → 보드 안정 → 좌표 범위 → 인접성 → 실패 이력 → 확신도 순 |
| 협조적 취소 | `safety_guard.make_cancel_check`, `action_executor` | 작업이 스스로 중지 요청을 확인하고 멈추는 방식. 실행 직전 여러 지점에서 콜백으로 확인하고, 요청이 있으면 예외를 냄 |

## 11. 실행 — `coordinate_mapper.py`, `action_executor.py`, `hotkeys.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 셀 → 픽셀 선형 변환 | `coordinate_mapper.cell_center` | `left + (col − 0.5) × 칸 너비`로 칸 중심의 화면 좌표를 계산 |
| 정규화 좌표 역변환 | `execute_button_tap` | 0~1 좌표에 캡처 영역 크기를 곱하고 원점을 더해 실제 좌표로 바꿈 |
| 타임아웃 폴링 | `_wait_until_active` | 조건이 맞을 때까지 짧게 반복 확인하되 제한 시간(`time.monotonic()` 기준)을 둠 |
| 다단계 폴백 | `activate_application` | 앞 방법이 실패하면 다음 방법을 시도. Windows는 `pygetwindow`로 최소화 안 된 창을 골라 activate, 거부되면 최소화 → 복원으로 우회. macOS는 AppKit → `open -b/-a` → `osascript` 순서 |
| 경계 검사 + FAILSAFE | `execute_decision`, `execute_button_tap` | 계산한 좌표가 영역 밖이면 차단하고, `pyautogui.FAILSAFE`로 마우스를 화면 모서리로 옮기면 비상 정지 |
| 합성 키 표식 | `hotkeys.SYNTHETIC_KEY_ACTIVE` | 프로그램이 직접 누른 키(키보드 뒤로)를 단축키 훅이 ESC 종료로 오해하지 않게, 누르는 동안 `threading.Event`를 켜 둠 |

## 12. 결과 관찰·보상 — `play_session.py`, `reward.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 관찰 루프 (최댓값 추적) | `observe_action_result` | 드래그 후 일정 간격으로 캡처하며 `peak_change`(가장 크게 바뀐 정도), `final_change`(최종 변화), `settled`(안정 여부)를 기록 |
| 임계값 규칙 분류 | `reward.classify_action_outcome` | 기준값과 비교하는 if 규칙으로 분류. 최종 변화가 크면 accepted, 바뀌었다 돌아오면 rejected, 거의 안 바뀌면 no_change, 그 외는 unclear |
| 삼진 보상 (−1 / 0 / +1) | `reward.evaluate_reward` | 확실한 성공과 실패만 ±1로 두고, 환경 오류나 불확실한 경우는 0으로 두어 잘못 학습하지 않게 함 |

## 13. 같은 보드 기억 — `image_utils.py`, `storage.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| dHash (차이 해시) | `image_utils.board_fingerprint` | 비슷한 이미지면 비슷한 값이 나오는 지각 해시의 한 종류. 흑백 17×16으로 줄이고 옆 픽셀보다 밝은지 비교해 256비트를 만든 뒤 16진수 문자열로 저장. 작은 반짝임에는 거의 영향을 받지 않음. 화면 지문(2장)에도 같은 함수를 씀 |
| 해밍 거리 (XOR + 비트 세기) | `image_utils.fingerprint_distance` | 서로 다른 비트 수. 전체 비트 수로 나눠 0~1로 정규화. `int.bit_count`가 없으면 `bin().count("1")`로 대체 |
| 근사 매칭 선형 탐색 | `storage.load_failed_moves_for_board` | 최근 기록을 최신순으로 훑어 거리가 `BOARD_FINGERPRINT_MAX_DISTANCE` 이하인 보드의 실패 수(rejected, no_change)만 모아 금지 목록을 만듦 |

## 14. 조사 기록·중복 제거 — `survey_utils.py`, `storage.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 텍스트 정규화 | `survey_utils.normalize_text` | `casefold`와 정규식으로 대소문자, 기호, 공백 차이를 없앰 |
| 이름 정규화 (불용어 + 별칭 사전) | `canonical_name_tokens`, `canonical_name` | "부스터 아이콘", "부스터 슬롯", "Booster"가 한 요소로 모이게, 영어를 한국어 대표 표기로 바꾸고(`NAME_ALIASES`) 생김새·위치만 말하는 낱말(아이콘, 버튼, 상단 등, `GENERIC_NAME_TOKENS`)을 뗌. 다 떼면 빈 이름이 되는 경우만 원래 낱말을 남김 |
| 콘텐츠 해시 키 | `stable_hash`, `element_key`, `button_key` | 정규화한 문자열 조각을 구분자로 이어 SHA-1로 해시. 같은 요소는 항상 같은 키 |
| 화면 시그니처 | `survey_utils.screen_signature` | 화면 타입, 정렬한 텍스트, 요소·버튼 키를 합쳐 해시한 화면 대표값 |
| 집합으로 신규·중복 분리 | `storage._known_survey_keys`, `save_survey_record` | 최근 기록의 키를 `set`에 모아 두고, 이번 키가 들어 있는지로 새 항목과 중복 항목을 나눔 |
| 허용 목록 필터 + 다중 키 정렬 | `survey_utils.safe_button_candidates` | 안전한 역할의 버튼만 남기고 `(역할 우선순위, −확신도)` 순으로 정렬. 결제·광고·로그인 버튼은 기록만 하고 누르지 않음. 지금 화면으로 가는 버튼(상점에서 상점, 홈에서 홈)과 소리 버튼도 뺌 |

## 15. 저장 — `storage.py`, `config.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| JSONL 추가 전용 로그 | `storage._append_jsonl` | 한 줄에 JSON 하나씩 쓰는 형식. 덮어쓰지 않고 뒤에 붙이기만 해서 중간에 끊겨도 앞 기록은 안전 |
| 파일 끝부터 역방향 읽기 | `storage._read_tail_lines` | 파일 끝에서 64KB씩 거꾸로 읽어 마지막 N줄만 가져옴. 파일이 커져도 필요한 만큼만 읽음 |
| 손상된 줄 건너뛰기 | `storage._parse_jsonl_records` | `JSONDecodeError`가 나면 경고만 출력하고 다음 줄로 넘어감 |
| run 폴더 분리 | `config.RUN_DIR` | 조사 기록·그래프·원장·대표 이미지·`ui_chrome.json`·지표를 `output/<GAME_SLUG>/runs/<run_id>/` 아래에 모아 게임이나 탐색이 바뀌어도 섞이지 않음 |
| 포인터 파일로 이어서 실행 | `config._resolve_run_id` | 우선순위: `RUN_ID` 환경변수 → `NEW_RUN`(`--new-run`)이면 새 run_id를 만들어 포인터를 갱신 → `current_run.txt`에 적힌 run 재사용. 여러 번 실행해도 같은 그래프와 원장에 이어서 기록 |

## 16. 보고서 생성 — `survey_report.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 재귀 전위 순회 (DFS) | `_append_screen_hierarchy._walk` | 부모를 먼저 출력하고 자식으로 내려가는 순회. BFS 스패닝 트리를 깊이만큼 들여쓴 목록으로 출력 |
| Mermaid 다이어그램 생성 | `_append_game_structure_diagram` | 텍스트로 그리는 다이어그램 문법. 트리 간선은 실선 `-->`, 교차 링크는 점선 `-.->`. 간선 라벨은 항상 따옴표로 감싸고 `"`, `\|`, 백틱을 바꿈(괄호 라벨 "뒤로(키)"가 다이어그램 전체를 깨뜨렸던 문제). 같은 화면으로 돌아오는 간선(self-loop)은 그리지 않고 개수만 셈. 노드가 `MERMAID_MAX_NODES`를 넘으면 생략 |
| 도달 불가 화면 표시 | `_append_orphan_screens` | `unreached_from` 결과를 대표 이미지와 함께 따로 적어, 트리에서 빠진 화면도 관찰 여부를 확인할 수 있게 함 |
| 이름 기준 묶기 + 최빈값 대표 | `generate_survey_markdown`, `_group_by_category`, `_representative_name` | 요소를 정규화 이름 튜플(14장)로 묶고 `Counter`로 표기·분류 횟수를 셈. 대표 이름 = 많이 나온 것 → 짧은 것 → 사전순, 대표 분류 = 가장 많이 붙은 분류. 다른 분류는 옆에 적어 분류가 흔들렸다는 사실을 남김 |
| 동시출현 집계 | `generate_survey_markdown` | 어떤 요소가 어느 화면들에 함께 나왔는지 세는 것. 요소마다 등장 화면을 `set`으로 모두 모음 |
| 연속 구간 압축 (run-length) | `_append_evidence_index` | 같은 화면 타입이 연달아 나오면 "Step 3–8 (6건)" 한 줄로 묶고, 세션이 바뀌면 끊음 |
| 그룹핑 + 커버리지 | `_append_frontier_section` | `defaultdict(list)`로 화면별 미시도 / blocked / not_visible / not_found / play_entry 항목을 나누고 탐색 비율을 표시 |
| 공통 UI 섹션 | `_append_persistent_ui_section` | 공통 띠 높이와 공통 버튼별로 몇 화면에서 보였고 눌러서 어디로 갔는지 표시 |
| 상대 경로 링크 | `_relative_link` | `os.path.relpath`로 보고서 폴더 밖 캡처도 `..` 경로로 연결. 윈도우 역슬래시를 슬래시로 바꿔 다른 OS에서도 열림 |

## 17. 탐색 지표 — `survey_metrics.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| 세션 분할 (그룹화) | `_split_sessions` | 조사 기록을 `session_id`로 묶고 첫 기록 시각순으로 정렬 |
| 3단계 폴백 판정 | `compute_session_metrics` | 탭이 화면을 바꿨는지: 기록의 `tap_effect` → 없으면(옛 run) 다음 캡처와 픽셀 차이를 직접 계산 → 이미지도 없으면 다음 기록의 `screen_id` 변화로 추정. 어느 근거를 썼는지도 남김 |
| 누적 집합으로 신규·누적 | `seen_screens`, `seen_transitions` | 세션마다 집합 크기 증가분 = 새 화면·새 전환, 집합 크기 = 누적. 누적이 세션을 거듭해도 평평하면 수렴 |
| 적중률·중앙값 | `hit_rate`, `refine_median_shift_px` | 적중률 = 화면이 바뀐 탭 / 판정된 탭(게임 재시작 제외). 확대 재확인 보정 거리는 중앙값(이상치에 강함) |
| BFS 도달성 요약 | `graph_summary` | `graph.json`·`ledger.json`만으로 루트에서 도달 못 하는 노드 수, self-loop 수, 커버리지, verdict 분포를 계산 |
| 독립 실행 | 모듈 전체 | `config`를 import하지 않아 새 run 포인터를 만들지 않음. 그래서 `frame_difference`를 복사해 둠 |

## 18. 실행 제어·설정 — `hotkeys.py`, `run_loop.py`, `main.py`, `cli.py`, `config.py`

| 기법 | 위치 | 설명 |
| --- | --- | --- |
| `threading.Event` 플래그 | `hotkeys.py` | 스레드 사이에 켜짐·꺼짐 상태를 안전하게 공유하는 객체. 키보드 콜백과 메인 루프가 시작·중지·종료 상태를 공유 |
| 이벤트 폴링 메인 루프 | `run_loop.run` | `while not EXIT_REQUESTED` 안에서 단발 분석과 자동 분석 요청을 확인하며 반복 |
| 클로저 콜백 주입 | `safety_guard.make_cancel_check` | 필요한 값을 묶어 둔 함수(`lambda`)를 넘겨, 하위 함수가 전역 상태를 몰라도 취소 여부를 확인할 수 있게 함 |
| 세션 반복 + 조기 종료 | `main.run_autodrive_sessions` | `--autodrive-repeat N`번 세션을 같은 run에 누적하고 세션마다 지표를 갱신. `converged`·`cancelled`·`error`·`blocked`·`play_stuck`이면 남은 세션을 건너뛰고, `unreachable`이면 다음 세션이 루트 정렬부터 다시 시작 |
| 옵션 조합 검증 | `cli.build_run_plan` | `argparse`로 읽은 옵션 중 함께 쓸 수 없는 조합(자동 탐색 + 다른 모드, `--autodrive-repeat` 단독 등)을 막고 불변 `RunPlan`으로 확정 |
| 환경변수 설정 + 즉시 검증 | `config.py` | `.env` 값을 `os.getenv`로 읽음. `_env_bool`, `_env_float(범위)`, `_env_int(하한)`은 잘못된 값이면 import 시점에 바로 에러를 냄(fail-fast) |

## 19. 자주 쓰인 파이썬 문법

| 문법 | 예시 위치 | 설명 |
| --- | --- | --- |
| `@dataclass(frozen=True)` | `GridShape`, `TapEffect`, `GameProfile`, `ScreenType` | 값만 담는 불변 객체를 짧게 정의 |
| `field(default_factory=...)` | `FrontierWalker`, `_Journey` | 리스트·사전 같은 가변 기본값을 인스턴스마다 새로 만듦 |
| 타입 힌트 (`Literal`, `Callable`, 유니온) | 대부분의 파일 | `from __future__ import annotations`와 함께 써서 함수 입출력 형식을 명시 |
| `Literal[튜플]` | `schemas.SurveyScreenType` | 실행 시 파일에서 읽은 값으로 `Literal` 타입을 만듦 |
| 키워드 전용 인자 `*` | `detect_grid_shape(..., *, enabled=...)` | `*` 뒤 인자는 이름을 붙여야만 넘길 수 있어 순서 실수를 막음 |
| `next((... for ...), None)` | `navigator._button_for_action_key` | 조건에 맞는 첫 항목을 찾고, 없으면 `None` 반환 |
| 집합 연산 `&`, `^`, `-` | `buttons_within_tolerance`, `run_autodrive` | 교집합, 대칭차집합, 차집합 |
| 튜플 정렬 키 | `safe_button_candidates`, `find_tolerant_match`, `_representative_name` | 여러 기준을 앞에서부터 차례로 비교. `min(..., key=lambda n: (-횟수[n], n))`은 최빈값 + 동률이면 사전순 |
| `dict.fromkeys(...)` | `game_knowledge._unique`, `canonical_name_tokens` | 첫 등장 순서를 지키며 중복 제거 |
| `@lru_cache` | `game_knowledge._screen_types_cached` | 같은 인자 호출 결과를 저장해 재사용 |
| `np.add.at` | `screen_index.hog_feature` | 같은 인덱스가 여러 번 나와도 값을 빠짐없이 더함(`h[idx] += v`는 중복 인덱스를 한 번만 더함) |
| 브로드캐스팅 `[:, None]`, `np.broadcast_to` | `hog_feature` | 크기가 다른 배열을 복사 없이 맞춰 계산 |
| `model_copy(update=...)` | `navigator` 확대 재확인 | Pydantic 객체를 일부 필드만 바꿔 복사(원본은 그대로) |
| 지연 import | `pyautogui`, `keyboard`, `AppKit`, `config`(game_knowledge) | 실제로 필요할 때만 불러와 플랫폼 의존성, macOS segfault, 순환 import를 피함 |
| `with`, `try / finally` | `mss.MSS()`, `release_hotkeys`, `SYNTHETIC_KEY_ACTIVE` | 오류가 나도 자원 정리를 보장 |
| `subprocess.run(..., timeout=)` | `action_executor._run_adb` | 외부 명령(adb)에 제한 시간을 걸어 멈춤을 막음 |

## 20. 테스트 — `tests/`

| 기법 | 설명 |
| --- | --- |
| `unittest` | 파이썬 기본 테스트 프레임워크 (`make test`로 실행, README 기준 233개) |
| `unittest.mock.patch`, `patch.object` | 설정값, API, 마우스·adb 호출을 가짜로 바꿔 네트워크나 실제 입력 없이 검증 |
| 함수 인자로 의존성 주입 | `wait_for_settle(sleep=, clock=)`, `refine_tap_target(request=)`, `restart_game(sleep=)`처럼 시계·대기·API 요청을 인자로 받아, 테스트에서 실제로 기다리거나 API를 부르지 않고 시간 흐름과 응답을 재현 |
| 고정 시드 난수 이미지 | `np.random.default_rng(시드)`로 매번 같은 가짜 화면·잡음을 만들어 HOG·공통 띠 검사를 재현 가능하게 함 |
| `types.SimpleNamespace` | 속성만 가진 간단한 가짜 객체. LLM 응답 같은 입력을 흉내 냄 |
| `tempfile.TemporaryDirectory` | 파일 저장 테스트를 임시 폴더에서 실행해 실제 기록과 섞이지 않게 함 |
