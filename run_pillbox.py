"""New application bootstrap; mm.py remains a compatibility UI entry point."""
import argparse
import tkinter as tk
import threading

from nodes.hardware_node import HardwareNode
from nodes.health_analysis_node import AITreeAnalyzer
from pillbox_database import initialize_database


def main(headless: bool = False) -> None:
    initialize_database()
    analyzer = AITreeAnalyzer()
    hardware = HardwareNode(analyzer)
    hardware.start()
    if headless:
        print("Pillbox hardware node is running without a display. Press Ctrl+C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return
    # The existing screen is retained during migration; it can now consume the
    # same shared state as the independent hardware node.
    from mm import SmartPillboxApp
    root = tk.Tk()
    SmartPillboxApp(root)
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the smart pillbox.")
    parser.add_argument("--headless", action="store_true", help="start hardware only; do not open Tkinter")
    args = parser.parse_args()
    main(headless=args.headless)
