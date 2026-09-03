"""API를 호출하지 않고 캡처 영역과 격자만 확인하는 프리뷰 도구입니다."""

from __future__ import annotations

import cv2
import mss

from capture import capture_bgr, make_absolute_region, print_monitors
from config import (
    BOARD_OFFSET,
    CELL_SIZE_PX,
    COLS,
    MONITOR_INDEX,
    OUTPUT_DIR,
    ROWS,
)
from grid_detector import detect_board_geometry, detect_grid_shape
from image_utils import add_grid_overlay


def main() -> None:
    # 보드를 캡처해 격자를 씌운 뒤 결과 이미지를 저장/표시합니다.
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
        
        geometry = detect_board_geometry(
            raw_image,
            cell_size=CELL_SIZE_PX,
            fallback_rows=ROWS,
            fallback_cols=COLS,
        )
        board_image = raw_image[
            geometry.top : geometry.top + geometry.height,
            geometry.left : geometry.left + geometry.width,
        ]
        grid_image = add_grid_overlay(board_image, geometry.rows, geometry.cols)
        print(
            f"보드 사각형: ({geometry.left},{geometry.top}) "
            f"{geometry.width}x{geometry.height}, "
            f"칸 {geometry.rows}x{geometry.cols}, source={geometry.source}"
        )

        raw_path = OUTPUT_DIR / "preview_raw.png"
        grid_path = OUTPUT_DIR / "preview_grid.png"

        cv2.imwrite(str(raw_path), raw_image)
        cv2.imwrite(str(grid_path), grid_image)

        print(f"원본 미리보기 저장: {raw_path}")
        print(f"격자 미리보기 저장: {grid_path}")
        print(
            f"격자: rows={geometry.rows}, cols={geometry.cols}, "
            f"reason: {geometry.reason}"
        )
        print("창을 선택한 뒤 아무 키나 누르면 종료됩니다.")

        cv2.imshow("Raw board capture", raw_image)
        cv2.imshow("Grid sent to LLM", grid_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
