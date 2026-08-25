"""Agent supervisor node managing Llama LLM, tool execution, and short/long term memory."""
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from llama_agent import LlamaLLMAgent
from memory_manager import MemoryManager
from nodes.tfda_sync_node import import_tfda_archive
from pillbox_config import DB_PATH, TFDA_ARCHIVE_PATH, shared_state
from pillbox_database import connect, initialize_database


class AgentSupervisorNode:
    """Independent supervisor node for natural language processing and tool dispatch."""

    def __init__(self, session_id: str = "default"):
        initialize_database()
        self.session_id = session_id
        self.memory_manager = MemoryManager(session_id=session_id)
        self.llama_agent = LlamaLLMAgent()
        self.tools: dict[str, Callable[..., dict[str, Any]]] = {
            "get_system_status": self.get_system_status,
            "read_vitals": self.read_vitals,
            "read_pending_medications": self.read_pending_medications,
            "search_drug": self.search_drug,
            "create_schedule": self.create_schedule,
            "confirm_medication": self.confirm_medication,
            "import_local_tfda_archive": self.import_local_tfda_archive,
        }

    def dispatch(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool_name not in self.tools:
            raise ValueError(f"Tool is not approved: {tool_name}")
        return self.tools[tool_name](**(arguments or {}))

    def process_user_message(self, user_input: str, session_id: str | None = None) -> str:
        target_session = session_id or self.session_id

        # 1. Store user message in short-term memory (memory.db)
        self.memory_manager.add_message(role="user", content=user_input, session_id=target_session)

        # 2. Retrieve short-term messages and long-term memory summary from memory.db
        short_term = self.memory_manager.get_short_term_memory(session_id=target_session, limit=10)
        long_term_summary = self.memory_manager.get_long_term_memory(session_id=target_session)

        # 3. Generate model response and execute tool calls via dispatch
        response_text, tool_call, tool_result = self.llama_agent.generate_response(
            user_input=user_input,
            short_term_messages=short_term,
            long_term_summary=long_term_summary,
            dispatch_fn=self.dispatch,
        )

        # 4. Store assistant response and tool metadata into memory.db
        self.memory_manager.add_message(
            role="assistant",
            content=response_text,
            tool_calls=tool_call,
            tool_results=tool_result,
            session_id=target_session,
        )

        # 5. Automatically summarize conversation into long-term memory if history grows
        self.memory_manager.summarize_and_compress(
            summarize_fn=self.llama_agent.summarize,
            trigger_threshold=8,
            session_id=target_session,
        )

        return response_text

    @staticmethod
    def get_system_status() -> dict[str, Any]:
        return {"database": str(DB_PATH), "sensor": dict(shared_state), "time": datetime.now().isoformat(timespec="seconds")}

    @staticmethod
    def read_vitals() -> dict[str, Any]:
        return {key: shared_state[key] for key in ("sensor_status", "bpm", "spo2", "temp", "ai_status", "ai_tag", "ai_advice")}

    @staticmethod
    def read_pending_medications() -> dict[str, Any]:
        with connect() as conn:
            rows = conn.execute("SELECT id, box_index, time_str, disease_name FROM pill_schedules ORDER BY time_str").fetchall()
        return {"schedules": [dict(row) for row in rows]}

    @staticmethod
    def search_drug(query: str, limit: int = 10) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("A drug search query is required.")
        with connect() as conn:
            rows = conn.execute(
                """SELECT drug_name, indication, warning, license_id FROM nhi_drug_database
                WHERE lower(drug_name) LIKE ? OR lower(license_id) LIKE ? LIMIT ?""",
                (f"%{query.lower()}%", f"%{query.lower()}%", min(max(limit, 1), 50)),
            ).fetchall()
        return {"results": [dict(row) for row in rows]}

    @staticmethod
    def create_schedule(box_index: int, time_str: str, medicine: str) -> dict[str, Any]:
        if not 0 <= int(box_index) < 12:
            raise ValueError("box_index must be between 0 and 11.")
        datetime.strptime(time_str, "%H:%M")
        if not medicine.strip():
            raise ValueError("medicine is required.")
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO pill_schedules (box_index, time_str, disease_name) VALUES (?, ?, ?)",
                (int(box_index), time_str, medicine.strip()),
            )
        return {"schedule_id": cursor.lastrowid, "requires_user_confirmation": True}

    @staticmethod
    def confirm_medication() -> dict[str, Any]:
        """Mark only currently due items as taken; call after explicit user approval."""
        now = datetime.now().strftime("%H:%M")
        with connect() as conn:
            rows = conn.execute("SELECT id, box_index, time_str, disease_name FROM pill_schedules WHERE time_str <= ?", (now,)).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT INTO medication_history (box_index, scheduled_time, disease_name, status) VALUES (?, ?, ?, 'TAKEN')",
                    (row["box_index"], row["time_str"], row["disease_name"]),
                )
                conn.execute("DELETE FROM pill_schedules WHERE id = ?", (row["id"],))
        return {"confirmed": [dict(row) for row in rows]}

    @staticmethod
    def import_local_tfda_archive(archive_path: str | None = None) -> dict[str, Any]:
        return import_tfda_archive(Path(archive_path) if archive_path else TFDA_ARCHIVE_PATH)


AgentSupervisor = AgentSupervisorNode
