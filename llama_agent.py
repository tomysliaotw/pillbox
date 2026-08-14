"""Llama LLM Agent supporting standalone Llama models, Ollama API, and AgentSupervisor tools."""
import json
import re
import urllib.request
import urllib.error
from typing import Any, Callable


SYSTEM_PROMPT = """You are a helpful Medical AI Assistant running on a Smart Pillbox Raspberry Pi system.
You can help the user inspect vitals, search drugs, set medication schedules, and check pending medications.

You have access to the following system tools via AgentSupervisor:
- get_system_status(): Check system time and status.
- read_vitals(): Check latest heart rate (BPM), SpO2, temperature, and health advice.
- read_pending_medications(): List upcoming scheduled pill reminders.
- search_drug(query: str): Search NHI drug database for indications and warnings.
- create_schedule(box_index: int, time_str: str, medicine: str): Create a new pill schedule. Note: box_index is 0 to 11. time_str is "HH:MM". medicine is drug name.
- confirm_medication(): Mark current due pills as taken.

If you need to use a tool to answer the user's request or set a schedule, reply strictly with a JSON tool call block in this format:
```json
{
  "tool": "tool_name",
  "arguments": { ... }
}
```
If no tool call is needed, provide a helpful and direct answer to the user.
"""

SUMMARY_PROMPT = """You are a concise medical memory compressor.
Given existing memory and conversation history, update the long-term summary covering:
1. User health conditions & vitals trends.
2. Scheduled medications and box indices.
3. User preferences or special requests.

Keep the summary concise, clear, and bulleted.
"""


