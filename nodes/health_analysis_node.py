"""Rule-based health analysis. This node is authoritative, not an LLM."""
from pillbox_database import connect


class AITreeAnalyzer:
    def __init__(self, base_bpm: float = 75.0, base_spo2: float = 98.0, base_temp: float = 36.2):
        self.base_bpm, self.base_spo2, self.base_temp = base_bpm, base_spo2, base_temp

    def _recent_medication_context(self) -> tuple[str | None, str]:
        with connect() as conn:
            row = conn.execute("""SELECT disease_name FROM medication_history
                WHERE taken_time >= datetime('now', '-4 hours')
                ORDER BY taken_time DESC LIMIT 1""").fetchone()
            drug = row[0] if row else None
            if not drug:
                return None, "未知適應症"
            name = drug.split("]")[0].replace("[", "").strip().split("(")[0].strip()
            indication = conn.execute(
                "SELECT indication FROM nhi_drug_database WHERE drug_name LIKE ? LIMIT 1", (f"%{name}%",)
            ).fetchone()
            return drug, indication[0] if indication else "未知適應症"

    def evaluate(self, current_bpm: float, current_spo2: float, current_temp: float) -> dict:
        recent_drug, recent_indication = self._recent_medication_context()
        delta_temp, delta_spo2 = current_temp - self.base_temp, current_spo2 - self.base_spo2
        bpm_ratio = (current_bpm - self.base_bpm) / self.base_bpm
        result = {"level": 0, "status": "✅ 狀況極佳", "tag": "生理指標平穩", "advice": "各項指標皆與平時的健康狀態一致，藥效平穩，保持得非常好！"}
        if delta_temp >= 1.0 and delta_spo2 <= -3.0:
            result = {"level": 2, "status": "🔴 高危險", "tag": "發熱伴隨血氧下降", "advice": "體溫異常且血氧低於平時水準，請立刻就醫！"}
        elif delta_temp >= 1.0 and bpm_ratio >= .2:
            result = {"level": 1, "status": "🟡 中度級", "tag": "發熱與代償性心跳加速", "advice": "身體正在發炎導致心跳加快，請多喝水觀察。"}
        elif delta_temp <= -1.0:
            result = {"level": 2, "status": "🔴 高危險", "tag": "體溫過低", "advice": "體溫異常偏低，請立即保暖並確認狀態！"}
        elif delta_spo2 <= -3.0:
            result = {"level": 2, "status": "🔴 高危險", "tag": "隱形缺氧警報", "advice": "血氧顯著下降，請深呼吸並準備就醫。"}
        elif bpm_ratio >= .25:
            advice = f"近期服用【{recent_drug}】可能與心悸相關，請坐下休息。" if recent_drug and any(x in recent_indication for x in ("氣喘", "感冒")) else "心跳異常高於基礎值！請確認是否為藥物副作用。"
            result = {"level": 1, "status": "🟡 需注意", "tag": "異常心搏過速", "advice": advice}
        elif bpm_ratio <= -.20:
            result = {"level": 1, "status": "🟡 需注意", "tag": "異常心搏過緩", "advice": "心跳比平時慢很多，若有頭暈請立即坐下。"}
        result["delta_str"] = f"BPM: {current_bpm - self.base_bpm:+.1f} ({bpm_ratio * 100:+.1f}%), SpO2: {delta_spo2:+.1f}%, Temp: {delta_temp:+.1f}°C"
        return result
