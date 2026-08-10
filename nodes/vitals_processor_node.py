"""Pure signal-processing functions; no hardware or database dependency."""
import numpy as np
from scipy.signal import butter, filtfilt


def butter_bandpass_filter(data, lowcut: float, highcut: float, fs: float, order: int = 3):
    nyquist = 0.5 * fs
    b, a = butter(order, [lowcut / nyquist, highcut / nyquist], btype="band")
    return filtfilt(b, a, data)


def calculate_vitals_fft(ir_data, red_data, fs: int = 20) -> tuple[float, float, bool]:
    ir_arr, red_arr = np.array(ir_data), np.array(red_data)
    ir_dc, red_dc = np.mean(ir_arr), np.mean(red_arr)
    if ir_dc == 0 or red_dc == 0:
        return 0.0, 0.0, False
    try:
        ir_filtered = butter_bandpass_filter(ir_arr / ir_dc, 0.5, 3.0, fs)
        red_filtered = butter_bandpass_filter(red_arr / red_dc, 0.5, 3.0, fs)
    except ValueError:
        return 0.0, 0.0, False
    ir_ac = np.sqrt(np.mean(ir_filtered ** 2))
    red_ac = np.sqrt(np.mean(red_filtered ** 2))
    is_valid = 0.1 <= ir_ac * 100 <= 5.0
    spo2 = min(100.0, max(85.0, 105 - 17 * (red_ac / ir_ac))) if is_valid and ir_ac else 0.0
    fft_res = np.abs(np.fft.rfft(ir_filtered, n=1024))
    freqs = np.fft.rfftfreq(1024, d=1 / fs)
    valid_idx = np.where((freqs >= 0.67) & (freqs <= 3.0))[0]
    bpm = freqs[valid_idx][np.argmax(fft_res[valid_idx])] * 60 if len(valid_idx) else 0.0
    return round(float(bpm), 1), round(float(spo2), 1), bool(is_valid)
