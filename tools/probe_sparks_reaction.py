"""Стенд реакции искр на звук: насколько точно искры отвечают на музыку.

Звук не подделывается спектром: синтезируется настоящая волна (бочка, малый,
хэт, бас, пэд) и идёт через тот же расчёт, что и живой звук, —
SharedAudio.spectrum_from_fft и бас 20-250 Гц. Время виртуальное: каждый тик
оверлея это ровно 1/60 с звука, поэтому замер одинаков от запуска к запуску
и не зависит от скорости машины.

Трек (секунды):
    0-1    тишина
    1-7    куплет: тихо (x0.35), бочка на каждую долю, хэт на слабые восьмые
    7-13   припев: громко, бочка, малый на 2 и 4, хэт, бас
    13-16  только пэд: ровный аккорд с медленной волной громкости, ударов нет
    16-18  тишина

Что меряется:
    задержка   — от удара до первого кадра, где в его частотной зоне выпуск
                 частиц подскочил (втрое выше, чем перед ударом)
    попадания  — какая доля ударов вообще дала такой всплеск за 80 мс
    зона       — куда вылетели искры удара: бочка слева, хэт справа
    ложные     — всплески выпуска на пэде, где ударов нет
    динамика   — во сколько раз искры припева выше искр куплета
    тишина     — сколько секунд искры живут после того, как звук стих
    полосы     — доля частиц, висящих почти без скорости (они и рисуют
                 горизонтальные «слои»)

    python tools/probe_sparks_reaction.py [--shots ПАПКА]

С --shots сохраняет кадры в ключевые моменты трека (список SHOTS).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import numpy as np                                    # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402
import ui.overlay as overlay_mod                      # noqa: E402
import ui.sparks as sparks_mod                        # noqa: E402
from core.audio_engine import SharedAudio             # noqa: E402
from core.config import DEFAULTS                      # noqa: E402
from shot_overlay import FakeSC, VirtualClock         # noqa: E402

SR = 48000
FPS = 60
W, H = 1920, 1080
BPM = 120.0


# ---------------------------------------------------------------- синтез
def _env(n, tau):
    return np.exp(-np.arange(n) / (tau * SR))


def kick(n=int(0.35 * SR)):
    t = np.arange(n) / SR
    f = 50.0 + 70.0 * np.exp(-t / 0.04)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * _env(n, 0.12)


def snare(rng, n=int(0.25 * SR)):
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0.0) * 0.5
    t = np.arange(n) / SR
    return (0.5 * noise * _env(n, 0.07) + 0.6 * np.sin(2 * np.pi * 190 * t) * _env(n, 0.05))


def hat(rng, n=int(0.08 * SR)):
    x = rng.standard_normal(n)
    for _ in range(2):
        x = np.diff(x, prepend=0.0)
    return 0.6 * x * _env(n, 0.025)


def make_track(seed=3):
    rng = np.random.default_rng(seed)
    total = 18.0
    y = np.zeros(int(total * SR) + SR)
    events = []
    beat = 60.0 / BPM

    def put(sig, t, gain, kind, region):
        i = int(t * SR)
        y[i:i + len(sig)] += gain * sig[:len(y) - i]
        events.append((t, kind, region, gain))

    # куплет — тихий
    t = 1.0
    while t < 7.0 - 1e-6:
        put(kick(), t, 0.35 * 0.9, "kick", "lo")
        put(hat(rng), t + beat / 2, 0.35 * 1.0, "hat", "hi")
        t += beat
    # припев — громкий
    t = 7.0
    k = 0
    while t < 13.0 - 1e-6:
        put(kick(), t, 0.9, "kick", "lo")
        if k % 2 == 1:
            put(snare(rng), t, 0.7, "snare", "mid")
        put(hat(rng), t + beat / 2, 1.0, "hat", "hi")
        t += beat
        k += 1
    # бас припева: ровная нота, без атак посреди такта
    i0, i1 = int(7.0 * SR), int(13.0 * SR)
    tt = np.arange(i1 - i0) / SR
    y[i0:i1] += 0.25 * np.sin(2 * np.pi * 55 * tt)
    # пэд: аккорд с медленной волной громкости, ударов нет
    i0, i1 = int(13.0 * SR), int(16.0 * SR)
    tt = np.arange(i1 - i0) / SR
    swell = 0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * tt - np.pi / 2)
    fade = np.minimum(1.0, np.minimum(tt / 0.3, (tt[-1] - tt) / 0.3))
    pad = sum(np.sin(2 * np.pi * f * tt) for f in (220.0, 277.2, 329.6, 440.0, 880.0)) / 5
    y[i0:i1] += 0.45 * pad * swell * fade
    return y.astype(np.float32), events


# ------------------------------------------------------- звук-заглушка
class TrackAudio:
    """Отдаёт трек кусками по 1/60 с через расчёт настоящего движка.

    Если у движка есть feed() — используется он (скользящее окно, как в
    живом приложении). Иначе повторяется прежнее поведение: спектр
    пересчитывается целым блоком chunk раз в chunk сэмплов.
    """

    def __init__(self, track, cfg):
        self.y = track
        self.pos = 0
        self.cfg = cfg
        self.eng = SharedAudio(lambda: cfg, cfg.get("bars", 64))
        self.eng.samplerate = SR
        self._acc = np.zeros(0, np.float32)

    def advance(self):
        step = SR // FPS
        x = self.y[self.pos:self.pos + step]
        self.pos += step
        if hasattr(self.eng, "feed"):
            self.eng.feed(x, SR)
            return
        chunk = int(self.cfg.get("chunk", 2048))
        self._acc = np.concatenate([self._acc, x])
        while len(self._acc) >= chunk:
            blk, self._acc = self._acc[:chunk], self._acc[chunk:]
            self._old_process(blk, chunk)

    def _old_process(self, x, chunk):
        e, c = self.eng, self.cfg
        e.block = chunk
        rms = float(np.sqrt(np.mean(x * x)))
        bass = e._bass_level(x, chunk, SR)
        boost = float(c.get("bass_boost", 1.6))
        spec = e.spectrum_from_fft(x, c.get("sensitivity", 1.4), c.get("bass_gain", 1.0),
                                   c.get("mid_gain", 1.25), c.get("treble_gain", 1.7))
        thr = float(c.get("silence_thr", 0.002))
        la = float(np.abs(x).mean())
        e.has_signal = la > thr * (0.5 if e.has_signal else 1.5)
        if e.has_signal:
            n = min(len(spec), len(e.spec))
            e.spec[:n] = spec[:n]
        else:
            e.spec *= 0.9
        e.level = max(rms, bass * boost)
        e.bass = bass * boost

    def snapshot(self):
        return self.eng.snapshot()

    def set_bars(self, n):
        self.eng.set_bars(n)

    def __getattr__(self, k):
        return getattr(self.eng, k)


# ------------------------------------------------------------- прогон
def _shot(ov, path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    layer = QPixmap(W, H)
    layer.fill(Qt.transparent)
    ov.render(layer)
    pm = QPixmap(W, H)
    pm.fill(QColor(13, 17, 26))        # тёмный фон, как у игры в полутьме
    p = QPainter(pm)
    p.drawPixmap(0, 0, layer)
    p.end()
    pm.save(path, "PNG")


def run(cfg_over=None, shots=None):
    cfg = dict(DEFAULTS)
    cfg.update({"bars": 64, "demo": False, "sys_fx": False, "overlay_fps": FPS,
                "style": "sparks", "viz_y": 0.93, "height_scale": 0.8,
                "line_count": 96, "auto_hide": False})
    cfg.update(cfg_over or {})
    track, events = make_track()
    clock = VirtualClock(fps=FPS, start=1000.0)
    saved = (overlay_mod.time, sparks_mod.time)
    overlay_mod.time = clock
    sparks_mod.time = clock
    aud = TrackAudio(track, cfg)
    try:
        ov = overlay_mod.Overlay(lambda: cfg, aud, FakeSC())
        ov.resize(W, H)
        ov._hide_k = 1.0
        ov._last_eff_op = -1.0
        x0, yb, zw = ov.zone(W, H)
        frames = int(17.9 * FPS)
        births = []          # (кадр, x 0..1, слот)
        maxh = {}
        rec_h = []           # (кадр рождения, высота вершины)
        live_n, stall_n = [], []
        prev_life = None
        # плавность: яркость и вертикальное ускорение каждой искры прошлых кадров
        prev_vis = prev_vy = prev_acc = None
        prev_life_s = None
        fade_drops, jerks = [], []
        for f in range(frames):
            aud.advance()
            # Тик зовёт time.time()/monotonic несколько раз; держим время
            # равным номеру кадра, чтобы dt был ровно 1/60.
            clock.t = 1000.0 + f / FPS
            clock.step = 0.0
            ov.tick()
            if shots and f in shots:
                _shot(ov, shots[f])
            st = ov._spark
            life = np.asarray(st["life"], dtype=np.float64)
            if prev_life is None or len(prev_life) != len(life):
                prev_life = np.zeros_like(life)
            born = np.flatnonzero((life > prev_life + 1e-6) & (life > 0))
            for i in born.tolist():
                if i in maxh:
                    rec_h.append(maxh.pop(i))
                births.append((f, (float(st["x"][i]) - x0) / zw, i))
                maxh[i] = [f, 0.0]
            alive = life > 0
            ys = np.asarray(st["y"], dtype=np.float64)
            for i in np.flatnonzero(alive).tolist():
                if i in maxh:
                    maxh[i][1] = max(maxh[i][1], yb - ys[i])
            for i in list(maxh.keys()):
                if not alive[i]:
                    rec_h.append(maxh.pop(i))
            prev_life = life.copy()
            live_n.append(int(alive.sum()))
            vy = np.asarray(st["vy"], dtype=np.float64)
            hgt = yb - ys
            # «висит»: живая, уже выше 10% экрана и почти не движется
            stall = alive & (np.abs(vy) < 0.02 * H) & (hgt > 0.10 * H)
            stall_n.append(int(stall.sum()))
            # --- плавность, только для искр, живших и в прошлом кадре ---
            vis = np.zeros(len(life))
            ai = np.flatnonzero(alive)
            if ai.size and hasattr(ov, "_spark_vis"):
                vis[ai] = ov._spark_vis(st, ai)[0]
            acc = None
            if prev_vy is not None and len(prev_vy) == len(vy):
                same = alive & (prev_life_s > 0) & ~np.isin(np.arange(len(life)), born)
                acc = (vy - prev_vy) * FPS
                # угасание: насколько яркость упала за кадр (смерть — до нуля)
                gone = (prev_life_s > 0) & ((~alive) | np.isin(np.arange(len(life)), born))
                d = np.where(same, prev_vis - vis, np.where(gone, prev_vis, 0.0))
                fade_drops.append(float(d.max()) if d.size else 0.0)
                if prev_acc is not None:
                    # излом: скачок ускорения за кадр, в px/с² на 1080 строк
                    j = np.abs(acc - prev_acc)[same & (prev_life_s > 0)]
                    if j.size:
                        jerks.append(float(np.percentile(j, 99)) * 1080.0 / H)
            prev_vis, prev_vy, prev_acc = vis, vy.copy(), acc
            prev_life_s = life.copy()
        rec_h.extend(maxh.values())
        ov.close()
    finally:
        overlay_mod.time, sparks_mod.time = saved
    res = analyse(events, births, rec_h, live_n, stall_n)
    if fade_drops:
        res["самое резкое угасание за кадр, доля яркости"] = round(
            float(np.percentile(fade_drops, 99)), 3)
    if jerks:
        res["излом подъёма: скачок ускорения, px/с² (p99)"] = int(np.median(jerks))
    return res


REG = {"lo": (0.0, 0.25), "mid": (0.25, 0.65), "hi": (0.65, 1.0)}


def analyse(events, births, rec_h, live_n, stall_n):
    nf = len(live_n)
    b_f = np.array([b[0] for b in births], dtype=np.int64)
    b_x = np.array([b[1] for b in births], dtype=np.float64)
    out = {}

    def rate(region, f0, f1):
        lo, hi = REG[region]
        m = (b_f >= f0) & (b_f < f1) & (b_x >= lo) & (b_x < hi)
        return int(m.sum())

    lat, hits, inzone = [], 0, []
    missed = []
    used = [e for e in events if e[1] in ("kick", "hat")]
    for t, kind, region, _g in used:
        f_on = int(round(t * FPS))
        base = rate(region, f_on - 12, f_on) / 12.0
        found = None
        for d in range(0, 6):            # до 100 мс
            c = rate(region, f_on + d, f_on + d + 1)
            if c >= max(3.0 * base, base + 3.0):
                found = d
                break
        if found is not None and found / FPS <= 0.08:
            hits += 1
            lat.append(found / FPS * 1000.0)
        else:
            missed.append("%s@%.2f" % (kind, t))
        # куда легли искры за 60 мс после удара (сверх фона)
        m = (b_f >= f_on) & (b_f < f_on + 4)
        if m.sum():
            lo, hi = REG[region]
            inzone.append(float(((b_x[m] >= lo) & (b_x[m] < hi)).mean()))
    out["попадания"] = "%d/%d" % (hits, len(used))
    if missed:
        out["пропущены"] = " ".join(missed[:12])
    out["задержка, мс (медиана)"] = round(float(np.median(lat)), 1) if lat else None
    out["искр удара в своей зоне"] = round(float(np.mean(inzone)), 2) if inzone else None

    # ложные всплески на пэде: кадры, где выпуск втрое выше скользящей медианы
    per = np.bincount(b_f, minlength=nf).astype(float)
    pad = per[int(13.3 * FPS):int(15.7 * FPS)]
    med = np.median(per[int(13.3 * FPS):int(15.7 * FPS)]) if len(pad) else 0.0
    spikes = int(np.sum(pad >= max(3.0 * med, med + 6.0)))
    out["ложные всплески на пэде (кадров)"] = spikes
    out["искр/с на пэде"] = round(float(pad.sum() / 2.4), 1)

    h_arr = np.array(rec_h, dtype=np.float64)
    if h_arr.size:
        hf, hh = h_arr[:, 0], h_arr[:, 1]
        verse = hh[(hf >= 2 * FPS) & (hf < 7 * FPS)]
        chorus = hh[(hf >= 8 * FPS) & (hf < 13 * FPS)]
        v90 = float(np.percentile(verse, 90)) if verse.size else 0.0
        c90 = float(np.percentile(chorus, 90)) if chorus.size else 0.0
        out["высота куплет / припев, px (p90)"] = "%d / %d" % (v90, c90)
        out["припев выше куплета, раз"] = round(c90 / max(v90, 1.0), 2)
        out["искр/с куплет / припев"] = "%d / %d" % (
            verse.size / 5.0, chorus.size / 5.0)
    # тишина после 16 с: когда погасла последняя
    tail = live_n[int(16.0 * FPS):]
    zero_at = next((i for i, v in enumerate(tail) if v == 0), None)
    out["гаснут после конца звука, с"] = (round(zero_at / FPS, 2)
                                          if zero_at is not None else ">1.9")
    out["искр в начальной тишине"] = int(sum(live_n[:int(0.9 * FPS)]))
    ch = slice(int(8 * FPS), int(13 * FPS))
    out["висят без движения (полосы), % живых"] = round(
        100.0 * sum(stall_n[ch]) / max(1, sum(live_n[ch])), 1)
    return out


SHOTS = (("куплет, 30 мс после бочки", 3.03), ("куплет, 150 мс", 3.15),
         ("припев, 30 мс после бочки", 9.03), ("припев, 150 мс", 9.15),
         ("припев, хэт", 9.28), ("пэд без ударов", 14.5))


def main():
    QApplication.instance() or QApplication([])
    shots = None
    if len(sys.argv) > 2 and sys.argv[1] == "--shots":
        out = sys.argv[2]
        os.makedirs(out, exist_ok=True)
        shots = {int(round(t * FPS)): os.path.join(out, "%02d.png" % i)
                 for i, (_l, t) in enumerate(SHOTS)}
    res = run(shots=shots)
    w = max(len(k) for k in res)
    for k, v in res.items():
        print("  %-*s  %s" % (w, k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
