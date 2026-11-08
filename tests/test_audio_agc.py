import numpy as np

from core.audio_engine import SharedAudio
from core.config import DEFAULTS

SR = 48000
STEP = SR // 60


def _peak_after(amp, agc_max, seconds=12.0):
    cfg = dict(DEFAULTS)
    cfg["auto_gain_max"] = agc_max
    eng = SharedAudio(lambda: cfg, 64)
    t = np.arange(int(seconds * SR)) / SR
    sig = amp * (np.sin(2 * np.pi * 70 * t) + 0.5 * np.sin(2 * np.pi * 440 * t))
    sig = sig.astype(np.float32)
    for i in range(0, len(sig) - STEP, STEP):
        eng.feed(sig[i:i + STEP], SR)
    spec, _w, level, _b, has = eng.snapshot()
    return float(spec.max()), float(level), bool(has), eng.agc_gain


def test_quiet_music_is_lifted_by_auto_gain():
    off = _peak_after(0.004, 1.0)
    on = _peak_after(0.004, 8.0)
    assert on[0] > off[0] * 3.0          # спектр заметно выше
    assert on[1] > off[1] * 3.0          # и уровень для подсветки тоже
    assert on[3] > 3.0


def test_loud_music_is_not_amplified():
    on = _peak_after(0.5, 8.0)
    assert on[3] < 1.5                   # громкое не раздувается


def test_gain_stays_put_in_silence():
    cfg = dict(DEFAULTS)
    eng = SharedAudio(lambda: cfg, 64)
    for _ in range(120):
        eng.feed(np.zeros(STEP, np.float32), SR)
    assert eng.agc_gain == 1.0           # тишина не раздувает множитель
