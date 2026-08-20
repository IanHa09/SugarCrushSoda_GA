from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))


class MainImportTests(unittest.TestCase):
    def test_main_imports_without_keyboard(self) -> None:
        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
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
