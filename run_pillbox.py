"""Smart Pillbox application bootstrap.

Startup modes
─────────────
  python run_pillbox.py                     GUI + hardware node
  python run_pillbox.py --headless          hardware node only (no display)
  python run_pillbox.py --ble               GUI + hardware + BLE GATT server
  python run_pillbox.py --ble --headless    hardware + BLE only (no display)
  python run_pillbox.py --cli-assistant     interactive Llama AI terminal
  python run_pillbox.py --ble --legacy-advertising   BLE with btmgmt fallback
"""
import argparse
import sys
import threading
import tkinter as tk

from agent_supervisor import AgentSupervisor
from nodes.hardware_node import HardwareNode
from nodes.health_analysis_node import AITreeAnalyzer
from pillbox_database import initialize_database


# ── CLI assistant ─────────────────────────────────────────────────────────────

def run_cli_assistant() -> None:
    supervisor = AgentSupervisor()
    print("==================================================")
    print("Smart Pillbox Llama AI Assistant (Memory DB Connected)")
    print("Type your message to set schedules, search drugs, or check vitals.")
    print("Type 'exit' or 'quit' to exit.")
    print("==================================================")
    while True:
        try:
            user_input = input("\nUser: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting AI Assistant.")
                break
            reply = supervisor.process_user_message(user_input)
            print(f"\nAI: {reply}")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting AI Assistant.")
            break


# ── BLE server thread ─────────────────────────────────────────────────────────

def start_ble_thread(legacy_advertising: bool = False) -> threading.Thread:
    """Launch the BLE GATT server in a daemon thread and return it."""
    def _run():
        try:
            from flutter_ble_receiver import start_ble_server
            start_ble_server(legacy_advertising=legacy_advertising)
        except ImportError as exc:
            print(
                f"[BLE] Cannot start BLE server (missing dependencies: {exc}). "
                "Install dbus-python and PyGObject on the Raspberry Pi.",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(f"[BLE] Server error: {exc}", file=sys.stderr, flush=True)

    thread = threading.Thread(target=_run, name="BLEServer", daemon=True)
    thread.start()
    print("[BLE] GATT server thread started.", flush=True)
    return thread


# ── Main entry point ──────────────────────────────────────────────────────────

def main(
    headless: bool = False,
    cli_assistant: bool = False,
    ble: bool = False,
    legacy_advertising: bool = False,
) -> None:
    initialize_database()
    analyzer = AITreeAnalyzer()
    hardware = HardwareNode(analyzer)
    hardware.start()

    # Start BLE server in background if requested
    if ble:
        start_ble_thread(legacy_advertising=legacy_advertising)

    if cli_assistant:
        run_cli_assistant()
        return

    if headless:
        print("Pillbox running in headless mode. Press Ctrl+C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return

    from main import SmartPillboxApp
    root = tk.Tk()
    SmartPillboxApp(root)
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the smart pillbox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--headless", action="store_true", help="start hardware only; do not open Tkinter")
    parser.add_argument("--cli-assistant", action="store_true", help="start interactive Llama AI Assistant CLI")
    parser.add_argument("--ble", action="store_true", help="start BLE GATT server alongside the application")
    parser.add_argument("--legacy-advertising", action="store_true", help="use btmgmt advertising for Pi 6.18 BLE regression")
    args = parser.parse_args()
    main(
        headless=args.headless,
        cli_assistant=args.cli_assistant,
        ble=args.ble,
        legacy_advertising=args.legacy_advertising,
    )
