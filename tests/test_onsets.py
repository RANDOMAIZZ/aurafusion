"""Детектор атак для искр: ловит удары и не путает их с тянущимся звуком.

Спектр строится настоящим расчётом движка (SharedAudio.feed) из
синтезированной волны, звуковая карта не нужна.
"""
import numpy as np

from core.audio_engine import SharedAudio
from core.config import DEFAULTS
from core.onsets import BandOnsets

SR = 48000
STEP = SR // 60


def _run(sig, skip=1.0):
    cfg = dict(DEFAULTS)
    eng = SharedAudio(lambda: cfg, 64)
    det = BandOnsets(64)
    out = []
    for i in range(0, len(sig) - STEP, STEP):
        eng.feed(sig[i:i + STEP], SR)
        spec, _w, _l, _b, has = eng.snapshot()
        _e, o = det.update(spec, 1.0 / 60, True) if has else det.silence(1.0 / 60)
        if i / SR >= skip:
            out.append(o.copy())
    return np.array(out)


def _kick(n=int(0.35 * SR)):
    t = np.arange(n) / SR
    f = 50.0 + 70.0 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def _t(sec):
    return np.arange(int(sec * SR)) / SR


def test_feed_updates_every_call():
    eng = SharedAudio(lambda: dict(DEFAULTS), 64)
    seq0 = eng.frame_seq
    for _ in range(5):
        eng.feed(np.zeros(STEP, np.float32), SR)
    assert eng.frame_seq == seq0 + 5


def test_every_kick_is_caught_in_low_bands():
    t = _t(6.0)
    y = np.zeros_like(t)
    k = _kick()
    hits = np.arange(1.5, 5.5, 0.5)
    for h in hits:
        i = int(h * SR)
        y[i:i + len(k)] += 0.8 * k[:len(y) - i]
    o = _run(y.astype(np.float32))
    lo = o[:, :10].max(axis=1)
    for h in hits:
        f = int(round((h - 1.0) * 60))
        # удар виден в первые 2 кадра (до 33 мс)
        assert lo[f:f + 2].max() > 0.5, "удар на %.1f с не пойман" % h
    # и только в нижней части спектра
    assert o[:, 32:].max() < 0.3


def test_sustained_sounds_do_not_fire():
    t = _t(6.0)
    for sig in (0.3 * np.sin(2 * np.pi * 440 * t + 3 * np.sin(2 * np.pi * 6 * t)),
                0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t)),
                0.3 * np.sin(2 * np.pi * (100 * t + 1900 / 12 * t * t))):
        o = _run(sig.astype(np.float32))
        assert (o.max(axis=1) > 0.3).sum() <= 6


def test_steady_noise_is_quiet():
    rng = np.random.default_rng(0)
    o = _run((0.15 * rng.standard_normal(int(6 * SR))).astype(np.float32))
    assert o.sum() / 5.0 < 3.0          # меньше 3 единиц атаки в секунду


def test_loud_hits_stronger_than_quiet():
    det = BandOnsets(8)
    det.update(np.zeros(8), 1 / 60)
    for _ in range(30):
        det.update(np.zeros(8), 1 / 60)
    _e, quiet = det.update(np.full(8, 0.6), 1 / 60)
    for _ in range(30):
        det.update(np.zeros(8), 1 / 60)
    _e, loud = det.update(np.full(8, 2.4), 1 / 60)
    assert loud.mean() > quiet.mean() * 1.5


def test_stale_frame_has_no_onset():
    det = BandOnsets(8)
    det.update(np.zeros(8), 1 / 60)
    det.update(np.full(8, 2.0), 1 / 60)
    _e, o = det.update(np.full(8, 2.0), 1 / 60, fresh=False)
    assert not o.any()
