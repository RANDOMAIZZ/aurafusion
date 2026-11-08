"""Плавность искр: не пропадают ли они рывками.

Считаем не число живых частиц, а свет: сколько искр пропало (умерло или
было заменено новой) в кадре, где они ещё были видны. Искра, догоревшая
до нуля, исчезает незаметно, сколько бы их ни умерло разом; а вот искра,
выключенная на половине яркости, — это и есть «рывок».

Порог нормы: ни одной искры ярче 10% не пропадает одним кадром.
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
    counts, pops = [], 0
    prev_vis = None
    prev_life = None
    prev_kind = None
    n = int(seconds * 60)
    for _ in range(n):
        ov.tick()
        st = ov._spark
        life = st["life"].copy()
        if prev_vis is not None and len(prev_life) == len(life):
            gone = (prev_life > 0) & ((life <= 0) | (life > prev_life + 1e-6))
            # ракета салюта гаснет на вершине нарочно: на её месте в том же
            # кадре вспышка и осколки, это не рывок
            if "kind" in st:
                gone &= prev_kind != 2
            pops += int((gone & (prev_vis > 0.10)).sum())
        idx = np.arange(len(life))
        vis = np.zeros(len(life))
        alive = life > 0
        if alive.any():
            vis[alive] = ov._spark_vis(st, idx[alive])[0]
        prev_vis, prev_life = vis, life
        prev_kind = st["kind"].copy() if "kind" in st else None
        counts.append(int(alive.sum()))
        time.sleep(1.0 / 60.0)
    ov.close()
    c = np.array(counts)
    print("  %-26s частиц %4d..%4d | пропало видимых %4d | %s"
          % (label, int(c.min()), int(c.max()), pops,
             "плавно" if pops == 0 else "РЫВОК"))
    return pops == 0


def main():
    QApplication.instance() or QApplication([])
    print("=== smooth fade-out of sparks (worst single-frame drop) ===")
    ok = True
    ok &= run({}, label="default")
    ok &= run({"spark_speed": 0.07, "spark_boost": 6.0, "spark_density": 4.0},
              label="speed .07 boost 6 den 4")
    ok &= run({"spark_speed": 1.6, "spark_boost": 2.5, "spark_density": 2.0},
              label="speed 1.6 boost 2.5 den 2")
    ok &= run({"spark_speed": 2.0, "spark_boost": 4.0, "spark_density": 8.0,
               "spark_burst_count": 8.0}, label="speed 2 boost 4 den 8 burst 8")
    print()
    print("RESULT:", "no jumps" if ok else "there are jumps")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())