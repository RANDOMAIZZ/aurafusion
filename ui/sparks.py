"""Искры: выпуск по звуку, полёт и отрисовка. Подмешивается в класс Overlay.

Как искры слушают музыку
------------------------
Источник — спектр прямо из звукового движка (self._spec_raw), без
авто-баланса и без сглаживания атаки/отпускания: сглаживание размазывает
удар на несколько кадров, а авто-баланс растягивает тихий кусок трека до
полной высоты. Из спектра core.onsets.BandOnsets делает по каждой полосе:

    energy — ровный уровень полосы относительно громкости трека;
    onset  — резкий прирост полосы прямо сейчас (удар).

Три потока частиц:

    фон    — энергия полосы. Поток и высота растут с уровнем частоты:
             тянущийся аккорд даёт спокойный ровный поток на своих частотах,
             тишина — ничего.
    удар   — атака полосы. Пучок вылетает в тот же кадр, где пришёл удар,
             ровно из его частот: бочка слева, хэт справа. Число частиц и
             высота — от силы удара.
    бас    — сильная атака в нижней шестой части спектра: узкий плотный
             столб из одной точки.

Полёт считается по формуле от момента вылета, а не накоплением скорости по
кадрам: положение не зависит от fps и от того, как часто Qt перерисовывает
окно. После вершины искра не зависает, а медленно всплывает и гаснет —
поэтому не собирается в горизонтальные «слои» на одной высоте.
"""
import math
import time

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRadialGradient

from core.onsets import BandOnsets

# Ускорение торможения при скорости 1.0 на экране высотой 1080 px.
G_BG = 900.0          # фон всплывает мягко
G_HIT = 3600.0        # удар выстреливает резко и быстро тормозит
FADE_IN = 0.035       # с, искра разгорается (без мелькания точками)
JITTER = 0.22         # разброс высоты, σ логнормального множителя
BG_SHARE = 0.55       # фон занимает не больше этой доли буфера
BASS_THR = 0.8        # сила атаки баса, с которой летит отдельный столб
BASS_GAP = 0.12       # с, не чаще одного столба

KIND_BG, KIND_HIT, KIND_BASS = 0, 1, 2

_FIELDS_F = ("x", "y", "vx", "vy", "life", "full", "x0", "y0", "v0", "g",
             "tup", "drift", "born", "amp", "heat", "cr", "cg", "cb")


def _new_state(cap, n_bands):
    st = {k: np.zeros(cap, np.float32) for k in _FIELDS_F}
    st["born"] = np.zeros(cap, np.float64)
    st["kind"] = np.zeros(cap, np.int8)
    st["bin"] = np.zeros(cap, np.int32)
    st["y"][:] = 1e9
    st.update({"cap": cap, "t": None, "seq": None, "last_raw": None,
               "det": BandOnsets(n_bands), "acc_bg": np.zeros(n_bands, np.float32),
               "acc_hit": np.zeros(n_bands, np.float32), "last_bass": -10.0,
               "last_fresh": None,
               "punch_hits": 0, "punch_req": 0, "punch_ok": 0, "punch_drop": 0})
    return st


