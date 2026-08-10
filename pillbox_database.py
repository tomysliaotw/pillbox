"""SQLite access and schema for all pillbox nodes."""
import sqlite3
from pathlib import Path
from typing import Iterable

from pillbox_config import DB_PATH


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pill_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                box_index INTEGER NOT NULL,
                time_str TEXT NOT NULL,
                disease_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS medication_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                box_index INTEGER NOT NULL,
                scheduled_time TEXT NOT NULL,
                taken_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                disease_name TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vitals_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                bpm REAL NOT NULL,
                spo2 REAL NOT NULL,
                temperature REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS biomarker_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                vitals_id INTEGER NOT NULL,
                symptom_tags TEXT NOT NULL,
                baseline_delta TEXT NOT NULL,
                risk_level INTEGER NOT NULL,
                ai_advice TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nhi_drug_database (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drug_name TEXT UNIQUE NOT NULL,
                indication TEXT,
                warning TEXT,
                license_id TEXT
            );
        """)
        # Older project scripts created this table before `license_id` existed.
        # SQLite CREATE IF NOT EXISTS does not migrate that schema automatically.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(nhi_drug_database)")}
        if "license_id" not in columns:
            conn.execute("ALTER TABLE nhi_drug_database ADD COLUMN license_id TEXT")


def replace_drug_catalogue(rows: Iterable[tuple[str, str, str, str]], db_path: Path | str = DB_PATH) -> int:
    """Atomically replace the catalogue only after input has been fully parsed."""
    rows = list(rows)
    with connect(db_path) as conn:
        conn.execute("DELETE FROM nhi_drug_database")
        conn.executemany(
            """INSERT OR IGNORE INTO nhi_drug_database
               (drug_name, indication, warning, license_id) VALUES (?, ?, ?, ?)""",
            rows,
        )
        return conn.execute("SELECT COUNT(*) FROM nhi_drug_database").fetchone()[0]
