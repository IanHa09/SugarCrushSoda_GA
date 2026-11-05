# 탐색 지표: run_2026-10-27_05-44-27

## 그래프 상태

- nodes: 9
- edges: 28
- self_loop_edges: 0
- unreached_from_root: 0
- coverage: 21/44
- screen_types: {'home': 1, 'shop': 1, 'popup': 5, 'menu': 1, 'level': 1}
- ledger verdicts: {'new_screen': 2, 'global': 5, 'known_screen': 15, 'not_found': 6, 'play_entry': 3, 'not_visible': 3, 'external': 1, 'no_change': 4, 'blocked': 4, 'open': 1}

## 세션별

| 세션 | 스텝 | 탭 | 화면 바뀜 | 그대로 | 적중률 | 재확인 찾음 | 재확인 못찾음 | 보정 중앙값(px) | 뒤로키 | 탈출 | 재시작 | 새 화면 | 누적 화면 | 새 전환 | 누적 전환 | 종료 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 40 | 31 | 18 | 13 | 58% | 7 | 29 | 115.6 | 3 | 8 | 0 | 6 | 6 | 13 | 13 | other(stuck_other_button) |
| 2 | 40 | 37 | 16 | 21 | 43% | 8 | 10 | 146.6 | 16 | 19 | 0 | 2 | 8 | 9 | 22 | other(tap_executed) |
| 3 | 40 | 28 | 14 | 14 | 50% | 10 | 15 | 59.1 | 6 | 12 | 0 | 1 | 9 | 8 | 30 | other(reset_exit_finder) |

- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. 판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id).
- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.
