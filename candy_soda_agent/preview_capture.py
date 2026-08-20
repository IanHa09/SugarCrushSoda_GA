"""
API를 호출하지 않고 캡처 영역과 격자만 확인하는 프로그램입니다.

실행:
    python preview_capture.py
"""

from __future__ import annotations

import cv2
import mss

from capture import capture_bgr, make_absolute_region, print_monitors
from config import (
    AUTO_GRID,
    BOARD_OFFSET,
    COLS,
    GRID_MAX_COLS,
    GRID_MAX_ROWS,
    GRID_MIN_COLS,
    GRID_MIN_CONFIDENCE,
    GRID_MIN_ROWS,
    MONITOR_INDEX,
    OUTPUT_DIR,
    ROWS,
)
from grid_detector import detect_grid_shape
from image_utils import add_grid_overlay


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with mss.MSS() as sct:
        print_monitors(sct)

        region = make_absolute_region(
            sct,
            MONITOR_INDEX,
            BOARD_OFFSET,
        )
        print(f"\n실제 캡처 영역: {region}")

        raw_image = capture_bgr(sct, region)
        shape = detect_grid_shape(
            raw_image,
            ROWS,
            COLS,
            enabled=AUTO_GRID,
            min_rows=GRID_MIN_ROWS,
            max_rows=GRID_MAX_ROWS,
            min_cols=GRID_MIN_COLS,
            max_cols=GRID_MAX_COLS,
            min_confidence=GRID_MIN_CONFIDENCE,
        )
        grid_image = add_grid_overlay(raw_image, shape.rows, shape.cols)

        raw_path = OUTPUT_DIR / "preview_raw.png"
        grid_path = OUTPUT_DIR / "preview_grid.png"

        cv2.imwrite(str(raw_path), raw_image)
        cv2.imwrite(str(grid_path), grid_image)

        print(f"원본 미리보기 저장: {raw_path}")
        print(f"격자 미리보기 저장: {grid_path}")
        print(
            f"격자: rows={shape.rows}, cols={shape.cols}, "
            f"confidence={shape.confidence:.2f}, source={shape.source}"
        )
        print("창을 선택한 뒤 아무 키나 누르면 종료됩니다.")

        cv2.imshow("Raw board capture", raw_image)
        cv2.imshow("Grid sent to LLM", grid_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
