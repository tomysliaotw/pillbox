"""Shared configuration and runtime state for the smart pillbox."""
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = PROJECT_DIR / "smart_pillbox.db"
TFDA_ARCHIVE_PATH = PROJECT_DIR / "36_5.json.zip"

BG_DARK = "#0F172A"
BG_PANEL = "#1E293B"
TEXT_MAIN = "#F8FAFC"
TEXT_SUB = "#94A3B8"
ACCENT_BLUE = "#0EA5E9"
ACCENT_GREEN = "#10B981"
ACCENT_RED = "#EF4444"
ACCENT_YELLOW = "#F59E0B"

shared_state = {
    "sensor_status": "IDLE",
    "progress": 0,
    "bpm": 0.0,
    "spo2": 0.0,
    "temp": 0.0,
    "ai_status": "機器待命中...",
    "ai_tag": "",
    "ai_advice": "請將手指輕壓於感測器上方紅光處，並保持靜止約 8 秒鐘。",
    "ai_color": TEXT_SUB,
}
