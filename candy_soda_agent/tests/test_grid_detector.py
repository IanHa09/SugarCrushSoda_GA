from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from grid_detector import detect_grid_shape


class GridDetectorTests(unittest.TestCase):
    def test_detects_repeating_grid(self) -> None:
        rows, cols, size = 7, 9, 40
        image = np.zeros((rows * size, cols * size, 3), dtype=np.uint8)
        colors = [(40, 90, 220), (220, 150, 30), (50, 190, 70)]
        for row in range(rows):
            for col in range(cols):
                top_left = (col * size + 3, row * size + 3)
                bottom_right = ((col + 1) * size - 3, (row + 1) * size - 3)
                cv2.rectangle(
                    image,
                    top_left,
                    bottom_right,
                    colors[(row + col) % len(colors)],
                    -1,
                )

        result = detect_grid_shape(image, 8, 8)

        self.assertEqual((result.rows, result.cols), (rows, cols))
        self.assertEqual(result.source, "auto")

    def test_falls_back_on_blank_image(self) -> None:
        image = np.zeros((280, 360, 3), dtype=np.uint8)
        result = detect_grid_shape(image, 7, 9)
        self.assertEqual((result.rows, result.cols), (7, 9))
        self.assertEqual(result.source, "fallback")


if __name__ == "__main__":
    unittest.main()
