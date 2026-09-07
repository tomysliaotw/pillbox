"""BLE GATT peripheral server node for MedAI mobile app communications."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any

from nodes.agent_supervisor_node import AgentSupervisorNode

DEVICE_NAME = "MedAI Raspberry Pi"
SERVICE_UUID = "8c52b70c-7697-4f3f-b8d7-530f1146bc00"
COMMAND_UUID = "8c52b70c-7697-4f3f-b8d7-530f1146bc01"
RESPONSE_UUID = "8c52b70c-7697-4f3f-b8d7-530f1146bc02"
CHUNK_SIZE = 20
NOTIFICATION_DELAY_MS = 30

BLUEZ = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

try:
    import dbus
    import dbus.exceptions
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    class InvalidArgsException(dbus.exceptions.DBusException):
        _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"

    class NotSupportedException(dbus.exceptions.DBusException):
        _dbus_error_name = "org.bluez.Error.NotSupported"

except ImportError:
    dbus = None
    GLib = None

    class InvalidArgsException(Exception):
        pass

    class NotSupportedException(Exception):
        pass


_supervisor_lock = threading.Lock()
_supervisor: Any = None


def _get_supervisor():
    global _supervisor
    if _supervisor is None:
        with _supervisor_lock:
            if _supervisor is None:
                try:
                    _supervisor = AgentSupervisorNode()
                    print("AgentSupervisorNode + Llama LLM initialised.", flush=True)
                except Exception as exc:
                    print(f"[WARN] Could not initialise AgentSupervisorNode: {exc}", file=sys.stderr, flush=True)
                    _supervisor = None
    return _supervisor


if dbus is not None:

    class Application(dbus.service.Object):
        def __init__(self, bus) -> None:
            self.path = "/com/medai/app"
            self.services: list[Service] = []
            super().__init__(bus, self.path)

        def add_service(self, service: "Service") -> None:
            self.services.append(service)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            managed = {}
            for service in self.services:
                managed[service.get_path()] = service.get_properties()
                for characteristic in service.characteristics:
                    managed[characteristic.get_path()] = characteristic.get_properties()
            return managed

    class Service(dbus.service.Object):
        def __init__(self, bus, index: int, uuid: str) -> None:
            self.path = f"/com/medai/service{index}"
            self.uuid = uuid
            self.characteristics: list[Characteristic] = []
            super().__init__(bus, self.path)

        def add_characteristic(self, characteristic: "Characteristic") -> None:
            self.characteristics.append(characteristic)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            return {
                GATT_SERVICE_IFACE: {
                    "UUID": self.uuid,
                    "Primary": True,
                    "Characteristics": dbus.Array(
                        [item.get_path() for item in self.characteristics], signature="o"
                    ),
                }
            }

        @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != GATT_SERVICE_IFACE:
                raise InvalidArgsException()
            return self.get_properties()[GATT_SERVICE_IFACE]

    class Characteristic(dbus.service.Object):
        def __init__(self, bus, index: int, uuid: str, flags: list[str], service: Service) -> None:
            self.path = f"{service.path}/char{index}"
            self.uuid = uuid
            self.flags = flags
            self.service = service
            self.notifying = False
            super().__init__(bus, self.path)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            return {
                GATT_CHRC_IFACE: {
                    "Service": self.service.get_path(),
                    "UUID": self.uuid,
                    "Flags": self.flags,
                }
            }

        @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != GATT_CHRC_IFACE:
                raise InvalidArgsException()
            return self.get_properties()[GATT_CHRC_IFACE]

        @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, _options):
            raise NotSupportedException()

        @dbus.service.method(GATT_CHRC_IFACE)
        def StartNotify(self):
            if "notify" not in self.flags:
                raise NotSupportedException()
            self.notifying = True

        @dbus.service.method(GATT_CHRC_IFACE)
        def StopNotify(self):
            self.notifying = False

        @dbus.service.signal(PROPERTIES_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

    class Advertisement(dbus.service.Object):
        def __init__(self, bus) -> None:
            self.path = "/com/medai/advertisement0"
            super().__init__(bus, self.path)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def get_properties(self):
            return {
                ADVERTISEMENT_IFACE: {
                    "Type": "peripheral",
                    "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s"),
                }
            }

        @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != ADVERTISEMENT_IFACE:
                raise InvalidArgsException()
            return self.get_properties()[ADVERTISEMENT_IFACE]

        @dbus.service.method(ADVERTISEMENT_IFACE)
        def Release(self):
            print("Advertisement released", flush=True)

    class ResponseCharacteristic(Characteristic):
        def __init__(self, bus, service: Service) -> None:
            super().__init__(bus, 1, RESPONSE_UUID, ["notify"], service)
            self._pending_chunks: list[bytes] = []
            self._sending = False

        def send_json(self, response: dict) -> None:
            encoded = (json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
            self._pending_chunks.extend(
                encoded[index : index + CHUNK_SIZE]
                for index in range(0, len(encoded), CHUNK_SIZE)
            )
            if not self._sending:
                self._sending = True
                GLib.idle_add(self._drain)

        def _drain(self):
            if not self._pending_chunks:
                self._sending = False
                return False
            if not self.notifying:
                self._pending_chunks.clear()
                self._sending = False
                print("No Flutter subscriber; BLE response dropped.", file=sys.stderr)
                return False
            chunk = self._pending_chunks.pop(0)
            self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(chunk, signature="y")}, [])
            GLib.timeout_add(NOTIFICATION_DELAY_MS, self._drain)
            return False

    class CommandCharacteristic(Characteristic):
        def __init__(self, bus, service: Service, response: ResponseCharacteristic) -> None:
            super().__init__(bus, 0, COMMAND_UUID, ["write"], service)
            self._response = response
            self._buffer = bytearray()

        @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
        def WriteValue(self, value, _options):
            self._buffer.extend(bytes(value))
            while b"\n" in self._buffer:
                raw, _, remainder = self._buffer.partition(b"\n")
                self._buffer = bytearray(remainder)
                self._handle(raw)

        def _handle(self, raw: bytes) -> None:
            if not raw.strip():
                return
            try:
                message = json.loads(raw.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("Request must be a JSON object.")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                print(f"Invalid Flutter message: {error}", file=sys.stderr, flush=True)
                self._response.send_json({"type": "error", "text": f"Invalid request: {error}", "done": True})
                return

            msg_id = str(message.get("id", ""))
            msg_type = str(message.get("type", "chat")).lower()
            msg_text = str(message.get("text", "")).strip()
            session_id = str(message.get("session_id", "ble_default"))

            print(f"[BLE ←] id={msg_id!r} type={msg_type!r} text={msg_text[:60]!r}", flush=True)

            supervisor = _get_supervisor()
            if msg_type in ("vitals", "schedules", "status"):
                tool_map = {
                    "vitals": "read_vitals",
                    "schedules": "read_pending_medications",
                    "status": "get_system_status",
                }
                try:
                    if supervisor:
                        result = supervisor.dispatch(tool_map[msg_type])
                    else:
                        result = {"error": "AgentSupervisorNode not available"}
                except Exception as exc:
                    result = {"error": str(exc)}
                self._response.send_json({"id": msg_id, "type": msg_type, "data": result, "done": True})
                print(f"[BLE →] {msg_type} data sent.", flush=True)
                return

            self._response.send_json({"id": msg_id, "type": "ack", "done": False})

            if not supervisor:
                self._response.send_json({
                    "id": msg_id,
                    "type": "chat",
                    "text": "AI supervisor is not available. Please try again later.",
                    "done": True,
                })
                return

            if not msg_text:
                self._response.send_json({
                    "id": msg_id,
                    "type": "error",
                    "text": "Empty message received.",
                    "done": True,
                })
                return

            def _llm_worker():
                try:
                    reply = supervisor.process_user_message(msg_text, session_id=session_id)
                except Exception as exc:
                    reply = f"⚠️ Error processing request: {exc}"
                    print(f"[LLM ERROR] {exc}", file=sys.stderr, flush=True)

                print(f"[BLE →] id={msg_id!r} reply={reply[:80]!r}", flush=True)
                GLib.idle_add(
                    self._response.send_json,
                    {"id": msg_id, "type": "chat", "text": reply, "done": True},
                )

            threading.Thread(target=_llm_worker, daemon=True).start()


def find_adapter(bus):
    objects = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE).GetManagedObjects()
    for path, interfaces in objects.items():
        if GATT_MANAGER_IFACE in interfaces and ADVERTISING_MANAGER_IFACE in interfaces:
            return bus.get_object(BLUEZ, path)
    raise RuntimeError("No Bluetooth adapter with GATT and advertising support found.")


def raise_error(error: Exception, operation: str = "BlueZ operation") -> None:
    """Surface asynchronous BlueZ errors in the terminal with useful context."""
    raise RuntimeError(f"{operation} failed: {error}")


def register_legacy_advertisement() -> None:
    # btmgmt requires a non-zero advertising-instance ID as its final argument.
    command = ["btmgmt", "add-adv", "-u", SERVICE_UUID, "-c", "-g", "1"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown btmgmt error"
        raise RuntimeError(f"Could not register legacy advertisement: {detail}")
    print("Advertising through btmgmt legacy mode", flush=True)


def start_ble_server(
    legacy_advertising: bool = False,
    fallback_to_legacy_advertising: bool = True,
) -> None:
    if dbus is None:
        raise RuntimeError("dbus-python and PyGObject are required to run the BLE server.")
    threading.Thread(target=_get_supervisor, daemon=True).start()

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter = find_adapter(bus)
    application = Application(bus)
    service = Service(bus, 0, SERVICE_UUID)
    response = ResponseCharacteristic(bus, service)
    command = CommandCharacteristic(bus, service, response)
    service.add_characteristic(command)
    service.add_characteristic(response)
    application.add_service(service)
    advertisement = Advertisement(bus)

    def on_gatt_error(error: Exception) -> None:
        raise_error(error, "GATT application registration")

    legacy_started = False

    def start_legacy_advertising() -> None:
        nonlocal legacy_started
        if legacy_started:
            return
        register_legacy_advertisement()
        legacy_started = True

    def on_advertising_error(error: Exception) -> None:
        # Some Raspberry Pi/BlueZ combinations expose an advertising manager but
        # reject its D-Bus registration.  The older receiver worked around this
        # by adding the same service UUID with btmgmt, so retain that path.
        print(f"[BLE] BlueZ advertisement registration failed: {error}", file=sys.stderr, flush=True)
        if not fallback_to_legacy_advertising:
            raise_error(error, "BLE advertisement registration")
        print("[BLE] Falling back to btmgmt advertising.", flush=True)
        try:
            start_legacy_advertising()
        except Exception as fallback_error:
            raise RuntimeError(
                f"BLE advertisement registration failed ({error}); "
                f"legacy fallback also failed: {fallback_error}"
            ) from fallback_error

    dbus.Interface(adapter, GATT_MANAGER_IFACE).RegisterApplication(
        application.get_path(),
        {},
        reply_handler=lambda: print("GATT service registered", flush=True),
        error_handler=on_gatt_error,
    )

    if legacy_advertising:
        print("[BLE] Using requested btmgmt legacy advertising mode.", flush=True)
        start_legacy_advertising()
    else:
        print("[BLE] Registering advertisement through BlueZ.", flush=True)
        dbus.Interface(adapter, ADVERTISING_MANAGER_IFACE).RegisterAdvertisement(
            advertisement.get_path(),
            {},
            reply_handler=lambda: print(f"Advertising as {DEVICE_NAME}", flush=True),
            error_handler=on_advertising_error,
        )

    print("Waiting for a Flutter app to connect...", flush=True)
    GLib.MainLoop().run()


class BLEReceiverNode(threading.Thread):
    """Thread wrapper for launching the BLE GATT server as a node."""

    def __init__(self, legacy_advertising: bool = False):
        super().__init__(daemon=True, name="pillbox-ble-receiver")
        self.legacy_advertising = legacy_advertising

    def run(self) -> None:
        try:
            start_ble_server(legacy_advertising=self.legacy_advertising)
        except Exception as exc:
            print(f"[BLE Node Error] {exc}", file=sys.stderr)
