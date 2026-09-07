"""UI Node encapsulating the Tkinter medical touch application."""
from datetime import datetime
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from nodes.agent_supervisor_node import AgentSupervisorNode
from pillbox_config import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_DARK,
    BG_PANEL,
    DB_PATH,
    TEXT_MAIN,
    TEXT_SUB,
    shared_state,
)
from pillbox_database import connect


class UINode:
    """Medical touch dashboard and schedule management node."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("智慧醫療藥盒 - 整合大腦")
        self.root.geometry("800x480")
        self.root.configure(bg=BG_DARK)
        self.db_path = DB_PATH
        self.search_timer = None
        self.active_entry = None
        self.assistant_window = None
        self.assistant_transcript = None
        self.assistant_input = None
        self.assistant_send_button = None
        self.quick_ai_input = None
        self.supervisor = AgentSupervisorNode()

        self.setup_styles()

        self.main_page = tk.Frame(self.root, bg=BG_DARK)
        self.schedule_page = tk.Frame(self.root, bg=BG_DARK)

        self.build_main_page()
        self.build_schedule_page()

        self.main_page.pack(fill="both", expand=True)
        self.update_clock()
        self.refresh_upcoming_schedule()
        self.poll_hardware_state()

    def setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", font=("微軟正黑體", 14, "bold"), padding=8)
        style.configure("Primary.TButton", background=ACCENT_BLUE, foreground=TEXT_MAIN, borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#0284C7")])
        style.configure("Success.TButton", background=ACCENT_GREEN, foreground="#000000", borderwidth=0)
        style.map("Success.TButton", background=[("active", "#059669")])
        style.configure("Danger.TButton", background=ACCENT_RED, foreground=TEXT_MAIN, borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#DC2626")])
        style.configure("Small.TButton", font=("微軟正黑體", 12, "bold"), padding=2, background=BG_PANEL, foreground=TEXT_MAIN)

    def set_active_entry(self, entry_widget) -> None:
        self.active_entry = entry_widget
        self.show_keyboard()

    def open_ai_assistant_dialog(self) -> None:
        if self.assistant_window and self.assistant_window.winfo_exists():
            self.assistant_window.deiconify()
            self.assistant_window.lift()
            self.assistant_input.focus_set()
            return

        window = tk.Toplevel(self.root)
        self.assistant_window = window
        window.title("🤖 Llama AI 醫療助理")
        window.geometry("680x560")
        window.minsize(520, 420)
        window.configure(bg=BG_DARK)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self.close_ai_assistant)

        header = tk.Frame(window, bg=BG_DARK)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(
            header,
            text="Llama AI 醫療助理",
            font=("微軟正黑體", 18, "bold"),
            bg=BG_DARK,
            fg=TEXT_MAIN,
        ).pack(side="left")
        tk.Label(
            header,
            text="對話紀錄會自動保存",
            font=("微軟正黑體", 10),
            bg=BG_DARK,
            fg=TEXT_SUB,
        ).pack(side="right", pady=(5, 0))

        transcript_frame = tk.Frame(window, bg=BG_PANEL)
        transcript_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        scrollbar = ttk.Scrollbar(transcript_frame)
        scrollbar.pack(side="right", fill="y")
        self.assistant_transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            font=("微軟正黑體", 12),
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
            padx=14,
            pady=12,
            yscrollcommand=scrollbar.set,
        )
        self.assistant_transcript.pack(fill="both", expand=True)
        scrollbar.config(command=self.assistant_transcript.yview)
        self.assistant_transcript.tag_configure("user", foreground=ACCENT_BLUE, spacing1=8)
        self.assistant_transcript.tag_configure("assistant", foreground=TEXT_MAIN, spacing1=8)
        self.assistant_transcript.tag_configure("meta", foreground=TEXT_SUB, font=("微軟正黑體", 9))

        input_frame = tk.Frame(window, bg=BG_DARK)
        input_frame.pack(fill="x", padx=16, pady=(0, 14))
        self.assistant_input = tk.Entry(
            input_frame,
            font=("微軟正黑體", 13),
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
        )
        self.assistant_input.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 8))
        self.assistant_input.bind("<Return>", self.send_assistant_message)
        self.assistant_send_button = ttk.Button(
            input_frame,
            text="送出",
            style="Primary.TButton",
            command=self.send_assistant_message,
        )
        self.assistant_send_button.pack(side="right")

        self.load_assistant_history()
        self.assistant_input.focus_set()

    def close_ai_assistant(self) -> None:
        if self.assistant_window and self.assistant_window.winfo_exists():
            self.assistant_window.destroy()
        self.assistant_window = None
        self.assistant_transcript = None
        self.assistant_input = None
        self.assistant_send_button = None

    def append_assistant_message(self, role: str, content: str) -> None:
        if not self.assistant_transcript:
            return
        label = "您" if role == "user" else "AI 助理"
        self.assistant_transcript.config(state="normal")
        self.assistant_transcript.insert(tk.END, f"{label}\n", (role,))
        self.assistant_transcript.insert(tk.END, f"{content.strip()}\n\n", (role,))
        self.assistant_transcript.config(state="disabled")
        self.assistant_transcript.see(tk.END)

    def load_assistant_history(self) -> None:
        history = self.supervisor.memory_manager.get_short_term_memory(limit=50)
        if not history:
            self.append_assistant_message("assistant", "您好，我可以協助您查詢生理數據、搜尋藥物或設定服藥提醒。")
            return
        for message in history:
            self.append_assistant_message(message["role"], message["content"])

    def send_assistant_message(self, _event=None) -> str:
        if not self.assistant_input or not self.assistant_send_button:
            return "break"
        prompt = self.assistant_input.get().strip()
        if not prompt or str(self.assistant_send_button["state"]) == "disabled":
            return "break"

        self.assistant_input.delete(0, tk.END)
        self.append_assistant_message("user", prompt)
        self.assistant_input.config(state="disabled")
        self.assistant_send_button.config(state="disabled", text="處理中...")

        def process_message() -> None:
            try:
                reply = self.supervisor.process_user_message(prompt)
                error = None
            except Exception as exc:
                reply = ""
                error = str(exc)
            self.root.after(0, self.finish_assistant_message, reply, error)

        threading.Thread(target=process_message, daemon=True).start()
        return "break"

    def finish_assistant_message(self, reply: str, error: str | None) -> None:
        if not self.assistant_window or not self.assistant_window.winfo_exists():
            return
        if error:
            self.append_assistant_message("assistant", f"處理訊息時發生錯誤：{error}")
        else:
            self.append_assistant_message("assistant", reply)
            self.refresh_upcoming_schedule()
        self.assistant_input.config(state="normal")
        self.assistant_send_button.config(state="normal", text="送出")
        self.assistant_input.focus_set()

    def send_quick_ai_message(self, _event=None) -> str:
        prompt = self.quick_ai_input.get().strip()
        if not prompt or prompt == "輸入問題詢問 AI...":
            return "break"
        self.quick_ai_input.delete(0, tk.END)
        self.open_ai_assistant_dialog()
        self.assistant_input.insert(0, prompt)
        self.send_assistant_message()
        return "break"

    def build_main_page(self) -> None:
        btn_frame = tk.Frame(self.main_page, bg=BG_DARK)
        btn_frame.pack(side="bottom", fill="x", pady=10, padx=15)

        btn_ai = ttk.Button(btn_frame, text="🤖 AI 助手", style="Primary.TButton", command=self.open_ai_assistant_dialog)
        btn_ai.pack(side="left", expand=True, fill="x", padx=(0, 2))
        btn_setting = ttk.Button(btn_frame, text="➕ 管理排程", style="Primary.TButton", command=self.switch_to_schedule)
        btn_setting.pack(side="left", expand=True, fill="x", padx=(2, 2))
        btn_take = ttk.Button(btn_frame, text="✔ 確認拿藥", style="Success.TButton", command=self.confirm_medication)
        btn_take.pack(side="right", expand=True, fill="x", padx=(2, 0))

        quick_ai_frame = tk.Frame(self.main_page, bg=BG_DARK)
        quick_ai_frame.pack(side="bottom", fill="x", padx=15, pady=(0, 4))
        self.quick_ai_input = tk.Entry(
            quick_ai_frame,
            font=("微軟正黑體", 12),
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
        )
        self.quick_ai_input.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.quick_ai_input.insert(0, "輸入問題詢問 AI...")
        self.quick_ai_input.bind("<FocusIn>", self.clear_quick_ai_placeholder)
        self.quick_ai_input.bind("<Return>", self.send_quick_ai_message)
        ttk.Button(
            quick_ai_frame,
            text="🤖 詢問 AI",
            style="Primary.TButton",
            command=self.send_quick_ai_message,
        ).pack(side="right")

        header_frame = tk.Frame(self.main_page, bg=BG_DARK)
        header_frame.pack(side="top", fill="x", padx=15, pady=(5, 0))

        self.greeting_lbl = tk.Label(header_frame, text="☀️ 早安，長輩", font=("微軟正黑體", 16, "bold"), bg=BG_DARK, fg=ACCENT_BLUE)
        self.greeting_lbl.pack(side="left")
        self.clock_lbl = tk.Label(header_frame, text="00:00:00", font=("微軟正黑體", 32, "bold"), bg=BG_DARK, fg=TEXT_MAIN)
        self.clock_lbl.pack(side="right")

        self.upcoming_listbox = tk.Listbox(
            self.main_page, font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_MAIN, bd=0, height=2, highlightthickness=0
        )
        self.upcoming_listbox.pack(side="bottom", fill="x", padx=15, pady=(0, 5))

        dashboard_frame = tk.Frame(self.main_page, bg=BG_DARK)
        dashboard_frame.pack(side="top", fill="both", expand=True, padx=15, pady=5)

        vitals_panel = tk.Frame(dashboard_frame, bg=BG_PANEL, bd=1, highlightbackground="#334155", highlightthickness=1)
        vitals_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))

        tk.Label(vitals_panel, text="即時生理特徵", font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_SUB).pack(pady=(5, 0))

        self.bpm_lbl = tk.Label(vitals_panel, text="-- BPM", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.bpm_lbl.pack(pady=0)
        self.spo2_lbl = tk.Label(vitals_panel, text="-- %", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.spo2_lbl.pack(pady=0)
        self.temp_lbl = tk.Label(vitals_panel, text="-- °C", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.temp_lbl.pack(pady=0)

        self.prog_canvas = tk.Canvas(vitals_panel, width=280, height=16, bg=BG_PANEL, highlightthickness=1, highlightbackground=ACCENT_BLUE)
        self.prog_canvas.pack(pady=(5, 2))
        self.prog_status_lbl = tk.Label(vitals_panel, text="等待手指置入...", font=("微軟正黑體", 10), bg=BG_PANEL, fg=TEXT_SUB)
        self.prog_status_lbl.pack(pady=(0, 5))

        ai_panel = tk.Frame(dashboard_frame, bg=BG_PANEL, bd=1, highlightbackground="#334155", highlightthickness=1)
        ai_panel.pack(side="right", fill="both", expand=True, padx=(5, 0))

        tk.Label(ai_panel, text="🧠 AI 決策樹護理師", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg="#D946EF").pack(pady=(5, 5))
        self.ai_status_lbl = tk.Label(ai_panel, text="機器待命中...", font=("微軟正黑體", 16, "bold"), bg=BG_PANEL, fg=TEXT_SUB)
        self.ai_status_lbl.pack(pady=(5, 5))
        self.ai_tag_lbl = tk.Label(ai_panel, text="", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.ai_tag_lbl.pack()
        self.ai_advice_lbl = tk.Label(
            ai_panel, text="請將手指輕壓於感測器上方紅光處，\n並保持靜止約 8 秒鐘。", font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_MAIN, wraplength=300, justify="center"
        )
        self.ai_advice_lbl.pack(pady=5, fill="x", padx=10)

    def clear_quick_ai_placeholder(self, _event=None) -> None:
        if self.quick_ai_input.get() == "輸入問題詢問 AI...":
            self.quick_ai_input.delete(0, tk.END)

    def draw_hightech_bar(self, percentage: float, status: str) -> None:
        self.prog_canvas.delete("bar")
        if status == "CONTINUOUS":
            color = ACCENT_GREEN
        elif status == "CALIBRATING":
            color = "#A855F7"
        elif status == "MEASURING" and percentage == 50:
            color = ACCENT_YELLOW
        else:
            color = ACCENT_BLUE

        bar_width = int((percentage / 100) * 278)
        if bar_width > 0:
            self.prog_canvas.create_rectangle(1, 1, 1 + bar_width, 15, fill=color, outline="", tags="bar")

    def poll_hardware_state(self) -> None:
        st = shared_state["sensor_status"]
        prog = shared_state["progress"]
        self.draw_hightech_bar(prog, st)

        if st == "IDLE":
            self.prog_status_lbl.config(text="等待手指置入...", fg=TEXT_SUB)
            self.bpm_lbl.config(text="-- BPM")
            self.spo2_lbl.config(text="-- %")
            self.temp_lbl.config(text="-- °C")
        elif st == "MEASURING":
            if prog == 50:
                self.prog_status_lbl.config(text=f"[ 干擾 ] 雜訊過濾重新對位中... {prog}%", fg=ACCENT_YELLOW)
            else:
                self.prog_status_lbl.config(text=f"[ 掃描 ] 生理光譜矩陣擷取中... {prog}%", fg=ACCENT_BLUE)
        elif st == "CALIBRATING":
            self.prog_status_lbl.config(text="[ 穩定校正 ] 防晃動檢測中", fg="#A855F7")
            if shared_state["bpm"] > 0:
                self.bpm_lbl.config(text=f"〰️ {shared_state['bpm']} BPM")
                self.spo2_lbl.config(text=f"〰️ {shared_state['spo2']} %")
                self.temp_lbl.config(text=f"〰️ {shared_state['temp']} °C")
        elif st == "CONTINUOUS":
            self.prog_status_lbl.config(text="[ 鎖定追蹤 ] 醫療級數值即時監控中", fg=ACCENT_GREEN)
            if shared_state["bpm"] > 0:
                self.bpm_lbl.config(text=f"💓 {shared_state['bpm']} BPM")
                self.spo2_lbl.config(text=f"🩸 {shared_state['spo2']} %")
                self.temp_lbl.config(text=f"🌡️ {shared_state['temp']} °C")

        self.ai_status_lbl.config(text=shared_state["ai_status"], fg=shared_state["ai_color"])
        self.ai_tag_lbl.config(text=f"【{shared_state['ai_tag']}】" if shared_state["ai_tag"] else "")
        self.ai_advice_lbl.config(text=shared_state["ai_advice"])

        self.root.after(100, self.poll_hardware_state)

    def update_clock(self) -> None:
        now = datetime.now()
        self.clock_lbl.config(text=now.strftime("%H:%M:%S"))
        hour = now.hour
        if 5 <= hour < 12:
            greeting = "🌅 早安，長輩"
        elif 12 <= hour < 18:
            greeting = "☀️ 午安，長輩"
        else:
            greeting = "🌙 晚安，長輩"
        self.greeting_lbl.config(text=greeting)
        self.root.after(1000, self.update_clock)

    def refresh_upcoming_schedule(self) -> None:
        self.upcoming_listbox.delete(0, tk.END)
        try:
            with connect() as conn:
                rows = conn.execute("SELECT box_index, time_str, disease_name FROM pill_schedules ORDER BY time_str ASC").fetchall()

            if not rows:
                self.upcoming_listbox.insert(tk.END, "🎉 太棒了！今日目前沒有任何待服用的排程。")
                self.upcoming_listbox.itemconfig(0, {"fg": TEXT_SUB})
            else:
                for row in rows:
                    box_idx, t_str, d_name = row["box_index"], row["time_str"], row["disease_name"]
                    display_text = f"⏰ {t_str} | 💊 第 {box_idx+1:02d} 格 | 🩺 {d_name}"
                    self.upcoming_listbox.insert(tk.END, display_text)
                    if t_str <= datetime.now().strftime("%H:%M"):
                        self.upcoming_listbox.itemconfig(tk.END, {"fg": ACCENT_RED})
        except Exception:
            pass

    def build_schedule_page(self) -> None:
        self.ctrl_panel = tk.Frame(self.schedule_page, bg=BG_PANEL, bd=0)
        self.ctrl_panel.pack(side="top", fill="x", padx=10, pady=(5, 2), ipady=2)

        tk.Label(self.ctrl_panel, text="格:", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN).pack(side="left", padx=(5, 2))
        self.box_var = tk.StringVar(value="01")
        box_spin = tk.Spinbox(
            self.ctrl_panel,
            from_=1,
            to=12,
            textvariable=self.box_var,
            font=("微軟正黑體", 12, "bold"),
            width=3,
            format="%02.0f",
            justify="center",
            bg=BG_DARK,
            fg=TEXT_MAIN,
            buttonbackground=BG_PANEL,
        )
        box_spin.pack(side="left", padx=2)

        tk.Label(self.ctrl_panel, text="時間:", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN).pack(side="left", padx=(10, 2))
        self.time_var = tk.StringVar(value=datetime.now().strftime("%H:%M"))
        self.time_entry = tk.Entry(
            self.ctrl_panel,
            textvariable=self.time_var,
            font=("微軟正黑體", 12, "bold"),
            justify="center",
            width=6,
            bg=BG_DARK,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
        )
        self.time_entry.pack(side="left", padx=2)
        self.time_entry.bind("<FocusIn>", lambda e: self.set_active_entry(self.time_entry))

        tk.Label(self.ctrl_panel, text="🔍檢索:", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=ACCENT_GREEN).pack(side="left", padx=(10, 2))
        self.med_var = tk.StringVar()
        self.med_entry = tk.Entry(self.ctrl_panel, textvariable=self.med_var, font=("微軟正黑體", 12), bg=BG_DARK, fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.med_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.med_entry.bind("<FocusIn>", lambda e: self.set_active_entry(self.med_entry))
        self.med_entry.bind("<KeyRelease>", lambda event: self.debounce_search(self.med_var.get()))

        hide_btn = ttk.Button(self.ctrl_panel, text="⬇️ 收起", style="Small.TButton", command=self.hide_keyboard)
        hide_btn.pack(side="right", padx=(0, 5))

        self.active_entry = self.med_entry

        self.btn_frame = tk.Frame(self.schedule_page, bg=BG_DARK)
        self.btn_frame.pack(side="bottom", fill="x", pady=5, padx=10)
        cancel_btn = ttk.Button(self.btn_frame, text="❌ 取消返回", style="Danger.TButton", command=self.switch_to_main)
        cancel_btn.pack(side="right", fill="x", expand=True, padx=(2, 0))
        save_btn = ttk.Button(self.btn_frame, text="💾 確認儲存排程", style="Success.TButton", command=self.save_schedule)
        save_btn.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.kb_frame = tk.Frame(self.schedule_page, bg=BG_DARK, height=220)
        self.kb_frame.pack_propagate(False)
        self.kb_frame.pack(side="bottom", fill="both", padx=10, pady=2)
        self.build_virtual_keyboard()

        self.result_listbox = tk.Listbox(
            self.schedule_page,
            font=("微軟正黑體", 14),
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            selectbackground=ACCENT_BLUE,
            bd=0,
            highlightthickness=1,
            highlightbackground="#334155",
        )
        self.result_listbox.pack(side="top", fill="both", expand=True, padx=10, pady=2)
        self.result_listbox.bind("<<ListboxSelect>>", self.on_med_select)

    def hide_keyboard(self) -> None:
        self.kb_frame.pack_forget()

    def show_keyboard(self) -> None:
        if not self.kb_frame.winfo_ismapped():
            self.result_listbox.pack_forget()
            self.kb_frame.pack(side="bottom", fill="both", padx=10, pady=2)
            self.result_listbox.pack(side="top", fill="both", expand=True, padx=10, pady=2)

    def build_virtual_keyboard(self) -> None:
        grid_frame = tk.Frame(self.kb_frame, bg=BG_DARK)
        grid_frame.pack(fill="both", expand=True)
        rows = [
            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
            ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
            ["A", "S", "D", "F", "G", "H", "J", "K", "L", ":"],
            ["Z", "X", "C", "V", "B", "N", "M", "⌫", " ", "清空"],
        ]
        for col in range(10):
            grid_frame.columnconfigure(col, weight=1)
        for row in range(4):
            grid_frame.rowconfigure(row, weight=1)

        for r, row_keys in enumerate(rows):
            for c, key in enumerate(row_keys):
                btn_bg = "#E14470" if key in ["⌫", "清空"] else BG_PANEL
                btn = tk.Button(
                    grid_frame,
                    text=key,
                    font=("微軟正黑體", 12, "bold"),
                    bg=btn_bg,
                    fg=TEXT_MAIN,
                    activebackground=ACCENT_BLUE,
                    bd=0,
                    command=lambda k=key: self.press_virtual_key(k),
                )
                btn.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

    def press_virtual_key(self, key: str) -> None:
        if not self.active_entry:
            self.active_entry = self.med_entry
        if key == "⌫":
            try:
                idx = self.active_entry.index(tk.INSERT)
                if idx > 0:
                    self.active_entry.delete(idx - 1)
            except Exception:
                pass
        elif key == "清空":
            self.active_entry.delete(0, tk.END)
        else:
            self.active_entry.insert(tk.INSERT, key)
        if self.active_entry == self.med_entry:
            self.debounce_search(self.med_entry.get())

    def switch_to_schedule(self) -> None:
        self.main_page.pack_forget()
        self.schedule_page.pack(fill="both", expand=True)
        self.time_var.set(datetime.now().strftime("%H:%M"))
        self.med_var.set("")
        self.result_listbox.delete(0, tk.END)
        self.active_entry = self.med_entry
        self.med_entry.focus_set()
        self.show_keyboard()

    def switch_to_main(self) -> None:
        self.schedule_page.pack_forget()
        self.refresh_upcoming_schedule()
        self.main_page.pack(fill="both", expand=True)

    def on_med_select(self, event) -> None:
        selection = self.result_listbox.curselection()
        if selection:
            selected_med = self.result_listbox.get(selection[0])
            if "找不到" not in selected_med:
                self.med_var.set(selected_med)

    def debounce_search(self, keyword: str) -> None:
        if self.search_timer is not None:
            self.root.after_cancel(self.search_timer)
        self.search_timer = self.root.after(300, self.perform_search, keyword)

    def perform_search(self, keyword: str) -> None:
        self.result_listbox.delete(0, tk.END)
        if len(keyword) < 1:
            return
        try:
            with connect() as conn:
                columns = [col[1] for col in conn.execute("PRAGMA table_info(nhi_drug_database)").fetchall()]
                search_conditions, params = [], []
                target_cols = ["drug_name", "中文品名", "英文品名", "許可證字號", "證字號", "license_id", "劑量", "成分", "dosage"]
                for col in target_cols:
                    if col in columns:
                        search_conditions.append(f"LOWER({col}) LIKE ?")
                        params.append(f"%{keyword.lower()}%")

                if not search_conditions:
                    search_conditions = ["LOWER(drug_name) LIKE ?"]
                    params = [f"%{keyword.lower()}%"]

                query = f"SELECT * FROM nhi_drug_database WHERE {' OR '.join(search_conditions)} LIMIT 80"
                rows = conn.execute(query, params).fetchall()

            blacklist = ["軟膏", "眼藥水", "注射", "栓劑", "點眼", "外用", "凝膠", "貼布", "膠布", "噴鼻", "滴劑", "乳膏", "洗劑", "噴霧", "塞劑", "外洗", "輸注", "點鼻", "滴耳"]
            unique_results = []

            for row in rows:
                row_dict = dict(row)
                name = row_dict.get("中文品名") or row_dict.get("drug_name") or row_dict.get("英文品名", "未知藥品")
                dosage = row_dict.get("劑量") or row_dict.get("dosage") or ""
                license_id = row_dict.get("許可證字號") or row_dict.get("證字號") or row_dict.get("license_id") or ""

                if not license_id:
                    lic_match = re.search(r"([衛內]署藥[製輸]字第\d+號|[A-Z]{2}\d{6,})", str(name))
                    if lic_match:
                        license_id = lic_match.group(1)
                        name = name.replace(license_id, "").strip()

                display_str = f"[{name}]"
                if dosage:
                    display_str += f" [{dosage}]"
                if license_id:
                    display_str += f" (證號: {license_id})"

                if any(bad in display_str for bad in blacklist):
                    continue
                if display_str not in unique_results:
                    unique_results.append(display_str)
                    if len(unique_results) >= 15:
                        break

            if unique_results:
                for r in unique_results:
                    self.result_listbox.insert(tk.END, r)
            else:
                self.result_listbox.insert(tk.END, "(找不到符合的口服藥品)")
        except Exception as e:
            print(f"資料庫搜尋錯誤: {e}")

    def save_schedule(self) -> None:
        try:
            box_id = int(self.box_var.get()) - 1
        except ValueError:
            messagebox.showwarning("錯誤", "藥格必須是數字！")
            return
        target_time = self.time_var.get()
        med_name = self.med_var.get()
        if not med_name or "找不到" in med_name:
            messagebox.showwarning("錯誤", "請輸入並選擇正確的藥品！")
            return
        try:
            with connect() as conn:
                conn.execute(
                    "INSERT INTO pill_schedules (box_index, time_str, disease_name) VALUES (?, ?, ?)",
                    (box_id, target_time, med_name),
                )
            self.switch_to_main()
        except Exception as e:
            messagebox.showerror("錯誤", f"資料庫錯誤: {e}")

    def confirm_medication(self) -> None:
        try:
            now_str = datetime.now().strftime("%H:%M")
            with connect() as conn:
                rows = conn.execute("SELECT id, box_index, time_str, disease_name FROM pill_schedules WHERE time_str <= ?", (now_str,)).fetchall()

                if not rows:
                    messagebox.showinfo("提示", "目前沒有任何待服用的藥物喔！")
                    return

                taken_list = []
                for row in rows:
                    sched_id, box_idx, t_str, d_name = row["id"], row["box_index"], row["time_str"], row["disease_name"]
                    taken_list.append(f"{d_name} (第 {box_idx+1} 格)")
                    conn.execute(
                        "INSERT INTO medication_history (box_index, scheduled_time, disease_name, status) VALUES (?, ?, ?, 'TAKEN')",
                        (box_idx, t_str, d_name),
                    )
                    conn.execute("DELETE FROM pill_schedules WHERE id = ?", (sched_id,))

            self.refresh_upcoming_schedule()
            msg = "\n".join(taken_list)
            messagebox.showinfo("✅ 拿藥確認成功", f"已確認服用以下藥物：\n{msg}\n\n系統已紀錄，硬體 LED 將於下一秒熄滅！")
        except Exception as e:
            messagebox.showerror("錯誤", f"資料庫操作失敗: {e}")


SmartPillboxApp = UINode
