"""Main entry point for Smart Pillbox application.

All runtime logic is modularized into nodes inside the `nodes/` package.
This module re-exports `SmartPillboxApp` (which wraps `UINode`) for backwards compatibility.
"""
from nodes.ui_node import SmartPillboxApp, UINode
from pillbox_config import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_DARK,
    BG_PANEL,
    TEXT_MAIN,
    TEXT_SUB,
    shared_state,
)

__all__ = [
    "SmartPillboxApp",
    "UINode",
    "BG_DARK",
    "BG_PANEL",
    "TEXT_MAIN",
    "TEXT_SUB",
    "ACCENT_BLUE",
    "ACCENT_GREEN",
    "ACCENT_RED",
    "ACCENT_YELLOW",
    "shared_state",
]

if __name__ == "__main__":
    from run_pillbox import main

    main()
