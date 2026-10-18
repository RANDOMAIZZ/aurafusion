"""Ищет кадры, где фигура схлопывается.

«Волна временами пропадает» — это когда высота фигуры падает почти в ноль
при живом звуке. Причина может быть в сглаживании, автобалансе, тизе линии
или в автоскрытии. Здесь гоняем настоящий путь отрисовки (tick + paintEvent)
на длинном меняющемся сигнале и меряем высоту фигуры в каждом кадре.

    python tools/find_fadeout.py [секунд]
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from core import config as C
from ui.overlay import STYLES, Overlay

W, H = 1280, 720


class Audio:
    """Живой звук: бас качает, паузы, тихие места, громкие удары."""

    def __init__(self, n):
        self.n = n
        self.t = 0.0

    def set_bars(self, n):
        self.n = n

    def snapshot(self):
        self.t += 1.0 / 60.0
        n = self.n
        t = self.t
        x = np.linspace(0, 1, n)
        env = 1.25 * np.exp(-3.1 * x) + 0.28 * np.exp(-0.6 * x) + 0.05
        peaks = np.zeros(n)
        for c, w, a in ((2, 1.6, .42), (7, 1.1, .34), (13, .8, .30), (21, .7, .24),
                        (31, .5, .20), (43, .4, .17), (52, .3, .14)):
            peaks += a * np.exp(-0.5 * ((np.arange(n) - c) / w) ** 2)
        # доля секунды: 60% музыка, 20% тихо, 20% пауза
        ph = t % 5.0
        if ph < 3.0:
            amp, sig = 1.0, True
        elif ph < 4.0:
            amp, sig = 0.06, True
        else:
            amp, sig = 0.0, False
        v = env * peaks * amp + np.random.default_rng(int(t * 60)).random(n) * 0.09 * amp
        return (np.clip(v, 0, 1.35).astype(np.float32),
                np.zeros(512, np.float32), float(amp), float(amp), sig)


class SC:
    def __init__(self):
        self.shown = np.array([40.0, 190.0, 235.0])
        self.bass = 0.5
        self.lock = None

    def tick(self, c):
        pass

    def feed_frame(self, a):
        pass


def measure(ov, pm):
    """Высота фигуры в пикселях над базовой линией.

    Считаем непрозрачные пиксели в полосе выше базовой линии. Базовая линия
    сама по себе непрозрачна и всегда на дне — по ней одной высоту не
    измерить, нужен отступ от неё."""
    _x0, y_base, _w = ov.zone(W, H)
    img = pm.toImage()
    top = None
    s = 0
    y = int(y_base) - 3
    while y > 0:
        for x in range(0, W, 4):
            if img.pixelColor(x, y).alpha() > 24:
                s += 1
        if s >= 3:
            top = y
            break
        s = 0
        y -= 1
    return 0 if top is None else int(y_base) - top


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    # QApplication нужен до создания Overlay: без экземпляра Qt виджет не
    # построится. Раньше он создавался здесь, а переменная не читалась —
    # отсюда и предупреждение «не используется», и соблазн её снести.
    QApplication.instance() or QApplication([])
    cfg = C.load()
    audio = Audio(cfg.get("bars", 64))
    ov = Overlay(lambda: cfg, audio, SC())
    ov.resize(W, H)
    pm = QPixmap(W, H)

    print("конфиг: style=%s height_scale=%s line_style=%s alpha=%s fill_alpha=%s"
          % (cfg["style"], cfg["height_scale"], cfg["line_style"],
             cfg["alpha"], cfg["fill_alpha"]))
    print("auto_hide=%s порог=%s demo=%s"
          % (cfg.get("auto_hide"), cfg.get("auto_hide_sec"), cfg.get("demo")))
    print()

    for key, name in STYLES:
        cfg["style"] = key
        ov.disp = np.zeros(cfg.get("bars", 64), dtype=np.float32)
        ov._hide_k = 1.0
        ov._last_eff_op = -1.0
        heights = []
        n = secs * 60
        for f in range(n):
            try:
                ov.tick()
            except Exception as e:
                print("  %-9s ИСКЛЮЧЕНИЕ на кадре %d: %s: %s"
                      % (key, f, type(e).__name__, e))
                break
            pm.fill(0)
            ov.render(pm)
            heights.append(measure(ov, pm))
        h = np.array(heights, dtype=np.float32)
        dead = int((h <= 2).sum())
        print("  %-9s кадров %4d  высота: мин %3d, сред %3d, макс %3d   "
              "пропали (высота<=2): %4d"
              % (name, len(h), h.min() if len(h) else 0,
                 h.mean() if len(h) else 0, h.max() if len(h) else 0, dead))
    cfg["style"] = "smooth"


if __name__ == "__main__":
    main()