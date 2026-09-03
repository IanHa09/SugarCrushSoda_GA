"""게임 보드 좌표를 마우스로 드래그해 선택하는 보정(calibration) 도구입니다."""

from __future__ import annotations

import cv2
import mss
import numpy as np

from config import MONITOR_INDEX
from capture import print_monitors


MAX_PREVIEW_WIDTH = 1400
MAX_PREVIEW_HEIGHT = 850


def main() -> None:
    # 화면을 캡처해 보드 영역을 마우스로 선택받고 BOARD_OFFSET 값을 출력합니다.
    with mss.MSS() as sct:
        print_monitors(sct)

        if MONITOR_INDEX <= 0 or MONITOR_INDEX >= len(sct.monitors):
            raise ValueError(
                f"config.py의 MONITOR_INDEX={MONITOR_INDEX}가 올바르지 않습니다."
            )

        monitor = sct.monitors[MONITOR_INDEX]
        shot = sct.grab(monitor)

        full_image = cv2.cvtColor(
            np.asarray(shot),
            cv2.COLOR_BGRA2BGR,
        )

        height, width = full_image.shape[:2]

        # 큰 모니터도 한 창 안에서 선택할 수 있도록 미리보기만 축소합니다.
        scale = min(
            1.0,
            MAX_PREVIEW_WIDTH / width,
            MAX_PREVIEW_HEIGHT / height,
        )

        preview = cv2.resize(
            full_image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )

        print("\n[사용 방법]")
        print("1. Candy Crush Soda의 실제 퍼즐 보드만 드래그합니다.")
        print("2. Enter 또는 Space를 누릅니다.")
        print("3. 취소하려면 C를 누릅니다.")

        x, y, w, h = cv2.selectROI(
            "Select Candy Crush Soda board",
            preview,
            showCrosshair=True,
            fromCenter=False,
        )
        cv2.destroyAllWindows()

        if w == 0 or h == 0:
            print("영역 선택을 취소했습니다.")
            return

        # 축소된 미리보기 좌표를 원래 모니터 픽셀 좌표로 되돌립니다.
        selected = {
            "left": round(x / scale),
            "top": round(y / scale),
            "width": round(w / scale),
            "height": round(h / scale),
        }

        print("\nconfig.py의 BOARD_OFFSET을 아래 값으로 교체하세요.\n")
        print("BOARD_OFFSET = {")
        print(f'    "left": {selected["left"]},')
        print(f'    "top": {selected["top"]},')
        print(f'    "width": {selected["width"]},')
        print(f'    "height": {selected["height"]},')
        print("}")


if __name__ == "__main__":
    main()
