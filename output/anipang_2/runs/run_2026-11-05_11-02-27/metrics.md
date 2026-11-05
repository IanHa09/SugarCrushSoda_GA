# 탐색 지표: run_2026-11-05_11-02-27

## 그래프 상태

- nodes: 26
- edges: 47
- self_loop_edges: 13
- unreached_from_root: 2
- coverage: 54/79
- screen_types: {'home': 7, 'popup': 6, 'unknown': 1, 'menu': 10, 'shop': 2}
- ledger verdicts: {'new_screen': 16, 'known_screen': 18, 'no_change': 20, 'open': 2, 'not_found': 10, 'external': 2, 'global': 4, 'blocked': 3, 'not_visible': 4}

## 세션별

| 세션 | 스텝 | 탭 | 화면 바뀜 | 그대로 | 적중률 | 재확인 찾음 | 재확인 못찾음 | 보정 중앙값(px) | 뒤로키 | 탈출 | 재시작 | 새 화면 | 누적 화면 | 새 전환 | 누적 전환 | 종료 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 40 | 33 | 20 | 13 | 61% | 23 | 11 | 207.5 | 3 | 12 | 0 | 11 | 11 | 17 | 17 | other(stuck_other_button) |
| 2 | 40 | 34 | 21 | 13 | 62% | 20 | 12 | 109.8 | 2 | 11 | 0 | 7 | 18 | 16 | 33 | other(tap_executed) |
| 3 | 40 | 35 | 28 | 7 | 80% | 24 | 12 | 190.4 | 0 | 1 | 0 | 5 | 23 | 10 | 43 | other(tap_executed) |
| 4 | 28 | 25 | 23 | 2 | 92% | 18 | 6 | 180.2 | 2 | 1 | 0 | 3 | 26 | 4 | 47 | unreachable |
| 5 | 1 | 0 | 0 | 0 | - | 0 | 0 | - | 0 | 0 | 0 | 0 | 26 | 0 | 47 | unreachable |

- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. 판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id).
- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.
