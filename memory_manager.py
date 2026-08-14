"""Memory manager handling short-term history and long-term memory summarization in memory.db."""
import json
from typing import Any, Callable
from pillbox_memory_db import connect_memory, initialize_memory_database


class MemoryManager:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        initialize_memory_database()

    def add_message(
        self,
        role: str,
        content: str,
        tool_calls: Any | None = None,
        tool_results: Any | None = None,
        session_id: str | None = None,
    ) -> int:
        target_session = session_id or self.session_id
        t_calls_str = json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
        t_res_str = json.dumps(tool_results, ensure_ascii=False) if tool_results is not None else None
        with connect_memory() as conn:
            cursor = conn.execute(
                """INSERT INTO chat_history (session_id, role, content, tool_calls, tool_results)
                   VALUES (?, ?, ?, ?, ?)""",
                (target_session, role, content, t_calls_str, t_res_str),
            )
            return cursor.lastrowid

    def get_short_term_memory(self, session_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        target_session = session_id or self.session_id
        with connect_memory() as conn:
            rows = conn.execute(
                """SELECT id, role, content, tool_calls, tool_results, created_at
                   FROM chat_history WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
                (target_session, limit),
            ).fetchall()
        
        # Reverse to return chronological order
        result = []
        for row in reversed(rows):
            item = {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            if row["tool_calls"]:
                item["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_results"]:
                item["tool_results"] = json.loads(row["tool_results"])
            result.append(item)
        return result

    def get_long_term_memory(self, session_id: str | None = None) -> str:
        target_session = session_id or self.session_id
        with connect_memory() as conn:
            row = conn.execute(
                "SELECT summary_text FROM long_term_memory WHERE session_id = ?",
                (target_session,),
            ).fetchone()
        return row["summary_text"] if row else ""

    def update_long_term_memory(self, summary_text: str, session_id: str | None = None) -> None:
        target_session = session_id or self.session_id
        with connect_memory() as conn:
            conn.execute(
                """INSERT INTO long_term_memory (session_id, summary_text, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(session_id) DO UPDATE SET
                       summary_text = excluded.summary_text,
                       updated_at = CURRENT_TIMESTAMP""",
                (target_session, summary_text),
            )

    def count_messages(self, session_id: str | None = None) -> int:
        target_session = session_id or self.session_id
        with connect_memory() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM chat_history WHERE session_id = ?",
                (target_session,),
            ).fetchone()
        return row[0] if row else 0

    def summarize_and_compress(
        self,
        summarize_fn: Callable[[str, list[dict[str, Any]]], str],
        trigger_threshold: int = 8,
        session_id: str | None = None,
    ) -> str:
        """Summarize all messages into long-term memory if message count reaches trigger threshold."""
        target_session = session_id or self.session_id
        msg_count = self.count_messages(target_session)
        if msg_count < trigger_threshold:
            return self.get_long_term_memory(target_session)

        # Retrieve full conversation history
        with connect_memory() as conn:
            rows = conn.execute(
                """SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id ASC""",
                (target_session,),
            ).fetchall()
            history = [{"role": r["role"], "content": r["content"]} for r in rows]

        existing_summary = self.get_long_term_memory(target_session)
        new_summary = summarize_fn(existing_summary, history)
        if new_summary:
            self.update_long_term_memory(new_summary, target_session)
        return new_summary
