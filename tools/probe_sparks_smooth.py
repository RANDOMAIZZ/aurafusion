"""Плавность искр: не пропадают ли они рывками.

Считаем число живых частиц в каждом кадре и смотрим на самый резкий обрыв
за секунду. Если часть фонтана исчезает одним кадром — это тот самый «затык»,
когда наверху висят точки и разом их нет.

Порог нормы: за кадр не может пропасть больше 12% живых частиц.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402
from core.config import DEFAULTS                     # noqa: E402
from ui.overlay import Overlay                       # noqa: E402
from shot_overlay import FakeSC                      # noqa: E402


class Beat:
    """Ритмичная музыка, чтобы было видно работу фонового потока."""

    def __init__(self, bars=64, fps=60.0, bpm=120.0):
        self.bars, self.fps, self.bpm = bars, fps, bpm
        self.t = 0.0

    def snapshot(self):
        self.t += 1.0 / self.fps
        per = 60.0 / self.bpm
        ph = (self.t % per) / per
        hit = np.exp(-ph * per / 0.05)
        x = np.linspace(0, 1, self.bars)
        spec = ((0.15 + 1.2 * hit) * np.exp(-2.0 * x)).astype(np.float32)
        return spec, np.zeros(512, np.float32), 0.6, 0.6, True

    def set_bars(self, n):
        pass


def run(style_over, seconds=12.0, label=""):
    cfg = dict(DEFAULTS)
    cfg.update({"bars": 64, "demo": True, "sys_fx": False, "overlay_fps": 60,
                "style": "sparks", "viz_y": 0.92, "height_scale": 0.83,
                "line_count": 96})
    cfg.update(style_over)
    ov = Overlay(lambda: cfg, Beat(), FakeSC())
    ov.resize(1280, 720)
    ov._hide_k = 1.0
    ov._last_eff_op = -1.0
    counts, alphas = [], []
    n = int(seconds * 60)
    for _ in range(n):
        ov.tick()
        st = ov._spark
        live = st["life"] > 0
        counts.append(int(live.sum()))
        if live.any():
            f = st["life"][live] / np.maximum(0.01, st["full"][live])
            alphas.append(float(np.mean(f ** 2)))
        time.sleep(1.0 / 60.0)
    ov.close()

    c = np.array(counts, dtype=float)
    # относительный обрыв: (было - стало) / было
    drops = []
    for a, b in zip(c[:-1], c[1:]):
        if a >= 20:
            drops.append(max(0.0, (a - b) / a))
    worst = float(max(drops)) if drops else 0.0
    print("  %-26s частиц %4d..%4d | худший обрыв за кадр %5.1f%% | %s"
          % (label, int(c.min()), int(c.max()), worst * 100,
             "плавно" if worst < 0.12 else "РЫВОК"))
    return worst < 0.12


def main():
    QApplication.instance() or QApplication([])
    print("=== smooth fade-out of sparks (worst single-frame drop) ===")
    ok = True
    ok &= run({}, label="default")
    ok &= run({"spark_speed": 0.07, "spark_boost": 6.0, "spark_density": 4.0},
              label="speed .07 boost 6 den 4")
    ok &= run({"spark_speed": 1.6, "spark_boost": 2.5, "spark_density": 2.0},
              label="speed 1.6 boost 2.5 den 2")
    ok &= run({"spark_speed": 3.0, "spark_boost": 4.0, "spark_density": 1.0},
              label="speed 3.0 boost 4 den 1")
    print()
    print("RESULT:", "no jumps" if ok else "there are jumps")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())