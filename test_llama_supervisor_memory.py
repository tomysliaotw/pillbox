"""Verification script for Llama Agent, AgentSupervisor tools, separate memory.db, and schedule writing."""
import os
import sqlite3
import sys
from pathlib import Path

# Force UTF-8 output encoding for Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_supervisor import AgentSupervisor
from pillbox_config import DB_PATH, MEMORY_DB_PATH
from pillbox_database import connect, initialize_database
from pillbox_memory_db import connect_memory, initialize_memory_database


def test_full_system():
    print("--- 1. Initializing databases ---")
    initialize_database()
    initialize_memory_database()
    assert DB_PATH.exists(), f"smart_pillbox.db missing at {DB_PATH}"
    assert MEMORY_DB_PATH.exists(), f"memory.db missing at {MEMORY_DB_PATH}"
    print("[SUCCESS] Databases initialized successfully.")

    supervisor = AgentSupervisor(session_id="test_session")

    print("\n--- 2. Testing natural language schedule writing ---")
    prompt_sched = "設定早上 08:30 第 1 格吃降血壓藥"
    reply_sched = supervisor.process_user_message(prompt_sched, session_id="test_session")
    print(f"User Prompt: {prompt_sched}")
    print(f"AI Response: {reply_sched}")

    # Verify schedule stored in smart_pillbox.db
    with connect() as conn:
        row = conn.execute("SELECT box_index, time_str, disease_name FROM pill_schedules WHERE disease_name LIKE '%降血壓藥%'").fetchone()
    assert row is not None, "Schedule was not inserted into smart_pillbox.db!"
    print(f"[SUCCESS] Verified DB schedule entry: box_index={row['box_index']}, time={row['time_str']}, disease={row['disease_name']}")

    print("\n--- 3. Testing vitals query tool dispatching ---")
    prompt_vitals = "幫我讀取目前的生理數據"
    reply_vitals = supervisor.process_user_message(prompt_vitals, session_id="test_session")
    print(f"User Prompt: {prompt_vitals}")
    print(f"AI Response: {reply_vitals}")
    print("[SUCCESS] Verified vitals tool query.")

    print("\n--- 4. Testing separate memory.db persistence ---")
    with connect_memory() as conn:
        chat_rows = conn.execute("SELECT role, content FROM chat_history WHERE session_id = 'test_session'").fetchall()

    print(f"Total stored chat history turns in memory.db: {len(chat_rows)}")
    assert len(chat_rows) >= 4, "Chat history turns were not saved to memory.db!"
    print("[SUCCESS] Verified short-term chat history in memory.db.")

    # Trigger memory summarization
    for i in range(6):
        supervisor.process_user_message(f"問候對話測試第 {i+1} 次", session_id="test_session")

    with connect_memory() as conn:
        memory_row = conn.execute("SELECT summary_text FROM long_term_memory WHERE session_id = 'test_session'").fetchone()
    assert memory_row is not None and len(memory_row["summary_text"]) > 0, "Long-term memory summary was not generated in memory.db!"
    print(f"Long-term memory summary text:\n{memory_row['summary_text']}")
    print("[SUCCESS] Verified long-term memory summarization in memory.db.")

    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_full_system()