class LlamaLLMAgent:
    def __init__(self, model_name: str = "llama3.2:1b", ollama_host: str = "http://localhost:11434"):
        self.model_name = model_name
        self.ollama_host = ollama_host

    def _call_ollama_api(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str | None:
        """Attempt calling local Ollama HTTP endpoint."""
        url = f"{self.ollama_host}/api/chat"
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("message", {}).get("content", "")
        except Exception:
            return None

    def _fallback_tool_parse(self, user_input: str) -> dict[str, Any] | None:
        """Rule-based NLU fallback for tool dispatching when offline or without server."""
        lowered = user_input.lower()
        
        # Schedule pattern: e.g. "設定 08:00 第1格 吃 阿斯匹靈" / "schedule box 0 at 08:00 for aspirin"
        time_match = re.search(r'(\d{1,2}:\d{2})', user_input)
        box_match = re.search(r'(?:box|第|格)\s*(\d{1,2})|(\d{1,2})\s*(?:格|號格|box)', user_input, re.IGNORECASE)
        
        # Checking for schedule keywords
        if any(k in user_input for k in ["排程", "提醒", "設定", "新增", "schedule", "remind", "set schedule"]):
            time_str = time_match.group(1) if time_match else "08:00"
            if len(time_str) == 4 and time_str[1] == ':':
                time_str = "0" + time_str
            
            raw_box = 1
            if box_match:
                raw_box = int(box_match.group(1) or box_match.group(2))
            # Translate 1-based UI box input (1-12) to 0-based DB box (0-11)
            box_idx = max(0, min(11, raw_box - 1)) if raw_box >= 1 else max(0, min(11, raw_box))

            # Cleanly extract drug name
            med_name = user_input
            if time_match:
                med_name = med_name.replace(time_match.group(0), "")
            if box_match:
                med_name = med_name.replace(box_match.group(0), "")
            for keyword in ["幫我", "設定", "排程", "提醒", "新增", "吃", "at", "for", "schedule", "remind", "set"]:
                med_name = re.sub(r'\b' + re.escape(keyword) + r'\b|' + re.escape(keyword), "", med_name, flags=re.IGNORECASE)
            med_name = med_name.strip() or "未指定藥物"

            return {
                "tool": "create_schedule",
                "arguments": {
                    "box_index": box_idx,
                    "time_str": time_str,
                    "medicine": med_name
                }
            }

        if any(k in lowered for k in ["生理", "心跳", "血氧", "體溫", "vitals", "bpm", "spo2", "temp"]):
            return {"tool": "read_vitals", "arguments": {}}

        if any(k in lowered for k in ["待服", "目前排程", "今天藥", "pending", "schedules"]):
            return {"tool": "read_pending_medications", "arguments": {}}

        if any(k in lowered for k in ["搜尋", "查藥", "適應症", "search", "drug"]):
            query = re.sub(r'搜尋|查藥|藥品|search|drug', '', user_input, flags=re.IGNORECASE).strip()
            return {"tool": "search_drug", "arguments": {"query": query or "aspirin"}}

        if any(k in lowered for k in ["確認拿藥", "拿藥", "已吃藥", "confirm"]):
            return {"tool": "confirm_medication", "arguments": {}}

        if any(k in lowered for k in ["系統狀態", "狀態", "status"]):
            return {"tool": "get_system_status", "arguments": {}}

        return None

    def generate_response(
        self,
        user_input: str,
        short_term_messages: list[dict[str, Any]],
        long_term_summary: str,
        dispatch_fn: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Generate response given long-term summary, short-term history, user prompt, and supervisor tools."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if long_term_summary:
            messages.append({"role": "system", "content": f"[Long-Term Memory Summary]\n{long_term_summary}"})

        for msg in short_term_messages:
            messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_input})

        # Try model via Ollama local API first
        raw_output = self._call_ollama_api(messages)
        tool_call = None
        tool_result = None

        if raw_output:
            # Check for JSON block tool call in model response
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_output, re.DOTALL)
            if not json_match:
                json_match = re.search(r'(\{\s*"tool"\s*:\s*".*?"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\})', raw_output, re.DOTALL)
            
            if json_match:
                try:
                    tool_call = json.loads(json_match.group(1))
                except Exception:
                    pass

        # Fallback to local NLU tool parser if LLM service isn't active or didn't format tool call
        if not tool_call:
            tool_call = self._fallback_tool_parse(user_input)

        if tool_call and "tool" in tool_call:
            tool_name = tool_call["tool"]
            args = tool_call.get("arguments", {})
            try:
                tool_result = dispatch_fn(tool_name, args)
                if tool_name == "create_schedule":
                    final_reply = f"✅ 已成功為您設定排程！藥格: 第 {args.get('box_index', 0)+1} 格, 時間: {args.get('time_str')}, 藥品/備註: {args.get('medicine')}。"
                elif tool_name == "read_vitals":
                    final_reply = f"📊 目前生理數據：心跳 {tool_result.get('bpm')} BPM, 血氧 {tool_result.get('spo2')}%, 體溫 {tool_result.get('temp')}°C。建議：{tool_result.get('ai_advice')}"
                elif tool_name == "read_pending_medications":
                    scheds = tool_result.get("schedules", [])
                    if not scheds:
                        final_reply = "🎉 目前沒有任何待服用的藥品排程。"
                    else:
                        formatted = ", ".join([f"第 {s['box_index']+1} 格 [{s['time_str']}] {s['disease_name']}" for s in scheds])
                        final_reply = f"⏰ 待服用排程：{formatted}"
                elif tool_name == "search_drug":
                    res = tool_result.get("results", [])
                    if not res:
                        final_reply = "🔍 未找到符合條件的藥品資訊。"
                    else:
                        top = res[0]
                        final_reply = f"💊 藥品：{top.get('drug_name')}\n適應症：{top.get('indication', '無資訊')}\n警語：{top.get('warning', '無資訊')}"
                elif tool_name == "confirm_medication":
                    confirmed = tool_result.get("confirmed", [])
                    final_reply = f"✔ 已成功確認服用 {len(confirmed)} 項藥物！"
                else:
                    final_reply = f"🛠️ 工具 {tool_name} 執行完成：{json.dumps(tool_result, ensure_ascii=False)}"
            except Exception as e:
                tool_result = {"error": str(e)}
                final_reply = f"⚠️ 執行工具 {tool_name} 時發生錯誤: {e}"
        else:
            final_reply = raw_output or f"🤖 我是智慧藥盒 AI 助手。已收到您的訊息：「{user_input}」。我可以協助您查詢生理指標、搜尋藥物、或設定拿藥提醒排程。"

        return final_reply, tool_call, tool_result

    def summarize(self, existing_summary: str, history: list[dict[str, str]]) -> str:
        """Compress conversation history into long-term memory summary."""
        messages = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": f"Existing Summary:\n{existing_summary}\n\nHistory to summarize:\n{json.dumps(history, ensure_ascii=False)}"}
        ]
        summary_result = self._call_ollama_api(messages)
        if summary_result:
            return summary_result.strip()
        
        # Rule-based fallback summarization
        topics = []
        for msg in history:
            content = msg.get("content", "")
            if "設定" in content or "schedule" in content.lower():
                topics.append("- 曾進行排程設定操作")
            if "bpm" in content.lower() or "生理" in content:
                topics.append("- 曾查詢生理數據")
        
        combined = f"使用者歷史紀錄摘要：\n" + ("\n".join(set(topics)) if topics else "- 使用者與系統進行一般對話")
        return (existing_summary + "\n" + combined).strip()
