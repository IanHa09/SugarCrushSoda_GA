"""keyboard 모듈 임포트 실패 시에도 main이 정상 로드되는지 검사하는 테스트입니다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))


class MainImportTests(unittest.TestCase):
    def test_main_imports_without_keyboard(self) -> None:
        """keyboard 임포트 실패를 흉내내도 main 모듈이 정상 로드되는지 검사합니다."""
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            # keyboard 모듈 임포트만 실패하도록 흉내냅니다.
            if name == "keyboard":
                raise RuntimeError("simulated keyboard import failure")
            return real_import(name, globals, locals, fromlist, level)

        for module_name in ["main", "candy_soda_agent.main"]:
            sys.modules.pop(module_name, None)

        with patch("builtins.__import__", side_effect=fake_import):
            import main

        self.assertTrue(hasattr(main, "main"))


if __name__ == "__main__":
    unittest.main()
