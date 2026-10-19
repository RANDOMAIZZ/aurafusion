"""Проверка искр: разгон на своей частоте, фон по всей площади, плотность.

Замер идёт по силе частицы (amp), а не по номеру полосы: номер — это индекс
в self.lines, а их line_count штук, тогда как спектр от аудио короче и
подгоняется интерполяцией. Привязка к номеру врёт: пик исходного спектра
попадает в другие индексы, и сравнение показывает ерунду.

Плюс включён auto_balance — он выравнивает громкость трёх третей спектра, так
что «тихие полосы» в тесте оказываются не такими уж тихими.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np                                    # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402
from core.config import DEFAULTS                      # noqa: E402
from ui.overlay import Overlay                        # noqa: E402
from shot_overlay import synth, FakeSC                # noqa: E402

N, W, H = 64, 1280, 720


class Fake:
    """Спектр с живым движением: у полосы 40..46 волнами идут вбок.

    Искры смотрят не на высоту спектра, а на её рост, поэтому статичный
    спектр для проверки разгона бесполезен: роста нет, всплесков нет,
    и все частицы летят с одинаковой скоростью при любом разгоне.
    """

    def __init__(self, spec, level, bass):
        self.spec, self.level, self.bass = spec, level, bass
        self.base, self.t = spec.copy(), 0.0

    def snapshot(self):
        self.t += 1.0 / 60.0
        hit = 1.0 if (self.t % 0.35) < 0.06 else 0.0
        s = self.base * (0.25 + 1.1 * hit)
        self.spec = s.astype(np.float32)
        return (self.spec.copy(), np.zeros(512, np.float32),
                self.level, self.bass, True)

    def set_bars(self, n):
        pass


def build(style_over=None, audio=None, seconds=3.0):
    cfg = dict(DEFAULTS)
    cfg.update({"bars": N, "demo": True, "sys_fx": False, "overlay_fps": 60,
                "style": "sparks", "viz_y": 0.92, "height_scale": 0.8,
                "line_count": 96})
    cfg.update(style_over or {})
    spec, level, bass = audio or (synth(N), 0.55, 0.5)
    ov = Overlay(lambda: cfg, Fake(spec, level, bass), FakeSC())
    ov.resize(W, H)
    ov._hide_k = 1.0
    ov._last_eff_op = -1.0
    end = time.time() + seconds
    while time.time() < end:
        ov.tick()
        time.sleep(1 / 120.0)
    return ov


def report(ov, tag):
    """Раскладывает частицы по силе полосы: громкие летели выше тихих."""
    st = ov._spark
    live = st["life"] > 0
    if not live.any():
        print("  %-22s частиц нет" % tag)
        return
    yb = ov.zone(W, H)[1]
    height = yb - st["y"][live].astype(float)
    amp = st["amp"][live]
    hot = amp > amp.max() * 0.6
    cold = ~hot
    hh = float(np.mean(height[hot])) if hot.any() else 0.0
    ch = float(np.mean(height[cold])) if cold.any() else 0.0
    print("  %-22s частиц %3d | громкая частота %.0f px, тихие %.0f px | разгон x%.2f"
          % (tag, int(live.sum()), hh, ch, hh / max(ch, 1.0)))


def main():
    QApplication.instance() or QApplication([])
    prof = np.full(N, 0.12, np.float32)
    prof[40:46] = 1.3
    music = (prof, 0.6, 0.5)

    print("=== 1. фон по всей площади, пока играет музыка ===")
    for tag, audio in (("играет", music),
                       ("тихо, но играет", (prof * 0.12, 0.03, 0.02)),
                       ("тишина", (np.zeros(N, np.float32), 0.0, 0.0))):
        ov = build({"spark_speed": 0.5, "spark_boost": 0.0}, audio)
        st = ov._spark
        live = st["life"] > 0
        xs = st["x"][live]
        print("  %-18s частиц %3d   разброс по X %4d px из 1178"
              % (tag, int(live.sum()), (int(xs.max() - xs.min()) if xs.size else 0)))
        ov.close()

    print("\n=== 2. разгон: где поднимается своя частота ===")
    for b in (0.0, 1.0, 3.0):
        ov = build({"spark_speed": 1.0, "spark_boost": b}, music)
        report(ov, "разгон %.1f" % b)
        ov.close()

    print("\n=== 3. плотность ===")
    for d in (0.3, 1.0, 3.0):
        ov = build({"spark_density": d}, music)
        st = ov._spark
        print("  плотность %.1f -> частиц %3d (бюджет %d)"
              % (d, int((st["life"] > 0).sum()), st["cap"]))
        ov.close()

    print("\n=== 4. все летят вверх, никто не падает ===")
    ov = build({}, music)
    st = ov._spark
    live = st["life"] > 0
    yb = ov.zone(W, H)[1]
    print("  все vy < 0: %s   ниже базы: %s"
          % (bool(np.all(st["vy"][live] <= 0)),
             bool(np.all(st["y"][live] <= yb + 1))))
    ov.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())