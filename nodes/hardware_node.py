"""Long-running hardware worker for LEDs and vital-sign acquisition."""
import logging
import statistics
import threading
import time

import numpy as np

from nodes.vitals_processor_node import calculate_vitals_fft
from pillbox_config import ACCENT_GREEN, ACCENT_RED, ACCENT_YELLOW, TEXT_SUB, shared_state
from pillbox_database import connect

LOG = logging.getLogger(__name__)
LED_PINS = (4, 5, 6, 12, 16, 18, 19, 20, 21, 22, 25, 26)


class HardwareNode(threading.Thread):
    """Owns physical I/O. It may run in simulated mode on development machines."""
    FS, BUFFER_SIZE, MAX_ADDR, MLX_ADDR = 20, 160, 0x57, 0x5A

    def __init__(self, analyzer, stop_event: threading.Event | None = None):
        super().__init__(daemon=True, name="pillbox-hardware")
        self.analyzer = analyzer
        self.stop_event = stop_event or threading.Event()
        self.bus, self.leds, self.hardware_available, self.last_active = None, [], True, []
        self._initialize_hardware()

    def _initialize_hardware(self) -> None:
        try:
            from gpiozero import LED
            self.leds = [LED(pin) for pin in LED_PINS]
        except Exception as exc:
            LOG.warning("LED initialization unavailable: %s", exc)
        try:
            # Raspberry Pi OS may provide the legacy ``smbus`` package, while
            # the venv-friendly dependency is named ``smbus2``.
            try:
                import smbus2 as smbus
            except ImportError:
                import smbus
            self.bus = smbus.SMBus(3)
            for register, value in ((0x04, 0x00), (0x09, 0x03), (0x0A, 0x27), (0x0C, 0x3F), (0x0D, 0x3F)):
                self.bus.write_byte_data(self.MAX_ADDR, register, value)
        except Exception as exc:
            self.hardware_available = False
            LOG.warning("I2C unavailable; running simulated sensor mode: %s", exc)

    def update_led_schedule(self) -> None:
        if not self.leds:
            return
        now = time.strftime("%H:%M")
        try:
            with connect() as conn:
                active_boxes = [row[0] for row in conn.execute("SELECT box_index FROM pill_schedules WHERE time_str <= ?", (now,))]
            if active_boxes != self.last_active:
                LOG.info("Active pill boxes: %s", active_boxes)
                self.last_active = active_boxes
            for index, led in enumerate(self.leds):
                led.on() if index in active_boxes else led.off()
        except Exception:
            LOG.exception("Unable to update LED schedule")

    def get_temperature(self) -> float:
        if not self.hardware_available:
            return 36.5
        try:
            ambient = self.bus.read_word_data(self.MLX_ADDR, 0x06) * .02 - 273.15
            skin = self.bus.read_word_data(self.MLX_ADDR, 0x07) * .02 - 273.15
            if 10.0 < skin < 50.0:
                return round(skin + .1 * (skin - ambient) + (1.2 if skin > 34.5 else 2.2), 1)
        except Exception:
            LOG.exception("Temperature read failed")
        return 0.0

    def get_optical_data(self) -> tuple[int, int]:
        if not self.hardware_available:
            return 80000 + np.random.randint(-1000, 1000), 80000 + np.random.randint(-1000, 1000)
        try:
            data = self.bus.read_i2c_block_data(self.MAX_ADDR, 0x07, 6)
            red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
            ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
            return ir, red
        except Exception:
            LOG.exception("Optical sensor read failed")
            return 0, 0

    @staticmethod
    def _idle_state() -> None:
        shared_state.update(sensor_status="IDLE", progress=0, bpm=0, spo2=0, temp=0,
            ai_status="機器待命中...", ai_tag="", ai_advice="請將手指輕壓於感測器上方紅光處，並保持靜止約 8 秒鐘。", ai_color=TEXT_SUB)

    def _store_measurement(self, bpm: float, spo2: float, temp: float) -> None:
        result = self.analyzer.evaluate(bpm, spo2, temp)
        shared_state.update(bpm=bpm, spo2=spo2, temp=temp, ai_status=result["status"], ai_tag=result["tag"], ai_advice=result["advice"],
            ai_color=ACCENT_RED if result["level"] == 2 else ACCENT_YELLOW if result["level"] == 1 else ACCENT_GREEN)
        with connect() as conn:
            cursor = conn.execute("INSERT INTO vitals_log (bpm, spo2, temperature) VALUES (?, ?, ?)", (bpm, spo2, temp))
            conn.execute("""INSERT INTO biomarker_analysis (vitals_id, symptom_tags, baseline_delta, risk_level, ai_advice)
                VALUES (?, ?, ?, ?, ?)""", (cursor.lastrowid, result["tag"], result["delta_str"], result["level"], result["advice"]))

    def run(self) -> None:
        ir_data, red_data, temperatures, bpms, spo2s = [], [], [], [], []
        last_led, last_write, counter = 0.0, 0.0, 0
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - last_led >= 1:
                self.update_led_schedule(); last_led = now
            ir, red = self.get_optical_data()
            if ir <= 50000:
                if shared_state["sensor_status"] != "IDLE":
                    self._idle_state(); ir_data.clear(); red_data.clear(); temperatures.clear(); bpms.clear(); spo2s.clear()
                self.stop_event.wait(1 / self.FS); continue
            if shared_state["sensor_status"] == "IDLE":
                shared_state.update(sensor_status="MEASURING", progress=0); counter = 0
            ir_data.append(ir); red_data.append(red)
            temperature = self.get_temperature()
            if temperature > 30: temperatures.append(temperature)
            del ir_data[:-self.BUFFER_SIZE]; del red_data[:-self.BUFFER_SIZE]; del temperatures[:-self.BUFFER_SIZE]
            shared_state["progress"] = min(100, int(len(ir_data) / self.BUFFER_SIZE * 100))
            if len(ir_data) == self.BUFFER_SIZE:
                counter += 1
                if counter >= self.FS:
                    counter = 0; bpm, spo2, valid = calculate_vitals_fft(ir_data, red_data, self.FS)
                    if valid and 40 < bpm < 160:
                        bpms.append(bpm); spo2s.append(spo2); del bpms[:-4]; del spo2s[:-4]
                        if len(bpms) == 4 and max(bpms) - min(bpms) <= 8 and max(spo2s) - min(spo2s) <= 3:
                            shared_state["sensor_status"] = "CONTINUOUS"
                            if now - last_write >= 5:
                                last_write = now
                                try: self._store_measurement(round(statistics.median(bpms), 1), round(statistics.median(spo2s), 1), statistics.median(temperatures) if temperatures else 36.5)
                                except Exception: LOG.exception("Measurement persistence failed")
                        else:
                            shared_state["sensor_status"] = "CALIBRATING"
                    else:
                        del ir_data[:80]; del red_data[:80]; bpms.clear(); spo2s.clear(); shared_state.update(sensor_status="MEASURING", progress=50)
            self.stop_event.wait(1 / self.FS)
