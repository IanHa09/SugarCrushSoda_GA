from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from action_executor import (
    active_application_name,
    activate_application,
    execute_decision,
)
from schemas import AgentDecision


class ActionExecutorTests(unittest.TestCase):
    def test_macos_uses_frontmost_application_name(self) -> None:
        application = SimpleNamespace(localizedName=lambda: "BlueStacks Air")
        workspace = SimpleNamespace(frontmostApplication=lambda: application)
        ns_workspace = SimpleNamespace(sharedWorkspace=lambda: workspace)
        appkit = SimpleNamespace(NSWorkspace=ns_workspace)

        with (
            patch("action_executor.sys.platform", "darwin"),
            patch.dict(sys.modules, {"AppKit": appkit}),
        ):
            self.assertEqual(active_application_name(), "BlueStacks Air")

    def test_live_swap_accepts_bluestacks_name(self) -> None:
        decision = AgentDecision(
            board_status="stable",
            detected_ui_state="playing",
            action="swap",
            source={"row": 1, "col": 1},
            target={"row": 1, "col": 2},
            confidence=0.9,
        )
        region = {"left": 100, "top": 200, "width": 90, "height": 70}

        with (
            patch("action_executor.active_application_name", return_value="BlueStacks Air"),
            patch("pyautogui.moveTo") as move_to,
            patch("pyautogui.dragTo") as drag_to,
        ):
            result = execute_decision(
                decision,
                region,
                dry_run=False,
                expected_window_title="BlueStacks",
            )

        self.assertTrue(result)
        move_to.assert_called_once()
        drag_to.assert_called_once()

    def test_macos_activation_falls_back_to_open_bundle(self) -> None:
        application = SimpleNamespace(
            localizedName=lambda: "BlueStacks Air",
            bundleIdentifier=lambda: "com.bluestacks.air",
            processIdentifier=lambda: 42,
            activationPolicy=lambda: 0,
            activateWithOptions_=lambda options: True,
        )
        workspace = SimpleNamespace(runningApplications=lambda: [application])
        appkit = SimpleNamespace(
            NSApplicationActivateAllWindows=1,
            NSApplicationActivateIgnoringOtherApps=2,
            NSApplicationActivationPolicyRegular=0,
            NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
        )

        with (
            patch("action_executor.sys.platform", "darwin"),
            patch.dict(sys.modules, {"AppKit": appkit}),
            patch(
                "action_executor._wait_until_active",
                side_effect=[False, True],
            ),
            patch("action_executor._run_focus_command") as run_focus,
        ):
            activate_application("BlueStacks")

        run_focus.assert_called_once_with(
            ["/usr/bin/open", "-b", "com.bluestacks.air"]
        )

    def test_live_swap_activates_bluestacks_when_code_is_frontmost(self) -> None:
        decision = AgentDecision(
            board_status="stable",
            detected_ui_state="playing",
            action="swap",
            source={"row": 1, "col": 1},
            target={"row": 1, "col": 2},
            confidence=0.9,
        )
        region = {"left": 100, "top": 200, "width": 90, "height": 70}

        with (
            patch(
                "action_executor.active_application_name",
                side_effect=["Code", "BlueStacks Air"],
            ),
            patch("action_executor.activate_application") as activate,
            patch("pyautogui.moveTo"),
            patch("pyautogui.dragTo"),
        ):
            result = execute_decision(
                decision,
                region,
                dry_run=False,
                expected_window_title="BlueStacks",
            )

        self.assertTrue(result)
        activate.assert_called_once_with("BlueStacks")

    def test_dynamic_grid_shape_is_used_for_coordinates(self) -> None:
        decision = AgentDecision(
            board_status="stable",
            detected_ui_state="playing",
            action="swap",
            source={"row": 1, "col": 9},
            target={"row": 2, "col": 9},
            confidence=0.9,
        )
        region = {"left": 0, "top": 0, "width": 900, "height": 700}

        with (
            patch("action_executor.active_application_name", return_value="BlueStacks"),
            patch("pyautogui.moveTo") as move_to,
            patch("pyautogui.dragTo") as drag_to,
        ):
            result = execute_decision(
                decision,
                region,
                dry_run=False,
                expected_window_title="BlueStacks",
                rows=7,
                cols=9,
            )

        self.assertTrue(result)
        move_to.assert_called_once_with(850, 50)
        drag_to.assert_called_once_with(
            850,
            150,
            duration=0.20,
            button="left",
        )


if __name__ == "__main__":
    unittest.main()
