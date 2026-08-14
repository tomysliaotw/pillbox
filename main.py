import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
from agent_supervisor import AgentSupervisor
import time
from datetime import datetime
import re
import threading
import numpy as np
import statistics
from scipy.signal import butter, filtfilt

# ==========================================
# ⚙️ 模組一：全域變數與系統設定
# ==========================================
BG_DARK = "#0F172A"       # 深色科技背景
BG_PANEL = "#1E293B"      # 面板底色
TEXT_MAIN = "#F8FAFC"     # 主文字白
TEXT_SUB = "#94A3B8"      # 次要文字灰
ACCENT_BLUE = "#0EA5E9"   # 科技藍 (主色調)
ACCENT_GREEN = "#10B981"  # 成功綠
ACCENT_RED = "#EF4444"    # 警告紅
ACCENT_YELLOW = "#F59E0B" # 注意黃

HARDWARE_AVAILABLE = True

# 系統狀態共享字典 (讓背景感測器能把數據傳給前景 UI)
shared_state = {
    "sensor_status": "IDLE", # 狀態：IDLE, MEASURING, CALIBRATING, CONTINUOUS
    "progress": 0,
    "bpm": 0.0,
    "spo2": 0.0,
    "temp": 0.0,
    "ai_status": "機器待命中...",
    "ai_tag": "",
    "ai_advice": "請將手指輕壓於感測器上方紅光處，並保持靜止約 8 秒鐘。",
    "ai_color": TEXT_SUB
}

# The UI reads the state owned by the independent hardware node.  The legacy
# constants above are retained temporarily so the existing visual design does
# not change during the UI-node migration.
from pillbox_config import shared_state

# ==========================================
# ⚙️ 模組二：內建 AI 決策樹引擎 (AITreeAnalyzer)
# ==========================================
class AITreeAnalyzer:
    def __init__(self, base_bpm=75.0, base_spo2=98.0, base_temp=36.2):
        self.base_bpm = base_bpm
        self.base_spo2 = base_spo2
        self.base_temp = base_temp

    def evaluate(self, current_bpm, current_spo2, current_temp):
        # 建立獨立連線去查最近吃的藥
        conn = sqlite3.connect('smart_pillbox.db', timeout=3.0)
        cursor = conn.cursor()
        cursor.execute('''SELECT disease_name FROM medication_history WHERE taken_time >= datetime('now', '-4 hours') ORDER BY taken_time DESC LIMIT 1''')
        history_row = cursor.fetchone()
        recent_drug = history_row[0] if history_row else None
        recent_indication = "未知適應症"
        
        if recent_drug:
            clean_name = recent_drug.split(']')[0].replace('[', '').strip().split("(")[0].strip()
            cursor.execute("SELECT indication FROM nhi_drug_database WHERE drug_name LIKE ? LIMIT 1", (f'%{clean_name}%',))
            nhi_row = cursor.fetchone()
            if nhi_row: recent_indication = nhi_row[0]
        conn.close()

        delta_temp = current_temp - self.base_temp
        delta_spo2 = current_spo2 - self.base_spo2
        bpm_increase_ratio = (current_bpm - self.base_bpm) / self.base_bpm
        delta_str = f"BPM: {current_bpm - self.base_bpm:+.1f} ({(bpm_increase_ratio*100):+.1f}%), SpO2: {delta_spo2:+.1f}%, Temp: {delta_temp:+.1f}°C"

        res = {"level": 0, "status": "✅ 狀況極佳", "tag": "生理指標平穩", "advice": "各項指標皆與平時的健康狀態一致，藥效平穩，保持得非常好！"}

        # --- 決策樹邏輯 ---
        if delta_temp >= 1.0:
            if delta_spo2 <= -3.0:
                res = {"level": 2, "status": "🔴 高危險", "tag": "發熱伴隨血氧下降", "advice": "體溫異常且血氧低於平時水準，疑似心肺感染，請立刻就醫！"}
            elif bpm_increase_ratio >= 0.2:
                res = {"level": 1, "status": "🟡 中度級", "tag": "發熱與代償性心跳加速", "advice": "身體正在發炎導致心跳加快，請多喝水觀察。"}
            else:
                res = {"level": 0, "status": "🟢 輕度級", "tag": "輕微體溫升高", "advice": "體溫略高，若有服用退燒藥請持續觀察。"}
        elif delta_temp <= -1.0:
            res = {"level": 2, "status": "🔴 高危險", "tag": "體溫過低", "advice": "體溫異常偏低，可能有休克風險，請立即保暖並確認狀態！"}
        else:
            if delta_spo2 <= -3.0:
                res = {"level": 2, "status": "🔴 高危險", "tag": "隱形缺氧警報", "advice": "體溫正常但血氧顯著下降，請深呼吸並準備就醫。"}
            elif bpm_increase_ratio >= 0.25:
                adv = f"近期服用【{recent_drug}】易產生心悸副作用，請坐下休息。" if recent_drug and any(k in recent_indication for k in ["氣喘", "感冒"]) else "心跳異常高於基礎值！請確認是否為藥物副作用。"
                res = {"level": 1, "status": "🟡 需注意", "tag": "異常心搏過速", "advice": adv}
            elif bpm_increase_ratio <= -0.20:
                adv = f"近期服用【{recent_drug}】藥效顯著，若頭暈請立即坐下。" if recent_drug and any(k in recent_indication for k in ["高血壓", "心"]) else "心跳比平時慢很多，若有吃降血壓藥請注意是否頭暈。"
                res = {"level": 1, "status": "🟡 需注意", "tag": "異常心搏過緩", "advice": adv}

        res["delta_str"] = delta_str
        return res

