"""SQLite connection and schema management for separate memory.db database."""
import sqlite3
from pathlib import Path
from pillbox_config import MEMORY_DB_PATH


def connect_memory(db_path: Path | str = MEMORY_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_memory_database(db_path: Path | str = MEMORY_DB_PATH) -> None:
    with connect_memory(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                tool_results TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                summary_text TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
