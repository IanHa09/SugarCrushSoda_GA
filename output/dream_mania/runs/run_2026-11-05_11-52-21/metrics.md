# 탐색 지표: run_2026-11-05_11-52-21

## 그래프 상태

- nodes: 8
- edges: 14
- self_loop_edges: 0
- unreached_from_root: 0
- coverage: 18/31
- screen_types: {'home': 2, 'menu': 5, 'level': 1}
- ledger verdicts: {'new_screen': 5, 'global': 4, 'no_change': 7, 'known_screen': 5, 'external': 1, 'not_found': 7, 'open': 1, 'play_entry': 1}

## 세션별

| 세션 | 스텝 | 탭 | 화면 바뀜 | 그대로 | 적중률 | 재확인 찾음 | 재확인 못찾음 | 보정 중앙값(px) | 뒤로키 | 탈출 | 재시작 | 새 화면 | 누적 화면 | 새 전환 | 누적 전환 | 종료 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 40 | 33 | 17 | 16 | 52% | 29 | 10 | 71.2 | 1 | 3 | 0 | 8 | 8 | 14 | 14 | other(tap_executed) |

- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. 판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id).
- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.