# ==========================================
# ⚙️ 模組三：硬體感測與訊號處理背景執行緒
# ==========================================
def butter_bandpass_filter(data, lowcut, highcut, fs, order=3):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut/nyq, highcut/nyq], btype='band')
    return filtfilt(b, a, data)

def calculate_vitals_fft(ir_data, red_data, fs=20):
    ir_arr, red_arr = np.array(ir_data), np.array(red_data)
    ir_dc, red_dc = np.mean(ir_arr), np.mean(red_arr)
    if ir_dc == 0 or red_dc == 0: return 0, 0, False
    try:
        ir_filtered = butter_bandpass_filter(ir_arr / ir_dc, 0.5, 3.0, fs)
        red_filtered = butter_bandpass_filter(red_arr / red_dc, 0.5, 3.0, fs)
    except ValueError: return 0, 0, False
    ir_ac = np.sqrt(np.mean(ir_filtered**2))
    red_ac = np.sqrt(np.mean(red_filtered**2))
    is_valid = 0.1 <= (ir_ac * 100) <= 5.0
    spo2 = min(100.0, max(85.0, 105 - (17 * (red_ac / ir_ac)))) if is_valid and ir_ac > 0 else 0
    fft_res = np.abs(np.fft.rfft(ir_filtered, n=1024))
    freqs = np.fft.rfftfreq(1024, d=1/fs)
    valid_idx = np.where((freqs >= 0.67) & (freqs <= 3.0))[0]
    bpm = freqs[valid_idx][np.argmax(fft_res[valid_idx])] * 60 if len(valid_idx) > 0 else 0
    return round(bpm, 1), round(spo2, 1), is_valid

