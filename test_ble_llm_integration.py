"""Mock test for BLE ↔ LLM integration.

Tests CommandCharacteristic._handle() directly, bypassing real BlueZ/dbus,
to verify the AgentSupervisor is called and a reply is queued correctly.

Run from the pillbox directory:
    python test_ble_llm_integration.py
"""
import sys
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── Stub out dbus so the import works on Windows/non-Pi environments ──────────
dbus_mock = MagicMock()
dbus_service_mock = MagicMock()
dbus_mock.service = dbus_service_mock
dbus_mock.service.Object = object          # plain base class
dbus_mock.service.method = lambda *a, **kw: (lambda f: f)
dbus_mock.service.signal = lambda *a, **kw: (lambda f: f)
dbus_mock.ObjectPath = str
dbus_mock.Array = list
dbus_mock.exceptions = MagicMock()
dbus_mock.exceptions.DBusException = Exception

gi_mock = MagicMock()
glib_mock = MagicMock()
gi_mock.repository.GLib = glib_mock

# Capture GLib.idle_add calls so we can invoke them synchronously in tests
_idle_calls: list = []
def _fake_idle_add(fn, *args):
    _idle_calls.append((fn, args))
glib_mock.idle_add = _fake_idle_add
glib_mock.timeout_add = lambda ms, fn: _idle_calls.append((fn, ()))

sys.modules.setdefault("dbus", dbus_mock)
sys.modules.setdefault("dbus.exceptions", MagicMock())
sys.modules.setdefault("dbus.mainloop", MagicMock())
sys.modules.setdefault("dbus.mainloop.glib", MagicMock())
sys.modules.setdefault("dbus.service", dbus_service_mock)
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)
sys.modules.setdefault("gi.repository.GLib", glib_mock)

# ── Now import the module under test ─────────────────────────────────────────
import flutter_ble_receiver as ble_mod

# ── Helper – build a minimal fake CommandCharacteristic ──────────────────────

def make_command_char():
    """Return a CommandCharacteristic wired to a mock ResponseCharacteristic."""
    sent_responses: list[dict] = []

    response = MagicMock()
    def _capture_send(payload):
        sent_responses.append(payload)
    response.send_json.side_effect = _capture_send

    cmd = object.__new__(ble_mod.CommandCharacteristic)
    cmd._response = response
    cmd._buffer = bytearray()
    return cmd, sent_responses


# ── Test 1: chat message → LLM → reply ───────────────────────────────────────

def test_chat_message():
    print("\n--- Test 1: chat message dispatched to LLM ---")
    cmd, sent = make_command_char()

    fake_reply = "✅ 已成功為您設定排程！"

    # Patch _get_supervisor so no real DB / Ollama is needed
    mock_supervisor = MagicMock()
    mock_supervisor.process_user_message.return_value = fake_reply

    with patch("nodes.ble_receiver_node._supervisor", mock_supervisor):
        raw = json.dumps({"id": "t1", "type": "chat", "text": "設定 08:30 第1格吃降血壓藥", "session_id": "test"}).encode()
        cmd._handle(raw)

    # The LLM worker runs in a background thread – wait for it
    timeout = 5.0
    import time; deadline = time.time() + timeout
    while time.time() < deadline:
        # Drain idle_add queue
        while _idle_calls:
            fn, args = _idle_calls.pop(0)
            fn(*args)
        if len(sent) >= 2:
            break
        time.sleep(0.05)

    assert len(sent) >= 2, f"Expected at least 2 responses (ack + reply), got: {sent}"
    ack = sent[0]
    assert ack["type"] == "ack", f"First response should be ack, got: {ack}"
    assert ack["done"] is False, "ack should have done=False"
    reply_msg = sent[1]
    assert reply_msg["type"] == "chat", f"Second response should be chat, got: {reply_msg}"
    assert reply_msg["text"] == fake_reply
    assert reply_msg["done"] is True
    assert reply_msg["id"] == "t1"
    print(f"  ack  : {ack}")
    print(f"  reply: {reply_msg}")
    print("[PASS] chat message test passed.")


# ── Test 2: vitals shortcut (no LLM needed) ───────────────────────────────────

def test_vitals_message():
    print("\n--- Test 2: vitals shortcut ---")
    cmd, sent = make_command_char()

    mock_supervisor = MagicMock()
    mock_supervisor.dispatch.return_value = {"bpm": 72.0, "spo2": 98.0, "temp": 36.5}

    with patch("nodes.ble_receiver_node._supervisor", mock_supervisor):
        raw = json.dumps({"id": "t2", "type": "vitals"}).encode()
        cmd._handle(raw)

    assert len(sent) == 1, f"Expected 1 response for vitals, got: {sent}"
    r = sent[0]
    assert r["type"] == "vitals"
    assert r["done"] is True
    assert r["data"]["bpm"] == 72.0
    print(f"  response: {r}")
    print("[PASS] vitals shortcut test passed.")


# ── Test 3: invalid JSON → error response ─────────────────────────────────────

def test_invalid_json():
    print("\n--- Test 3: invalid JSON ---")
    cmd, sent = make_command_char()
    cmd._handle(b"not valid json at all")
    assert len(sent) == 1
    assert sent[0]["type"] == "error"
    print(f"  response: {sent[0]}")
    print("[PASS] invalid JSON test passed.")


# ── Test 4: schedules shortcut ────────────────────────────────────────────────

def test_schedules_message():
    print("\n--- Test 4: schedules shortcut ---")
    cmd, sent = make_command_char()

    mock_supervisor = MagicMock()
    mock_supervisor.dispatch.return_value = {
        "schedules": [{"box_index": 0, "time_str": "08:30", "disease_name": "降血壓藥"}]
    }

    with patch("nodes.ble_receiver_node._supervisor", mock_supervisor):
        raw = json.dumps({"id": "t4", "type": "schedules"}).encode()
        cmd._handle(raw)

    assert len(sent) == 1
    assert sent[0]["type"] == "schedules"
    assert len(sent[0]["data"]["schedules"]) == 1
    print(f"  response: {sent[0]}")
    print("[PASS] schedules shortcut test passed.")


# ── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_chat_message()
    test_vitals_message()
    test_invalid_json()
    test_schedules_message()
    print("\nALL BLE+LLM INTEGRATION TESTS PASSED.")
