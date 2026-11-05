# 탐색 지표: run_2026-10-27_01-39-38

## 그래프 상태

- nodes: 11
- edges: 20
- self_loop_edges: 0
- unreached_from_root: 2
- coverage: 17/39
- screen_types: {'home': 1, 'shop': 1, 'menu': 2, 'popup': 5, 'level': 1, 'unknown': 1}
- ledger verdicts: {'new_screen': 4, 'global': 5, 'known_screen': 10, 'not_found': 8, 'no_change': 3, 'open': 8, 'play_entry': 1}

## 세션별

| 세션 | 스텝 | 탭 | 화면 바뀜 | 그대로 | 적중률 | 재확인 찾음 | 재확인 못찾음 | 보정 중앙값(px) | 뒤로키 | 탈출 | 재시작 | 새 화면 | 누적 화면 | 새 전환 | 누적 전환 | 종료 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 27 | 21 | 13 | 8 | 62% | 17 | 5 | 46.2 | 2 | 4 | 0 | 7 | 7 | 10 | 10 | other(stuck_resurvey) |
| 2 | 23 | 17 | 13 | 4 | 76% | 12 | 5 | 152 | 3 | 5 | 0 | 4 | 11 | 7 | 17 | other(stuck_resurvey) |
| 3 | 16 | 9 | 5 | 4 | 56% | 6 | 6 | 114.7 | 3 | 7 | 0 | 0 | 11 | 3 | 20 | other(escape_target_not_found) |

- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. 판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id).
- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.