class HardwareThread(threading.Thread):
    def __init__(self, ai_engine):
        super().__init__(daemon=True)
        global HARDWARE_AVAILABLE
        self.ai = ai_engine
        self.bus = None
        self.leds = []
        self.MAX_ADDR = 0x57
        self.MLX_ADDR = 0x5A
        self.FS = 20
        self.BUFFER_SIZE = 160 # 8秒鐘的光譜緩衝區
        
        try:
            from gpiozero import LED
            # 🌟 換上你專屬測試成功的 12 顆 LED 真實腳位
            LED_PINS = [4, 5, 6, 12, 16, 18, 19, 20, 21, 22, 25, 26]
            self.leds = [LED(pin) for pin in LED_PINS]
            print(f"✅ 實體 {len(self.leds)} 顆 LED 模組初始化成功！")
        except Exception as e:
            print("⚠️ LED 模組初始化失敗:", e)
            
        if HARDWARE_AVAILABLE:
            try:
                import smbus
                self.bus = smbus.SMBus(3)
                self.bus.write_byte_data(self.MAX_ADDR, 0x04, 0x00)
                self.bus.write_byte_data(self.MAX_ADDR, 0x09, 0x03)
                self.bus.write_byte_data(self.MAX_ADDR, 0x0A, 0x27)
                self.bus.write_byte_data(self.MAX_ADDR, 0x0C, 0x3F) 
                self.bus.write_byte_data(self.MAX_ADDR, 0x0D, 0x3F)
            except Exception as e:
                print("硬體連線失敗:", e)
                HARDWARE_AVAILABLE = False

    def update_led_schedule(self):
        """🌟 給 LED 掃描自己專屬的資料庫連線，防止執行緒鎖死"""
        if not self.leds: return
        now_str = datetime.now().strftime("%H:%M")
        try:
            conn = sqlite3.connect('smart_pillbox.db', timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("SELECT box_index FROM pill_schedules WHERE time_str <= ?", (now_str,))
            active_boxes = [row[0] for row in cursor.fetchall()]
            conn.close()

            if not hasattr(self, "last_active"): self.last_active = []
            if self.last_active != active_boxes:
                print(f"\n💡 [硬體連動] 偵測到排程！目前應該亮起的藥格內部陣列索引為: {active_boxes}")
                self.last_active = active_boxes

            for i, led in enumerate(self.leds):
                if i in active_boxes: led.on()
                else: led.off()
        except Exception as e:
            pass 

    def get_temperature(self):
        if not HARDWARE_AVAILABLE: return 36.5 
        try:
            amb_data = self.bus.read_word_data(self.MLX_ADDR, 0x06)
            t_amb = (amb_data * 0.02) - 273.15
            obj_data = self.bus.read_word_data(self.MLX_ADDR, 0x07)
            t_skin = (obj_data * 0.02) - 273.15
            if 10.0 < t_skin < 50.0:
                # 🌟 動態體溫修正：貼太近就調降補償常數
                base_comp = 1.2 if t_skin > 34.5 else 2.2
                return round(t_skin + 0.1 * (t_skin - t_amb) + base_comp, 1)
        except: pass
        return 0.0

    def get_optical_data(self):
        if not HARDWARE_AVAILABLE:
            time.sleep(1/self.FS)
            return 80000 + np.random.randint(-1000, 1000), 80000 + np.random.randint(-1000, 1000)
        try:
            data = self.bus.read_i2c_block_data(self.MAX_ADDR, 0x07, 6)
            red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
            ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
            return ir, red
        except: return 0, 0

    def run(self):
        ir_buffer, red_buffer, temp_buffer = [], [], []
        recent_bpms, recent_spo2s = [], []
        last_led_check = 0   
        last_db_write = 0
        calc_counter = 0
        
        while True:
            now_sec = time.time()
            # 🌟 每 1 秒鐘查一次資料庫，讓燈號「零延遲」反應
            if now_sec - last_led_check >= 1.0:
                self.update_led_schedule()
                last_led_check = now_sec

            ir, red = self.get_optical_data()
            finger_present = ir > 50000

            if not finger_present:
                if shared_state["sensor_status"] != "IDLE":
                    shared_state["sensor_status"] = "IDLE"
                    shared_state["progress"] = 0
                    shared_state["bpm"], shared_state["spo2"], shared_state["temp"] = 0, 0, 0
                    shared_state["ai_status"] = "機器待命中..."
                    shared_state["ai_tag"] = ""
                    shared_state["ai_advice"] = "請將手指輕壓於感測器上方紅光處，並保持靜止約 8 秒鐘。"
                    shared_state["ai_color"] = TEXT_SUB
                    ir_buffer.clear()
                    red_buffer.clear()
                    temp_buffer.clear()
                    recent_bpms.clear()
                    recent_spo2s.clear()
            else:
                if shared_state["sensor_status"] == "IDLE":
                    shared_state["sensor_status"] = "MEASURING"
                    shared_state["progress"] = 0
                    calc_counter = 0
                    
                if shared_state["sensor_status"] in ["MEASURING", "CALIBRATING", "CONTINUOUS"]:
                    ir_buffer.append(ir)
                    red_buffer.append(red)
                    t = self.get_temperature()
                    if t > 30: temp_buffer.append(t)
                    
                    if len(ir_buffer) > self.BUFFER_SIZE:
                        ir_buffer.pop(0)
                        red_buffer.pop(0)
                    if len(temp_buffer) > self.BUFFER_SIZE:
                        temp_buffer.pop(0)
                        
                    if shared_state["sensor_status"] == "MEASURING":
                        shared_state["progress"] = int((len(ir_buffer) / self.BUFFER_SIZE) * 100)
                    
                    if len(ir_buffer) == self.BUFFER_SIZE:
                        calc_counter += 1
                        if calc_counter >= 20: # 每收集 1 秒計算一次
                            calc_counter = 0
                            bpm, spo2, is_valid = calculate_vitals_fft(ir_buffer, red_buffer, self.FS)
                            final_temp = statistics.median(temp_buffer) if temp_buffer else 36.5
                            
                            if is_valid and 40 < bpm < 160:
                                recent_bpms.append(bpm)
                                recent_spo2s.append(spo2)
                                if len(recent_bpms) > 4: 
                                    recent_bpms.pop(0)
                                    recent_spo2s.pop(0)
                                
                                if len(recent_bpms) == 4:
                                    bpm_variance = max(recent_bpms) - min(recent_bpms)
                                    spo2_variance = max(recent_spo2s) - min(recent_spo2s)
                                    
                                    # 防幽靈數據穩定校正
                                    if bpm_variance <= 8.0 and spo2_variance <= 3.0:
                                        shared_state["sensor_status"] = "CONTINUOUS"
                                        stable_bpm = round(statistics.median(recent_bpms), 1)
                                        stable_spo2 = round(statistics.median(recent_spo2s), 1)
                                        
                                        shared_state["bpm"] = stable_bpm
                                        shared_state["spo2"] = stable_spo2
                                        shared_state["temp"] = final_temp
                                        
                                        now_sec = time.time()
                                        if now_sec - last_db_write > 5.0: # AI防洗頻: 每5秒才寫入一次
                                            last_db_write = now_sec
                                            try:
                                                conn = sqlite3.connect('smart_pillbox.db', timeout=3.0)
                                                cursor = conn.cursor()
                                                cursor.execute('INSERT INTO vitals_log (bpm, spo2, temperature) VALUES (?, ?, ?)', (stable_bpm, stable_spo2, final_temp))
                                                vitals_id = cursor.lastrowid
                                                
                                                ai_res = self.ai.evaluate(stable_bpm, stable_spo2, final_temp)
                                                shared_state["ai_status"] = ai_res["status"]
                                                shared_state["ai_tag"] = ai_res["tag"]
                                                shared_state["ai_advice"] = ai_res["advice"]
                                                
                                                if ai_res["level"] == 2: shared_state["ai_color"] = ACCENT_RED
                                                elif ai_res["level"] == 1: shared_state["ai_color"] = ACCENT_YELLOW
                                                else: shared_state["ai_color"] = ACCENT_GREEN
                                                
                                                cursor.execute('''INSERT INTO biomarker_analysis (vitals_id, symptom_tags, baseline_delta, risk_level, ai_advice) VALUES (?, ?, ?, ?, ?)''', (vitals_id, ai_res["tag"], ai_res["delta_str"], ai_res["level"], ai_res["advice"]))
                                                conn.commit()
                                                conn.close()
                                            except Exception as e:
                                                pass
                                    else:
                                        shared_state["sensor_status"] = "CALIBRATING"
                                        shared_state["bpm"] = round(statistics.mean(recent_bpms), 1)
                                        shared_state["spo2"] = round(statistics.mean(recent_spo2s), 1)
                                        shared_state["temp"] = final_temp
                                        shared_state["ai_status"] = "數值跳動中..."
                                        shared_state["ai_tag"] = "防晃動校正中"
                                        shared_state["ai_advice"] = "偵測到些微干擾。系統正在進行多重取樣交叉比對，請勿移動手指，等待數值鎖定。"
                                        shared_state["ai_color"] = "#A855F7"
                                else:
                                    shared_state["sensor_status"] = "CALIBRATING"
                            else:
                                # 雜訊過大，退回一半進度繼續算，而不是完全失敗
                                ir_buffer = ir_buffer[80:]
                                red_buffer = red_buffer[80:]
                                temp_buffer = temp_buffer[len(temp_buffer)//2:]
                                recent_bpms.clear()
                                recent_spo2s.clear()
                                shared_state["sensor_status"] = "MEASURING"
                                shared_state["progress"] = 50
                                
            time.sleep(1/self.FS)

# ==========================================
# ⚙️ 模組四：前端 GUI (醫療級觸控介面)
# ==========================================
class SmartPillboxApp:
    def __init__(self, root):
        self.root = root
        self.root.title("智慧醫療藥盒 - 整合大腦")
        self.root.geometry("800x480")
        self.root.configure(bg=BG_DARK)
        self.db_path = "smart_pillbox.db"
        self.search_timer = None
        self.active_entry = None
        self.supervisor = AgentSupervisor()
        
        self.setup_styles()
        
        self.main_page = tk.Frame(self.root, bg=BG_DARK)
        self.schedule_page = tk.Frame(self.root, bg=BG_DARK) 

        self.build_main_page()
        self.build_schedule_page()

        self.main_page.pack(fill="both", expand=True)
        self.update_clock()
        self.refresh_upcoming_schedule()
        self.poll_hardware_state()

    def setup_styles(self):
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

    def set_active_entry(self, entry_widget):
        self.active_entry = entry_widget
        self.show_keyboard()

    def open_ai_assistant_dialog(self):
        prompt = simpledialog.askstring("🤖 Llama AI 醫療語音/對話", "請輸入指令 (例如: 設定 08:30 第 1 格吃降血壓藥 / 查詢生理指標 / 查藥 阿斯匹靈):")
        if prompt and prompt.strip():
            reply = self.supervisor.process_user_message(prompt.strip())
            self.refresh_upcoming_schedule()
            messagebox.showinfo("🤖 AI 回應", reply)

    # ---------------- 畫面一：主監控儀表板 (緊湊化設計) ----------------
    def build_main_page(self):
        # 💡 先把底部按鈕釘死在最下面，防止被擠出畫面
        btn_frame = tk.Frame(self.main_page, bg=BG_DARK)
        btn_frame.pack(side="bottom", fill="x", pady=10, padx=15)
        
        btn_ai = ttk.Button(btn_frame, text="🤖 AI 助手", style="Primary.TButton", command=self.open_ai_assistant_dialog)
        btn_ai.pack(side="left", expand=True, fill="x", padx=(0, 2))
        btn_setting = ttk.Button(btn_frame, text="➕ 管理排程", style="Primary.TButton", command=self.switch_to_schedule)
        btn_setting.pack(side="left", expand=True, fill="x", padx=(2, 2))
        btn_take = ttk.Button(btn_frame, text="✔ 確認拿藥", style="Success.TButton", command=self.confirm_medication)
        btn_take.pack(side="right", expand=True, fill="x", padx=(2, 0))

        # 頂部時間區塊
        header_frame = tk.Frame(self.main_page, bg=BG_DARK)
        header_frame.pack(side="top", fill="x", padx=15, pady=(5, 0))
        
        self.greeting_lbl = tk.Label(header_frame, text="☀️ 早安，長輩", font=("微軟正黑體", 16, "bold"), bg=BG_DARK, fg=ACCENT_BLUE)
        self.greeting_lbl.pack(side="left")
        self.clock_lbl = tk.Label(header_frame, text="00:00:00", font=("微軟正黑體", 32, "bold"), bg=BG_DARK, fg=TEXT_MAIN)
        self.clock_lbl.pack(side="right")

        # 今日排程清單 (放在按鈕上方，高度縮為 2 行)
        self.upcoming_listbox = tk.Listbox(self.main_page, font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_MAIN, bd=0, height=2, highlightthickness=0)
        self.upcoming_listbox.pack(side="bottom", fill="x", padx=15, pady=(0, 5))

        # 中間儀表板區塊 (填滿剩下空間)
        dashboard_frame = tk.Frame(self.main_page, bg=BG_DARK)
        dashboard_frame.pack(side="top", fill="both", expand=True, padx=15, pady=5)
        
        # 左側：生理數據
        vitals_panel = tk.Frame(dashboard_frame, bg=BG_PANEL, bd=1, highlightbackground="#334155", highlightthickness=1)
        vitals_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        tk.Label(vitals_panel, text="即時生理特徵", font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_SUB).pack(pady=(5, 0))
        
        # 💡 字體縮小為 24，節省垂直空間給進度條
        self.bpm_lbl = tk.Label(vitals_panel, text="-- BPM", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.bpm_lbl.pack(pady=0)
        self.spo2_lbl = tk.Label(vitals_panel, text="-- %", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.spo2_lbl.pack(pady=0)
        self.temp_lbl = tk.Label(vitals_panel, text="-- °C", font=("微軟正黑體", 24, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.temp_lbl.pack(pady=0)

        # 🌟 高科技發光進度條 (常駐外框藍線)
        self.prog_canvas = tk.Canvas(vitals_panel, width=280, height=16, bg=BG_PANEL, highlightthickness=1, highlightbackground=ACCENT_BLUE)
        self.prog_canvas.pack(pady=(5, 2))
        self.prog_status_lbl = tk.Label(vitals_panel, text="等待手指置入...", font=("微軟正黑體", 10), bg=BG_PANEL, fg=TEXT_SUB)
        self.prog_status_lbl.pack(pady=(0, 5))

        # 右側：AI 診斷
        ai_panel = tk.Frame(dashboard_frame, bg=BG_PANEL, bd=1, highlightbackground="#334155", highlightthickness=1)
        ai_panel.pack(side="right", fill="both", expand=True, padx=(5, 0))
        
        tk.Label(ai_panel, text="🧠 AI 決策樹護理師", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg="#D946EF").pack(pady=(5, 5))
        self.ai_status_lbl = tk.Label(ai_panel, text="機器待命中...", font=("微軟正黑體", 16, "bold"), bg=BG_PANEL, fg=TEXT_SUB)
        self.ai_status_lbl.pack(pady=(5, 5))
        self.ai_tag_lbl = tk.Label(ai_panel, text="", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN)
        self.ai_tag_lbl.pack()
        self.ai_advice_lbl = tk.Label(ai_panel, text="請將手指輕壓於感測器上方紅光處，\n並保持靜止約 8 秒鐘。", font=("微軟正黑體", 12), bg=BG_PANEL, fg=TEXT_MAIN, wraplength=300, justify="center")
        self.ai_advice_lbl.pack(pady=5, fill="x", padx=10)

    def draw_hightech_bar(self, percentage, status):
        """🌟 繪製高科技長條實心能量條"""
        self.prog_canvas.delete("bar") 
        if status == "CONTINUOUS": color = ACCENT_GREEN
        elif status == "CALIBRATING": color = "#A855F7"
        elif status == "MEASURING" and percentage == 50: color = ACCENT_YELLOW
        else: color = ACCENT_BLUE 
        
        bar_width = int((percentage / 100) * 278)
        if bar_width > 0:
            self.prog_canvas.create_rectangle(1, 1, 1 + bar_width, 15, fill=color, outline="", tags="bar")

    def poll_hardware_state(self):
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
        self.ai_tag_lbl.config(text=f"【{shared_state['ai_tag']}】" if shared_state['ai_tag'] else "")
        self.ai_advice_lbl.config(text=shared_state["ai_advice"])

        self.root.after(100, self.poll_hardware_state)

    def update_clock(self):
        now = datetime.now()
        self.clock_lbl.config(text=now.strftime("%H:%M:%S"))
        hour = now.hour
        if 5 <= hour < 12: greeting = "🌅 早安，長輩"
        elif 12 <= hour < 18: greeting = "☀️ 午安，長輩"
        else: greeting = "🌙 晚安，長輩"
        self.greeting_lbl.config(text=greeting)
        self.root.after(1000, self.update_clock)

    def refresh_upcoming_schedule(self):
        self.upcoming_listbox.delete(0, tk.END)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT box_index, time_str, disease_name FROM pill_schedules ORDER BY time_str ASC')
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                self.upcoming_listbox.insert(tk.END, "🎉 太棒了！今日目前沒有任何待服用的排程。")
                self.upcoming_listbox.itemconfig(0, {'fg': TEXT_SUB})
            else:
                for row in rows:
                    box_idx, t_str, d_name = row
                    display_text = f"⏰ {t_str} | 💊 第 {box_idx+1:02d} 格 | 🩺 {d_name}"
                    self.upcoming_listbox.insert(tk.END, display_text)
                    if t_str <= datetime.now().strftime("%H:%M"):
                        self.upcoming_listbox.itemconfig(tk.END, {'fg': ACCENT_RED})
        except Exception:
            pass

    # ---------------- 畫面二：全維度搜尋與排程頁面 ----------------
    def build_schedule_page(self):
        self.ctrl_panel = tk.Frame(self.schedule_page, bg=BG_PANEL, bd=0)
        self.ctrl_panel.pack(side="top", fill="x", padx=10, pady=(5, 2), ipady=2)

        tk.Label(self.ctrl_panel, text="格:", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN).pack(side="left", padx=(5, 2))
        self.box_var = tk.StringVar(value="01")
        # 🌟 擴充至 12 格
        box_spin = tk.Spinbox(self.ctrl_panel, from_=1, to=12, textvariable=self.box_var, font=("微軟正黑體", 12, "bold"), width=3, format="%02.0f", justify="center", bg=BG_DARK, fg=TEXT_MAIN, buttonbackground=BG_PANEL)
        box_spin.pack(side="left", padx=2)

        tk.Label(self.ctrl_panel, text="時間:", font=("微軟正黑體", 12, "bold"), bg=BG_PANEL, fg=TEXT_MAIN).pack(side="left", padx=(10, 2))
        self.time_var = tk.StringVar(value=datetime.now().strftime("%H:%M"))
        self.time_entry = tk.Entry(self.ctrl_panel, textvariable=self.time_var, font=("微軟正黑體", 12, "bold"), justify="center", width=6, bg=BG_DARK, fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
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

        # 沉浸式鍵盤 (固定高度防擠壓)
        self.kb_frame = tk.Frame(self.schedule_page, bg=BG_DARK, height=220)
        self.kb_frame.pack_propagate(False) 
        self.kb_frame.pack(side="bottom", fill="both", padx=10, pady=2)
        self.build_virtual_keyboard()

        self.result_listbox = tk.Listbox(self.schedule_page, font=("微軟正黑體", 14), bg=BG_PANEL, fg=TEXT_MAIN, selectbackground=ACCENT_BLUE, bd=0, highlightthickness=1, highlightbackground="#334155")
        self.result_listbox.pack(side="top", fill="both", expand=True, padx=10, pady=2)
        self.result_listbox.bind("<<ListboxSelect>>", self.on_med_select)

    def hide_keyboard(self):
        self.kb_frame.pack_forget()

    def show_keyboard(self):
        if not self.kb_frame.winfo_ismapped():
            self.result_listbox.pack_forget()
            self.kb_frame.pack(side="bottom", fill="both", padx=10, pady=2)
            self.result_listbox.pack(side="top", fill="both", expand=True, padx=10, pady=2)

    def build_virtual_keyboard(self):
        grid_frame = tk.Frame(self.kb_frame, bg=BG_DARK)
        grid_frame.pack(fill="both", expand=True) 
        rows = [
            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
            ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
            ["A", "S", "D", "F", "G", "H", "J", "K", "L", ":"],  
            ["Z", "X", "C", "V", "B", "N", "M", "⌫", " ", "清空"]
        ]
        for col in range(10): grid_frame.columnconfigure(col, weight=1)
        for row in range(4): grid_frame.rowconfigure(row, weight=1)

        for r, row_keys in enumerate(rows):
            for c, key in enumerate(row_keys):
                btn_bg = "#E14470" if key in ["⌫", "清空"] else BG_PANEL
                btn = tk.Button(grid_frame, text=key, font=("微軟正黑體", 12, "bold"), bg=btn_bg, fg=TEXT_MAIN, activebackground=ACCENT_BLUE, bd=0, command=lambda k=key: self.press_virtual_key(k))
                btn.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

    def press_virtual_key(self, key):
        if not self.active_entry: self.active_entry = self.med_entry
        if key == "⌫":
            try:
                idx = self.active_entry.index(tk.INSERT)
                if idx > 0: self.active_entry.delete(idx - 1)
            except: pass
        elif key == "清空":
            self.active_entry.delete(0, tk.END)
        else:
            self.active_entry.insert(tk.INSERT, key)
        if self.active_entry == self.med_entry:
            self.debounce_search(self.med_entry.get())

    def switch_to_schedule(self):
        self.main_page.pack_forget()
        self.schedule_page.pack(fill="both", expand=True)
        self.time_var.set(datetime.now().strftime("%H:%M"))
        self.med_var.set("")
        self.result_listbox.delete(0, tk.END)
        self.active_entry = self.med_entry
        self.med_entry.focus_set()
        self.show_keyboard()

    def switch_to_main(self):
        self.schedule_page.pack_forget()
        self.refresh_upcoming_schedule()
        self.main_page.pack(fill="both", expand=True)

    def on_med_select(self, event):
        selection = self.result_listbox.curselection()
        if selection:
            selected_med = self.result_listbox.get(selection[0])
            if "找不到" not in selected_med:
                self.med_var.set(selected_med)

    def debounce_search(self, keyword):
        if self.search_timer is not None:
            self.root.after_cancel(self.search_timer)
        self.search_timer = self.root.after(300, self.perform_search, keyword)

    def perform_search(self, keyword):
        self.result_listbox.delete(0, tk.END)
        if len(keyword) < 1: return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(nhi_drug_database)")
            columns = [col[1] for col in cursor.fetchall()]
            
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
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            blacklist = ["軟膏", "眼藥水", "注射", "栓劑", "點眼", "外用", "凝膠", "貼布", "膠布", "噴鼻", "滴劑", "乳膏", "洗劑", "噴霧", "塞劑", "外洗", "輸注", "點鼻", "滴耳"]
            unique_results = []
            
            for row in rows:
                row_dict = dict(zip(columns, row))
                name = row_dict.get("中文品名") or row_dict.get("drug_name") or row_dict.get("英文品名", "未知藥品")
                dosage = row_dict.get("劑量") or row_dict.get("dosage") or ""
                license_id = row_dict.get("許可證字號") or row_dict.get("證字號") or row_dict.get("license_id") or ""
                
                if not license_id:
                    lic_match = re.search(r'([衛內]署藥[製輸]字第\d+號|[A-Z]{2}\d{6,})', str(name))
                    if lic_match:
                        license_id = lic_match.group(1)
                        name = name.replace(license_id, "").strip() 

                display_str = f"[{name}]"
                if dosage: display_str += f" [{dosage}]"
                if license_id: display_str += f" (證號: {license_id})"
                
                if any(bad in display_str for bad in blacklist): continue
                if display_str not in unique_results:
                    unique_results.append(display_str)
                    if len(unique_results) >= 15: break

            if unique_results:
                for r in unique_results: self.result_listbox.insert(tk.END, r)
            else:
                self.result_listbox.insert(tk.END, "(找不到符合的口服藥品)")
        except Exception as e:
            print(f"資料庫搜尋錯誤: {e}")

    def save_schedule(self):
        try: box_id = int(self.box_var.get()) - 1 
        except ValueError:
            messagebox.showwarning("錯誤", "藥格必須是數字！")
            return
        target_time = self.time_var.get()
        med_name = self.med_var.get()
        if not med_name or "找不到" in med_name:
            messagebox.showwarning("錯誤", "請輸入並選擇正確的藥品！")
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO pill_schedules (box_index, time_str, disease_name) VALUES (?, ?, ?)", (box_id, target_time, med_name))
            conn.commit()
            conn.close()
            self.switch_to_main() 
        except Exception as e:
            messagebox.showerror("錯誤", f"資料庫錯誤: {e}")

    def confirm_medication(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%H:%M")
            cursor.execute('SELECT id, box_index, time_str, disease_name FROM pill_schedules WHERE time_str <= ?', (now_str,))
            rows = cursor.fetchall()
            
            if not rows:
                messagebox.showinfo("提示", "目前沒有任何待服用的藥物喔！")
                conn.close()
                return
                
            taken_list = []
            for row in rows:
                sched_id, box_idx, t_str, d_name = row
                taken_list.append(f"{d_name} (第 {box_idx+1} 格)")
                cursor.execute("INSERT INTO medication_history (box_index, scheduled_time, disease_name, status) VALUES (?, ?, ?, 'TAKEN')", (box_idx, t_str, d_name))
                cursor.execute('DELETE FROM pill_schedules WHERE id = ?', (sched_id,))
                
            conn.commit()
            conn.close()
            self.refresh_upcoming_schedule() 
            msg = "\n".join(taken_list)
            messagebox.showinfo("✅ 拿藥確認成功", f"已確認服用以下藥物：\n{msg}\n\n系統已紀錄，硬體 LED 將於下一秒熄滅！")
        except Exception as e:
            messagebox.showerror("錯誤", f"資料庫操作失敗: {e}")

# ==========================================
# 🚀 系統啟動進入點
# ==========================================
if __name__ == "__main__":
    # Compatibility entry point. New startup is fully node based.
    from run_pillbox import main
    main()
