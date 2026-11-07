import numpy as np

from core.beat import BeatTracker


def _feed(bt, seconds, bpm, fps=60.0, n=32):
    period = 60.0 / bpm
    hits = 0
    for i in range(int(seconds * fps)):
        t = i / fps
        spec = np.zeros(n, np.float32)
        if (t % period) < 1.5 / fps:
            spec[: n // 2] = 2.0
        if bt.update(spec, dt=1.0 / fps):
            hits += 1
    return hits


def test_history_is_a_real_ring_buffer():
    # Раньше после заполнения буфера запись всегда шла в ячейку 0.
    bt = BeatTracker(fps=60)
    _feed(bt, 12, 120)                     # больше 8 с истории
    assert bt.n_hist == bt.maxlen
    assert np.count_nonzero(bt.hist) > 10  # удары разложены по всему кольцу


def test_finds_tempo_after_buffer_is_full():
    bt = BeatTracker(fps=60)
    _feed(bt, 20, 120)
    assert abs(bt.bpm - 120) < 3


def test_no_tempo_on_silence():
    bt = BeatTracker(fps=60)
    for _ in range(600):
        bt.update(np.zeros(32, np.float32), dt=1 / 60, has_sig=False)
    assert bt.bpm == 0.0
