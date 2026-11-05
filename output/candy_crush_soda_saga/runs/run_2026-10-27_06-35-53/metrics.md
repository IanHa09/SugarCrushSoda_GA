# 탐색 지표: run_2026-10-27_06-35-53

## 그래프 상태

- nodes: 7
- edges: 18
- self_loop_edges: 0
- unreached_from_root: 0
- coverage: 21/30
- screen_types: {'home': 1, 'shop': 1, 'popup': 4, 'level': 1}
- ledger verdicts: {'new_screen': 2, 'global': 5, 'known_screen': 9, 'external': 1, 'no_change': 10, 'play_entry': 3}

## 세션별

| 세션 | 스텝 | 탭 | 화면 바뀜 | 그대로 | 적중률 | 재확인 찾음 | 재확인 못찾음 | 보정 중앙값(px) | 뒤로키 | 탈출 | 재시작 | 새 화면 | 누적 화면 | 새 전환 | 누적 전환 | 종료 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 40 | 39 | 15 | 24 | 38% | 12 | 26 | 121.8 | 1 | 1 | 0 | 6 | 6 | 14 | 14 | other(tap_executed) |
| 2 | 33 | 32 | 14 | 18 | 44% | 10 | 22 | 184.4 | 0 | 0 | 0 | 1 | 7 | 4 | 18 | converged |

- 적중률 = 화면을 실제로 바꾼 탭 / 판정 가능한 탭. 판정 근거(change_source)는 CSV에 있습니다(tap_effect > pixels > screen_id).
- 누적 화면·누적 전환이 세션을 거듭해도 거의 안 늘면 그래프가 수렴한 것입니다.