class SparksMixin:
    """Стиль «искры». Состояние частиц — в self._spark, шаг — из tick()."""

    # ------------------------------------------------------------ настройки
    @staticmethod
    def _spark_cfg(S):
        def f(key, dflt, lo, hi):
            try:
                return float(np.clip(float(S.get(key, dflt)), lo, hi))
            except (TypeError, ValueError):
                return dflt
        return {
            "hs": f("height_scale", 0.55, 0.01, 2.0),
            "spd": f("spark_speed", 1.0, 0.01, 2.0),
            "boost": f("spark_boost", 1.0, 0.0, 6.0),
            "dens": f("spark_density", 1.0, 0.15, 8.0),
            "burst_n": f("spark_burst_count", 0.7, 0.0, 8.0),
            "burst_h": f("spark_burst_height", 0.35, 0.02, 0.95),
            "sens": f("spark_sensitivity", 1.0, 0.25, 4.0),
            "bass": f("spark_bass_burst", 0.5, 0.0, 1.0),
            "hover": f("spark_hover", 1.0, 0.0, 2.0),
        }

    # ------------------------------------------------------------ шаг
    def step_sparks(self, S):
        """Шаг искр: слушаем звук, выпускаем, двигаем, гасим.

        Физика живёт здесь, а не в draw_sparks: если шагать в paintEvent,
        частицы двигаются только когда Qt перерисует окно, и при скрытом
        или фоновом окне искры замирают, а потом разом срываются.
        """
        W, H = int(self.width()), int(self.height())
        if W <= 0 or H <= 0:
            return
        c = self._spark_cfg(S)
        raw = getattr(self, "_spec_raw", None)
        if raw is None or not len(raw):
            raw = np.asarray(getattr(self, "_beat_in", self.lines), dtype=np.float32)
        nb = len(raw)
        # Бюджет частиц: line_count × 4, от 32 до 2048.
        cap = int(np.clip(S.get("line_count", 64) * 4, 32, 2048))
        st = getattr(self, "_spark", None)
        if st is None or st["cap"] != cap or st["det"].n != nb:
            st = self._spark = _new_state(cap, nb)

        now = time.time()
        if st["t"] is None:
            st["t"] = now
        dt = float(np.clip(now - st["t"], 0.0, 0.1))
        st["t"] = now
        x0, y_base, w = self.zone(W, H)
        px = H / 1080.0                       # масштаб под разрешение

        # --- свежий ли кадр звука ---
        seq = getattr(self, "_audio_seq", None)
        if seq is not None:
            fresh = seq != st["seq"]
            st["seq"] = seq
        else:
            last = st["last_raw"]
            fresh = last is None or len(last) != nb or not np.array_equal(last, raw)
            st["last_raw"] = np.array(raw, copy=True)
        has_sig = bool(getattr(self, "_has_sig", True))
        det = st["det"]
        if not has_sig:
            energy, onset = det.silence(dt)
            fresh = False
        else:
            lf = st["last_fresh"]
            fdt = (now - lf) if (fresh and lf is not None) else dt
            if fresh:
                st["last_fresh"] = now
            energy, onset = det.update(np.asarray(raw, np.float32) * c["sens"],
                                       float(np.clip(fdt, 1e-3, 0.2)), fresh)

        # --- сколько выпускать ---
        dens = c["dens"]
        reqs = []                              # (полосы, вид)
        if has_sig:
            # Фон: поток полосы ~ energy^1.3. При полной громкости по всему
            # спектру — около половины буфера за секунду с небольшим.
            e = np.clip(energy, 0.0, 1.6)
            # Плюс немного на каждую звучащую полосу: одиночная тянущаяся нота
            # занимает две-три полосы из 64 и иначе была бы почти не видна.
            rate = ((0.6 * cap / 1.1) / nb * np.power(e, 1.3)
                    + 1.5 * (e > 0.12)) * dens
            st["acc_bg"] = np.minimum(st["acc_bg"] + rate * dt, 4.0)
            k_bg = np.floor(st["acc_bg"]).astype(np.int32)
            st["acc_bg"] -= k_bg
            if k_bg.any():
                reqs.append((np.repeat(np.arange(nb), k_bg), KIND_BG))
        if fresh and onset.any():
            # Удар: частиц пропорционально силе атаки. Остаток копится, и
            # слабая атака на нескольких полосах всё равно даёт несколько искр.
            st["acc_hit"] = np.minimum(st["acc_hit"] + onset * c["burst_n"] * 9.0 * dens,
                                       60.0)
            k_hit = np.floor(st["acc_hit"]).astype(np.int32)
            st["acc_hit"] -= k_hit
            if k_hit.any():
                reqs.append((np.repeat(np.arange(nb), k_hit), KIND_HIT))
            # Бас: сильная атака в нижней шестой части — отдельный столб.
            lo_n = max(2, nb // 6)
            lo = onset[:lo_n]
            top3 = np.sort(lo)[-3:]
            s_b = float(top3.mean()) if len(top3) else 0.0
            mono = time.monotonic()
            if (c["bass"] > 0.01 and s_b > BASS_THR
                    and mono - st["last_bass"] >= BASS_GAP):
                st["last_bass"] = mono
                st["punch_hits"] += 1
                k_b = int(np.argmax(lo))
                n_b = int(c["bass"] * (10.0 + 30.0 * min(1.0, s_b / 2.0)) * min(2.0, dens))
                st["punch_req"] += n_b
                if n_b > 0:
                    reqs.append((np.full(n_b, k_b, np.int32), KIND_BASS))
                    st["bass_s"] = s_b

        if reqs:
            self._spark_emit(st, reqs, c, energy, onset, x0, y_base, w, H, px, now)

        self._spark_move(st, c, dt, has_sig, now, px, H)

        self._stat["punch_hits"] = st["punch_hits"]
        self._stat["punch_req"] = st["punch_req"]
        self._stat["punch_ok"] = st["punch_ok"]
        self._stat["punch_drop"] = st["punch_drop"]
        self._stat["spark_alive"] = int((st["life"] > 0.0).sum())
        self._stat["spark_cap"] = cap

    # ------------------------------------------------------------ выпуск
    def _spark_emit(self, st, reqs, c, energy, onset, x0, y_base, w, H, px, now):
        cap = st["cap"]
        rng = np.random
        life = st["life"]
        # удар и бас — вперёд фона: при забитом буфере теряется фон, не удар
        reqs = sorted(reqs, key=lambda r: -r[1])
        free = list(np.flatnonzero(life <= 0.0)[::-1])
        n_bg = int(((life > 0.0) & (st["kind"] == KIND_BG)).sum())
        for bands, kind in reqs:
            bands = np.asarray(bands, np.int32)
            m = len(bands)
            if kind == KIND_BG:
                m = min(m, max(0, int(BG_SHARE * cap) - n_bg), len(free))
                bands = rng.permutation(bands)[:m]
                if m == 0:
                    continue
                slots = np.array([free.pop() for _ in range(m)], np.int64)
                n_bg += m
            else:
                take = min(m, len(free))
                slots = [free.pop() for _ in range(take)]
                if take < m:
                    # Места нет — уступают почти погасшие фоновые искры: удар
                    # важнее догорающего фона. Видимую искру не трогаем —
                    # её пропажа одним кадром заметна глазом.
                    alive = np.flatnonzero((life > 0.0) & (st["kind"] == KIND_BG))
                    if len(alive):
                        vis = self._spark_vis(st, alive)[0]
                        alive = alive[vis < 0.08]
                        order = alive[np.argsort(vis[vis < 0.08])]
                        slots += order[:m - take].tolist()
                if kind == KIND_BASS:
                    st["punch_ok"] += len(slots)
                    st["punch_drop"] += m - len(slots)
                bands = bands[:len(slots)]
                slots = np.asarray(slots, np.int64)
                m = len(slots)
                if m == 0:
                    continue
            self._spark_launch(st, slots, bands, kind, c, energy, onset,
                               x0, y_base, w, H, px, now)

    def _spark_launch(self, st, idx, bands, kind, c, energy, onset,
                      x0, y_base, w, H, px, now):
        m = len(idx)
        nb = len(energy)
        rng = np.random
        e = np.clip(energy[bands], 0.0, 1.6)
        o = np.clip(onset[bands], 0.0, 2.0)
        top = 0.97 * y_base
        # Общая высота фигуры: ползунок «Высота».
        h_fig = H * (0.10 + 0.85 * min(c["hs"], 1.0)) * max(1.0, c["hs"])
        h_fig = min(h_fig, top)
        jit = np.exp(rng.standard_normal(m) * JITTER)
        spd2 = max(c["spd"], 0.02) ** 2
        if kind == KIND_BG:
            # Фон: высота от уровня полосы, до половины фигуры.
            h = 0.5 * h_fig * (0.08 + 0.92 * np.power(e, 1.2))
            g = G_BG * px * spd2 * np.ones(m)
            amp = 0.30 + 0.45 * np.minimum(e, 1.2)
            heat = np.zeros(m)
            xs = (bands + rng.rand(m)) / nb
            vx = rng.standard_normal(m) * 12.0 * px
        else:
            # Удар: высота от силы атаки. «Высота всплеска» поднимает потолок
            # удара над фоном, «разгон» — насколько сильный удар выстреливает
            # резче слабого.
            h_hit = min(top, h_fig * (0.5 + c["burst_h"]))
            if kind == KIND_BASS:
                s = float(st.get("bass_s", 1.0))
                strength = np.full(m, min(1.0, s / 1.6))
                xs = (bands + 0.5 + rng.standard_normal(m) * 0.35) / nb
                vx = rng.standard_normal(m) * 25.0 * px
            else:
                strength = np.minimum(1.0, o / 1.2)
                xs = (bands + rng.rand(m)) / nb
                vx = rng.standard_normal(m) * 45.0 * px
            # высота — от силы удара и от громкости самой полосы: удар в
            # тихом куплете ниже, чем такой же удар в громком припеве
            h = h_hit * (0.22 + 0.78 * np.power(strength, 0.8)) \
                * (0.45 + 0.55 * np.minimum(e, 1.0))
            g = G_HIT * px * spd2 * (1.0 + 0.35 * c["boost"] * strength)
            amp = 0.55 + 0.6 * strength
            heat = 0.4 + 0.6 * strength
        # Разброс высоты логнормальный: искры одной полосы не выстраиваются
        # в линию. Потолок мягкий только у самого верха, чтобы сильные удары
        # не собирались в полосу под краем экрана.
        h = h * jit
        knee = 0.8 * top
        over = h > knee
        if over.any():
            h[over] = knee + (top - knee) * np.tanh((h[over] - knee) / (top - knee))
        h = np.maximum(h, 6.0 * px)
        # Подъём не дольше 4 с даже на минимальной скорости.
        g = np.maximum(g, 2.0 * h / 16.0)
        v0 = np.sqrt(2.0 * g * h)
        tup = v0 / g
        # После вершины искра всплывает и гаснет; «Зависание» — сколько.
        tail = (0.10 + 0.45 * c["hover"]) * (0.7 + 0.6 * rng.rand(m))
        if kind != KIND_BG:
            tail *= 0.8
        st["x0"][idx] = x0 + w * np.clip(xs, 0.0, 1.0)
        st["y0"][idx] = y_base - 1.0
        st["vx"][idx] = vx
        st["v0"][idx] = v0
        st["g"][idx] = g
        st["tup"][idx] = tup
        st["drift"][idx] = (30.0 + 40.0 * rng.rand(m)) * px
        st["full"][idx] = tup + tail
        st["life"][idx] = tup + tail
        st["born"][idx] = now
        st["kind"][idx] = kind
        st["bin"][idx] = bands
        st["amp"][idx] = amp
        st["heat"][idx] = heat
        for i, k in zip(idx.tolist(), bands.tolist()):
            r_, g_, b_ = self._shape_rgb(int(k))
            st["cr"][i], st["cg"][i], st["cb"][i] = r_, g_, b_
        st["x"][idx] = st["x0"][idx]
        st["y"][idx] = st["y0"][idx]
        st["vy"][idx] = -v0

    # ------------------------------------------------------------ полёт
    def _spark_move(self, st, c, dt, has_sig, now, px, H):
        life = st["life"]
        alive = life > 0.0
        if not alive.any():
            return
        # В тишине догорают втрое быстрее: музыка кончилась — искры тоже.
        life[alive] -= dt * (1.0 if has_sig else 4.0)
        a = np.flatnonzero(life > 0.0)
        dead = alive & (life <= 0.0)
        if a.size:
            age = (now - st["born"][a]).astype(np.float32)
            tup = st["tup"][a]
            v0 = st["v0"][a]
            g = st["g"][a]
            up = np.minimum(age, tup)
            # точная баллистика до вершины: y = y0 - v0 t + g t²/2
            y = st["y0"][a] - (v0 * up - 0.5 * g * up * up)
            post = np.maximum(age - tup, 0.0)
            y = y - st["drift"][a] * post
            st["y"][a] = y
            st["vy"][a] = np.where(age < tup, -(v0 - g * age), -st["drift"][a])
            # горизонталь: начальный толчок гаснет за ~0.5 с
            st["x"][a] = st["x0"][a] + st["vx"][a] * 0.5 * (1.0 - np.exp(-age / 0.5))
            dead[a[y < -8.0]] = True
        if dead.any():
            life[dead] = 0.0
            st["y"][dead] = 1e9

    # ------------------------------------------------------------ спрайты
    def _spark_sprite(self, kind, r, g, b):
        """Мягкий спрайт 64x64: kind 0 — ореол, 1 — ядро.

        Оба — гауссов спад к нулю, без колец и ступенек. Кэш по цвету с
        шагом 8, при переполнении очищается."""
        cache = getattr(self, "_sprites", None)
        if cache is None or len(cache) > 3000:
            cache = self._sprites = {}
        key = (kind, r >> 3, g >> 3, b >> 3)
        pm = cache.get(key)
        if pm is not None:
            return pm
        pm = QPixmap(64, 64)
        pm.fill(QColor(0, 0, 0, 0))
        col = QColor(min(255, (r >> 3) * 8 + 4), min(255, (g >> 3) * 8 + 4),
                     min(255, (b >> 3) * 8 + 4))
        grad = QRadialGradient(32.0, 32.0, 32.0)
        sigma = 0.36 if kind == 0 else 0.30
        peak = 150 if kind == 0 else 255
        for i in range(13):
            pos = i / 12.0
            al = peak * math.exp(-0.5 * (pos / sigma) ** 2)
            al *= max(0.0, 1.0 - pos ** 6)      # ровно 0 на краю
            cc = QColor(col)
            cc.setAlpha(int(round(al)))
            grad.setColorAt(pos, cc)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(grad))
        qp.drawEllipse(QRectF(0.0, 0.0, 64.0, 64.0))
        qp.end()
        cache[key] = pm
        return pm

    # ------------------------------------------------------------ рисунок
    def draw_sparks(self, p, W, H, n, alpha):
        """Только рисунок: двигает частицы step_sparks() из tick().

        Свет складывается (CompositionMode_Plus): там, где искр много,
        фонтан светлеет к белому, как настоящее пламя, а не перекрывается
        кругами (ядра и след; ореолы — обычным наложением, см. ниже).
        Свежая искра удара горит белым и за ~0.2 с остывает до
        своего цвета."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        st = getattr(self, "_spark", None)
        if st is None:
            return
        life = st["life"]
        a = np.flatnonzero(life > 0.0)
        if a.size:
            a = a[st["y"][a] >= -8.0]
        if a.size:
            self._spark_paint(p, S, st, a, y_base, alpha)
        pen = QPen(QColor(255, 255, 255, 45))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    @staticmethod
    def _spark_vis(st, a):
        """Видимость искр a (0..1) и её части: разгорание, затухание, возраст,
        доля подъёма. Одна формула и для рисунка, и для выбора, какую искру
        можно незаметно заменить."""
        life = st["life"][a]
        full = np.maximum(st["full"][a], 0.01)
        tup = np.maximum(st["tup"][a], 1e-3)
        age = np.maximum(full - life, 0.0)
        tail = np.maximum(full - tup, 1e-3)
        # разгорание, ровный свет на подъёме, затухание после вершины
        fin = np.clip(age / FADE_IN, 0.0, 1.0)
        fin = fin * fin * (3.0 - 2.0 * fin)
        fo = np.clip(life / tail, 0.0, 1.0)
        fo = fo * fo * (3.0 - 2.0 * fo)
        prog = np.clip(age / tup, 0.0, 1.0)          # 0 у базы, 1 на вершине
        ef = np.clip((st["y"][a] + 8.0) / 60.0, 0.0, 1.0)   # у верхнего края гаснет
        vis = (0.45 + 0.55 * np.minimum(st["amp"][a], 1.0)) * fin * fo * ef
        return vis, fin, fo, age, prog

    def _spark_paint(self, p, S, st, a, y_base, alpha):
        def f(key, dflt, lo, hi):
            try:
                return float(np.clip(float(S.get(key, dflt)), lo, hi))
            except (TypeError, ValueError):
                return dflt
        glow = f("spark_glow", 0.5, 0.0, 3.0)
        g_a = f("spark_glow_start", 0.0, 0.0, 1.0)
        g_b = f("spark_glow_end", 0.55, 0.0, 1.0)
        if g_b < g_a:
            g_a, g_b = g_b, g_a
        edge = max(0.08, 0.5 * (g_b - g_a)) if g_b > g_a else 0.5 * (1.0 - g_a)
        sz = f("spark_size", 1.0, 0.2, 3.0)
        tint = f("spark_height_tint", 0.3, 0.0, 1.0)
        trail = f("spark_trail", 0.5, 0.0, 1.0)

        vis, fin, fo, age, prog = self._spark_vis(st, a)
        amp = st["amp"][a]
        rad = (1.1 + 4.6 * amp * sz) * (0.55 + 0.45 * fo) * (0.5 + 0.5 * fin)
        rad = np.maximum(rad, 0.9)
        ys = st["y"][a]
        xs = st["x"][a]
        a_core = np.clip(alpha / 255.0 * vis, 0.0, 1.0)

        pc = np.stack((st["cr"][a], st["cg"][a], st["cb"][a]), axis=1).astype(np.float32)
        pc = pc * (0.75 + 0.45 * np.minimum(amp, 1.0))[:, None]
        # свежий удар горит белым и остывает
        hot = st["heat"][a] * np.exp(-age / 0.18)
        if tint > 0.01:
            hp = np.clip((y_base - ys) / max(1.0, 0.9 * y_base), 0.0, 1.0)
            hot = np.maximum(hot, tint * 0.85 * hp * hp)
        pc = np.clip(pc + (255.0 - pc) * np.clip(hot, 0.0, 1.0)[:, None], 0.0, 255.0)

        q = pc.astype(np.int32) >> 3
        keys = (q[:, 0] << 10) | (q[:, 1] << 5) | q[:, 2]
        uniq, inv = np.unique(keys, return_inverse=True)
        cols_u = [((int(u) >> 10) << 3, ((int(u) >> 5) & 31) << 3, (int(u) & 31) << 3)
                  for u in uniq]
        core_spr = [self._spark_sprite(1, r_, g_, b_) for r_, g_, b_ in cols_u]
        inv_l = inv.reshape(-1).tolist()
        xs_l, ys_l = xs.tolist(), ys.tolist()
        rc_l = (rad * 1.35).tolist()
        ac_l = a_core.tolist()

        had_sm = p.testRenderHint(QPainter.SmoothPixmapTransform)
        base_op = p.opacity()
        mode0 = p.compositionMode()
        p.setPen(Qt.NoPen)
        src = QRectF(0.0, 0.0, 64.0, 64.0)
        draw, set_op, R = p.drawPixmap, p.setOpacity, QRectF

        # ореол — только в своём окне высоты полёта, края окна мягкие
        if glow > 0.02:
            e_ = max(edge, 1e-6)
            gw = np.clip((prog - (g_a - e_)) / e_, 0.0, 1.0) * \
                np.clip(((g_b + e_) - prog) / e_, 0.0, 1.0)
            op_h = a_core * gw * min(1.0, 0.35 + 0.35 * glow)
            hsel = np.flatnonzero(op_h >= 0.02)
            if hsel.size:
                p.setRenderHint(QPainter.SmoothPixmapTransform, False)
                halo_spr = {}
                rh_l = (rad[hsel] * (1.6 + 1.8 * glow * gw[hsel])).tolist()
                oh_l = op_h[hsel].tolist()
                for j, i in enumerate(hsel.tolist()):
                    ci = inv_l[i]
                    spr = halo_spr.get(ci)
                    if spr is None:
                        spr = halo_spr[ci] = self._spark_sprite(0, *cols_u[ci])
                    rh = rh_l[j]
                    set_op(oh_l[j])
                    draw(R(xs_l[i] - rh, ys_l[i] - rh, rh * 2.0, rh * 2.0), spr, src)
        # Ядра и след — сложением света: где искр много, середина
        # раскаляется до белого. Ореолы выше — обычным наложением: сложение
        # для больших спрайтов в Qt вдвое дороже, а мягкий гауссов ореол
        # и так не даёт колец.
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for i in range(len(xs_l)):
            ac = ac_l[i]
            if ac < 0.004:
                continue
            rc = rc_l[i]
            set_op(ac)
            draw(R(xs_l[i] - rc, ys_l[i] - rc, rc * 2.0, rc * 2.0), core_spr[inv_l[i]], src)
        # след — только у тех, кто сейчас быстро летит вверх
        if trail > 0.01:
            vy = st["vy"][a]
            fast = np.flatnonzero((vy < -350.0 * max(0.15, float(np.clip(
                S.get("spark_speed", 1.0), 0.01, 2.0)))) & (a_core > 0.05))
            if fast.size:
                vy_l = vy[fast].tolist()
                for j, i in enumerate(fast.tolist()):
                    # копии чаще радиуса — сливаются в сплошной хвост,
                    # а не в пунктир из отдельных точек
                    rc, ac, spr = rc_l[i], ac_l[i], core_spr[inv_l[i]]
                    length = -vy_l[j] * (0.012 + 0.020 * trail)
                    cnt = int(min(5, max(2, length / max(0.8 * rc, 1.0))))
                    step = length / cnt
                    for k in range(1, cnt + 1):
                        q_ = k / (cnt + 1.0)
                        rg = rc * (1.0 - 0.55 * q_)
                        set_op(ac * trail * 0.55 * (1.0 - q_))
                        yy = ys_l[i] + step * k
                        draw(R(xs_l[i] - rg, yy - rg, rg * 2.0, rg * 2.0), spr, src)
        p.setCompositionMode(mode0)
        p.setOpacity(base_op)
        p.setRenderHint(QPainter.SmoothPixmapTransform, had_sm)
