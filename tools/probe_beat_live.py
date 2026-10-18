"""Проверка темпа внутри живого оверлея.

Синтетическая музыка с ровным ритмом подаётся в настоящий Overlay.tick(),
и мы смотрим, что детектор в оверлее выдаёт удары с нужной частотой.

Заодно сравниваем со старым способом — порогом по басу, — чтобы было видно,
что изменилось.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402
from core.config import DEFAULTS                     # noqa: E402
from ui.overlay import Overlay                       # noqa: E402
from shot_overlay import FakeSC                      # noqa: E402

FPS = 60.0
BARS = 64


class RythmTrack:
    """Спектр с ударами ровно на заданном темпе."""

    def __init__(self, bpm, bars=BARS, decay=0.045, peak=1.3, floor=0.12):
        self.bpm, self.bars, self.decay, self.peak, self.floor = (
            bpm, bars, decay, peak, floor)
        self.t = 0.0

    def snapshot(self):
        self.t += 1.0 / FPS
        period = 60.0 / self.bpm
        ph = (self.t % period) / period
        hit = np.exp(-ph * period / self.decay)
        x = np.linspace(0, 1, self.bars)
        spec = (self.floor + self.peak * hit) * np.exp(-2.0 * x)
        return (spec.astype(np.float32), np.zeros(512, np.float32), 0.6, 0.6, True)

    def set_bars(self, n):
        pass


def build(track, seconds=30.0, style="sparks"):
    cfg = dict(DEFAULTS)
    cfg.update({"bars": track.bars, "demo": True, "sys_fx": False,
                "overlay_fps": 60, "style": style, "viz_y": 0.92,
                "height_scale": 0.8, "line_count": 96})
    ov = Overlay(lambda: cfg, track, FakeSC())
    ov.resize(1280, 720)
    ov._hide_k = 1.0
    ov._last_eff_op = -1.0
    beat_times, old_times = [], []
    n = int(seconds * FPS)
    prev = None
    for f in range(n):
        ov.tick()
        t = f / FPS
        if t > 6.0 and ov._beat_fired:
            beat_times.append(t)
        # старый способ: порог по басу, как было до детектора темпа
        try:
            b = float(ov._bass)
            if t > 6.0 and prev is not None and b > prev * 1.10 + 0.006:
                old_times.append(t)
            prev = b
        except Exception:
            prev = None
    return ov, beat_times, old_times


def stats(times, want_period):
    if len(times) < 4:
        return None
    iv = np.diff(times)
    good = iv[(iv > want_period * 0.6) & (iv < want_period * 1.6)]
    if not len(good):
        return {"ударов": len(times), "в_окне": 0, "разброс": 999.0}
    return {
        "ударов": len(times),
        "в_окне": len(good),
        "разброс": float(np.std(good) / np.mean(good) * 100.0),
        "средний": float(np.mean(good)),
    }


def classify(times, period):
    """Разбирает интервалы: половина, ровный, удвоенный или мусор."""
    if len(times) < 3:
        return {}
    iv = np.diff(times)
    out = {"pol": 0, "takt": 0, "double": 0, "junk": 0}
    for x in iv:
        r = x / period
        if 0.42 < r < 0.58:
            out["pol"] += 1
        elif 0.85 < r < 1.15:
            out["takt"] += 1
        elif 1.75 < r < 2.25:
            out["double"] += 1
        else:
            out["junk"] += 1
    return out


def main():
    QApplication.instance() or QApplication([])
    # ASCII-метки: кириллица в консоли Windows превращается в мусор
    print("=== BPM inside live overlay ===")
    print("  want    detector (jit/hits/intervals)      old bass-threshold")
    bad = 0
    for bpm in (90, 120, 128, 160):
        want = 60.0 / bpm
        ov, bt, ot = build(RythmTrack(bpm), seconds=20.0)
        a = stats(bt, want)
        b = stats(ot, want)
        cls = classify(bt, want)
        fa = ("jit %5.1f%% hits %3d [half %d beat %d dbl %d junk %d]"
              % (a["разброс"], a["ударов"], cls.get("pol", 0), cls.get("takt", 0),
                 cls.get("double", 0), cls.get("junk", 0))) if a else "no beats"
        fb = ("hits %3d" % b["ударов"]) if b else "no beats"
        expect = 120 - 72
        ok = bool(a and a["разброс"] < 6.0 and a["ударов"] < expect * 1.35)
        if not ok:
            bad += 1
        print("  %4d    %-50s %-14s %s" % (bpm, fa, fb, "" if ok else "<-- BAD"))
        ov.close()

    print()
    print("=== no rhythm at all: white noise ===")
    rng = np.random.default_rng(3)
    n = 20 * FPS
    noise = (0.15 + 0.35 * rng.random((int(n), 64))).astype(np.float32)

    class Noise:
        def __init__(self, track):
            self.track = track
            self.bars = track.shape[1]
            self.i = 0

        def snapshot(self):
            self.i += 1
            return (self.track[self.i - 1], np.zeros(512, np.float32), 0.4, 0.4, True)

        def set_bars(self, _n):
            pass

    ov, bt, ot = build(Noise(noise), seconds=20.0)
    print("  detector    : %d beats / 20 s" % len(bt))
    print("  bass-thresh : %d beats / 20 s" % len(ot))
    if len(bt) > 6:
        bad += 1
        print("  <-- BAD: white noise must not look like rhythm")
    ov.close()
    print()
    print("RESULT:", "rhythm tracked cleanly" if bad == 0 else "bad cases: %d" % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())