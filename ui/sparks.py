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
    салют  — сильная атака в нижней шестой части спектра (бас): из точки
             удара взлетает ракета и на вершине рассыпается кольцом искр,
             которые тормозят о воздух, провисают и мерцают, догорая.

Чтобы искры не летели по линейке, у каждой свой небольшой разлёт вбок и
своё покачивание (ползунок «Хаос разлёта»).

Полёт считается по формуле от момента вылета (для осколков салюта — с
сопротивлением воздуха и тяжестью), а не накоплением скорости по кадрам: положение не зависит от fps и от того, как часто Qt перерисовывает
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
BASS_THR = 0.55       # сила удара баса (атака × громкость), с которой летит салют
BASS_GAP = 0.35       # с, не чаще одного салюта: иначе на каждой доле
                      # припева небо было бы сплошной кашей из взрывов
FW_DRAG = 2.4         # 1/с, торможение осколков салюта о воздух
FW_G = 0.22           # провисание осколков, доля высоты экрана за с²
SWAY_RAMP = 0.25      # с, покачивание нарастает после вылета, а не с места
FW_RISE = 0.35        # с, подъём ракеты: взрыв почти сразу за ударом
FW_RESERVE = 320      # слотов сверх бюджета — под осколки двух-трёх салютов сразу

# Виды частиц. Ракета салюта летит как удар, на вершине исчезает и
# рождает осколки и вспышку.
KIND_BG, KIND_HIT, KIND_SHELL, KIND_FW, KIND_FLASH = 0, 1, 2, 3, 4

_FIELDS_F = ("x", "y", "vx", "vy", "life", "full", "x0", "y0", "v0", "g",
             "tup", "drift", "born", "amp", "heat", "cr", "cg", "cb",
             # сопротивление воздуха (осколки), покачивание: размах, частота,
             # фаза; скорость сейчас (для следа); осколков и сила у ракеты
             "k", "sw", "om", "ph", "vxn", "vyn", "fwn", "fws")


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
            "chaos": f("spark_chaos", 0.5, 0.0, 2.0),
            "fw_size": f("spark_fw_size", 1.0, 0.3, 2.0),
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
        # Бюджет частиц: line_count × 4, от 32 до 2048. Плюс запас под
        # салют: его осколки появляются разом, и без запаса им не хватало
        # места рядом с фоном и ударами — кольцо выходило дырявым.
        base_cap = int(np.clip(S.get("line_count", 64) * 4, 32, 2048))
        cap = min(2048, base_cap + (FW_RESERVE if c["bass"] > 0.01 else 0))
        st = getattr(self, "_spark", None)
        if st is None or st["cap"] != cap or st["det"].n != nb:
            st = self._spark = _new_state(cap, nb)
        st["base_cap"] = base_cap

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
            # Бас: сильная атака в нижней шестой части — салют.
            lo_n = max(2, nb // 6)
            lo = onset[:lo_n]
            # Сила удара: самая сильная атака баса, умноженная на то, насколько
            # громок бас на фоне трека. Салют — событие громкого места: в тихом
            # куплете та же бочка его не запускает, в припеве — запускает.
            s_b = float(lo.max()) * float(min(1.2, np.max(energy[:lo_n]))) if lo_n else 0.0
            mono = time.monotonic()
            if (c["bass"] > 0.01 and s_b > BASS_THR
                    and mono - st["last_bass"] >= BASS_GAP):
                st["last_bass"] = mono
                st["punch_hits"] += 1
                k_b = int(np.argmax(lo))
                # осколков: 100..240 при «Салют на басу» = 1 (по умолчанию 0.5 —
                # вдвое меньше), плотность добавляет
                n_b = int(c["bass"] * (100.0 + 140.0 * min(1.0, s_b)) * min(2.0, dens))
                n_b = min(n_b, 260)
                st["punch_req"] += n_b
                if n_b > 0:
                    reqs.append((np.array([k_b], np.int32), KIND_SHELL))
                    st["bass_s"] = s_b
                    st["bass_n"] = n_b

        if reqs:
            self._spark_emit(st, reqs, c, energy, onset, x0, y_base, w, H, px, now)

        self._spark_move(st, c, dt, has_sig, now, px, H, W)

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
                m = min(m, max(0, int(BG_SHARE * st.get("base_cap", cap)) - n_bg),
                        len(free))
                bands = rng.permutation(bands)[:m]
                if m == 0:
                    continue
                slots = np.array([free.pop() for _ in range(m)], np.int64)
                n_bg += m
            else:
                slots = self._spark_take(st, m, free)
                bands = bands[:len(slots)]
                m = len(slots)
                if m == 0:
                    continue
            self._spark_launch(st, slots, bands, kind, c, energy, onset,
                               x0, y_base, w, H, px, now)

    def _spark_take(self, st, m, free):
        """m слотов: сначала свободные, потом почти погасшие фоновые искры.

        Удар и салют важнее догорающего фона. Видимую искру не трогаем —
        её пропажа одним кадром заметна глазом. Может вернуть меньше m."""
        life = st["life"]
        take = min(m, len(free))
        slots = [free.pop() for _ in range(take)]
        if take < m:
            alive = np.flatnonzero((life > 0.0) & (st["kind"] == KIND_BG))
            if len(alive):
                vis = self._spark_vis(st, alive)[0]
                ok = vis < 0.03
                order = alive[ok][np.argsort(vis[ok])]
                slots += order[:m - take].tolist()
                # занятые здесь слоты помечаем сразу, чтобы следующий запрос
                # в этом же кадре не забрал их второй раз
                st["kind"][order[:m - take]] = KIND_HIT
        return np.asarray(slots, np.int64)

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
        chaos = c["chaos"]
        if kind == KIND_SHELL:
            self._spark_shell(st, idx, bands, c, energy, x0, y_base, w, H, px,
                              now, h_fig, top, spd2)
            return
        if kind == KIND_BG:
            # Фон: высота от уровня полосы, до половины фигуры.
            h = 0.5 * h_fig * (0.08 + 0.92 * np.power(e, 1.2))
            g = G_BG * px * spd2 * np.ones(m)
            amp = 0.30 + 0.45 * np.minimum(e, 1.2)
            heat = np.zeros(m)
            xs = (bands + rng.rand(m)) / nb
            vx = rng.standard_normal(m) * 12.0 * px * (1.0 + 2.0 * chaos)
        else:
            # Удар: высота от силы атаки. «Высота всплеска» поднимает потолок
            # удара над фоном, «разгон» — насколько сильный удар выстреливает
            # резче слабого.
            h_hit = min(top, h_fig * (0.5 + c["burst_h"]))
            strength = np.minimum(1.0, o / 1.2)
            xs = (bands + rng.rand(m)) / nb
            # веер удара: с хаосом шире, искры удара брызжут в стороны
            vx = rng.standard_normal(m) * 45.0 * px * (1.0 + 2.0 * chaos)
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
        # Покачивание: у каждой искры свой размах, частота и фаза — фонтан
        # колышется, как искры над костром, а не летит по линейке.
        st["sw"][idx] = chaos * (8.0 + 22.0 * rng.rand(m)) * px
        st["om"][idx] = 2.0 * np.pi * (0.5 + 1.3 * rng.rand(m))
        st["ph"][idx] = rng.rand(m) * 2.0 * np.pi
        self._spark_common(st, idx, bands, kind, amp, heat, now)
        st["vy"][idx] = -v0

    def _spark_common(self, st, idx, bands, kind, amp, heat, now, rgb=None):
        st["born"][idx] = now
        st["kind"][idx] = kind
        st["bin"][idx] = bands
        st["amp"][idx] = amp
        st["heat"][idx] = heat
        if rgb is not None:
            st["cr"][idx], st["cg"][idx], st["cb"][idx] = rgb
        else:
            for i, k in zip(idx.tolist(), np.broadcast_to(bands, idx.shape).tolist()):
                r_, g_, b_ = self._shape_rgb(int(k))
                st["cr"][i], st["cg"][i], st["cb"][i] = r_, g_, b_
        st["x"][idx] = st["x0"][idx]
        st["y"][idx] = st["y0"][idx]
        st["vxn"][idx] = st["vx"][idx]

    # ------------------------------------------------------------ салют
    def _fw_radius(self, c, s, H):
        """Радиус взрыва салюта: от силы удара и ползунка «Размер салюта»."""
        return (0.09 + 0.09 * s) * H * c["fw_size"]

    def _spark_shell(self, st, idx, bands, c, energy, x0, y_base, w, H, px,
                     now, h_fig, top, spd2):
        """Ракета салюта: взлетает от точки удара баса, наискосок к месту
        взрыва. Бас всегда слева в спектре, и салют, взрывающийся прямо над
        ним, наполовину уходил бы за край экрана. Поэтому ракета стартует
        оттуда, где ударило, а взрывается правее — там, где кольцо целиком
        помещается на экране."""
        rng = np.random
        nb = len(energy)
        s = float(np.clip(st.get("bass_s", 1.0), 0.0, 1.0))
        R = self._fw_radius(c, s, H)
        xs = (bands.astype(np.float64) + 0.5) / nb
        tx = xs + 0.05 + 0.35 * rng.rand(len(idx))
        lo_m = min(0.45, (R + 10.0) / max(1.0, w))
        tx = np.clip(tx, lo_m, 1.0 - lo_m)
        h = h_fig * (0.5 + 0.35 * s) * np.exp(rng.standard_normal(len(idx)) * 0.12)
        # кольцо целиком под верхним краем
        h = np.clip(h, 0.25 * h_fig, max(0.25 * h_fig, y_base - R - 0.04 * H))
        # Подъём за FW_RISE секунд, как у настоящей ракеты: резкий «вжух».
        # Ползунок скорости меняет его мягко, иначе на медленной скорости
        # взрыв отставал бы от удара на секунды.
        t_rise = FW_RISE / max(c["spd"], 0.25) ** 0.5
        g = 2.0 * h / (t_rise * t_rise)
        v0 = np.sqrt(2.0 * g * h)
        tup = v0 / g
        tau = 0.5
        sx = x0 + w * xs
        st["x0"][idx] = sx
        st["y0"][idx] = y_base - 1.0
        st["vx"][idx] = (x0 + w * tx - sx) / (tau * (1.0 - np.exp(-tup / tau)))
        st["v0"][idx] = v0
        st["g"][idx] = g
        st["tup"][idx] = tup
        st["drift"][idx] = 0.0
        # жизнь с запасом: ракета не догорает сама, её гасит взрыв на вершине,
        # даже если кадр подвис и проскочил момент вершины
        st["full"][idx] = tup + 1.0
        st["life"][idx] = tup + 1.0
        st["sw"][idx] = 0.0
        st["fwn"][idx] = float(st.get("bass_n", 60))
        st["fws"][idx] = s
        self._spark_common(st, idx, bands, KIND_SHELL, 0.9, 1.0, now)
        st["vy"][idx] = -v0

    def _spark_explode(self, st, c, shells, now, px, H):
        """Ракеты на вершине: гаснут, на их месте — вспышка и кольцо осколков."""
        free = list(np.flatnonzero(st["life"] <= 0.0)[::-1])
        rng = np.random
        for sh in shells.tolist():
            ex, ey = float(st["x"][sh]), float(st["y"][sh])
            s = float(st["fws"][sh])
            n = int(st["fwn"][sh])
            rgb = (float(st["cr"][sh]), float(st["cg"][sh]), float(st["cb"][sh]))
            band = int(st["bin"][sh])
            st["life"][sh] = 0.0
            st["y"][sh] = 1e9
            flash = self._spark_take(st, 1, free)
            if len(flash):
                st["x0"][flash] = ex
                st["y0"][flash] = ey
                st["vx"][flash] = 0.0
                st["v0"][flash] = 0.0
                st["k"][flash] = 1.0
                st["g"][flash] = 0.0
                st["full"][flash] = 0.22
                st["life"][flash] = 0.22
                st["tup"][flash] = 0.02
                self._spark_common(st, flash, band, KIND_FLASH, 3.0 + 2.0 * s, 1.0, now, rgb)
            idx = self._spark_take(st, n, free)
            st["punch_ok"] += len(idx)
            st["punch_drop"] += n - len(idx)
            m = len(idx)
            if m == 0:
                continue
            R = self._fw_radius(c, s, H)
            # Пион: три четверти осколков летят почти на полный радиус и
            # рисуют кольцо, четверть — внутрь, чтобы шар был не пустой.
            ring = rng.rand(m) < 0.75
            u = np.where(ring, 0.70 + 0.30 * rng.rand(m), 0.15 + 0.50 * rng.rand(m))
            th = rng.rand(m) * 2.0 * np.pi
            k = FW_DRAG * (0.85 + 0.30 * rng.rand(m))
            spd = R * k * u            # на этой скорости осколок пролетит ~R*u
            st["x0"][idx] = ex
            st["y0"][idx] = ey
            st["vx"][idx] = spd * np.cos(th)
            st["v0"][idx] = spd * np.sin(th)        # вниз — плюс
            st["k"][idx] = k
            st["g"][idx] = FW_G * H * (0.8 + 0.4 * rng.rand(m))
            full = (0.9 + 0.7 * rng.rand(m)) * (0.8 + 0.3 * c["hover"])
            st["full"][idx] = full
            st["life"][idx] = full
            st["tup"][idx] = 0.3 * full             # горит ровно, потом гаснет
            st["om"][idx] = 2.0 * np.pi * (7.0 + 6.0 * rng.rand(m))   # мерцание
            st["ph"][idx] = rng.rand(m) * 2.0 * np.pi
            # оттенок чуть гуляет от осколка к осколку: живее, чем ровная заливка
            var = (0.85 + 0.3 * rng.rand(m))[:, None] * np.asarray(rgb, np.float32)[None, :]
            var = np.clip(var, 0.0, 255.0)
            self._spark_common(st, idx, band, KIND_FW, 0.55 + 0.40 * rng.rand(m), 1.0,
                               now, (var[:, 0], var[:, 1], var[:, 2]))

    # ------------------------------------------------------------ полёт
    def _spark_move(self, st, c, dt, has_sig, now, px, H, W):
        life = st["life"]
        alive = life > 0.0
        if not alive.any():
            return
        # В тишине догорают втрое быстрее: музыка кончилась — искры тоже.
        life[alive] -= dt * (1.0 if has_sig else 4.0)
        a = np.flatnonzero(life > 0.0)
        dead = alive & (life <= 0.0)
        if a.size:
            kind = st["kind"][a]
            fw = kind >= KIND_FW
            b = a[~fw]
            if b.size:
                self._move_rise(st, b, now)
            f = a[fw]
            if f.size:
                self._move_fw(st, f, now)
            y = st["y"][a]
            dead[a[(y < -8.0) | (y > H + 10.0)]] = True
            # ракеты на вершине взрываются — и в тишине тоже: запущенный
            # салют должен догореть, а не повиснуть ракетой в небе
            sh = a[(kind == KIND_SHELL) & ((now - st["born"][a]) >= st["tup"][a])]
            sh = sh[~dead[sh]]
            if sh.size:
                self._spark_explode(st, c, sh, now, px, H)
        if dead.any():
            life[dead] = 0.0
            st["y"][dead] = 1e9

    @staticmethod
    def _move_rise(st, a, now):
        """Фон, удар, ракета: баллистика вверх, потом всплывание."""
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
        # горизонталь: начальный толчок гаснет за ~0.5 с, поверх — своё
        # покачивание, которое нарастает после вылета
        ex = np.exp(-age / 0.5)
        ramp = np.minimum(1.0, age / SWAY_RAMP)
        om, ph, sw = st["om"][a], st["ph"][a], st["sw"][a]
        st["x"][a] = (st["x0"][a] + st["vx"][a] * 0.5 * (1.0 - ex)
                      + sw * np.sin(om * age + ph) * ramp)
        st["vxn"][a] = st["vx"][a] * ex + sw * om * np.cos(om * age + ph) * ramp
        st["vyn"][a] = st["vy"][a]

    @staticmethod
    def _move_fw(st, a, now):
        """Осколки и вспышка салюта: разлёт с сопротивлением воздуха и тяжестью.

        v(t) = v0·e^(-kt) + g/k·(1 - e^(-kt)) — скорость падает к скорости
        «парашюта» g/k; положение — интеграл этого, тоже точный."""
        age = (now - st["born"][a]).astype(np.float32)
        k = np.maximum(st["k"][a], 1e-3)
        e = np.exp(-k * age)
        q = (1.0 - e) / k
        g = st["g"][a]
        st["x"][a] = st["x0"][a] + st["vx"][a] * q
        st["y"][a] = st["y0"][a] + st["v0"][a] * q + g / k * (age - q)
        st["vxn"][a] = st["vx"][a] * e
        st["vyn"][a] = st["v0"][a] * e + g / k * (1.0 - e)
        st["vy"][a] = st["vyn"][a]

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
        fw = st["kind"][a] == KIND_FW
        if fw.any():
            # догорая, осколок салюта мерцает — тем сильнее, чем ближе конец
            cr = 0.5 + 0.5 * np.sin(st["om"][a][fw] * age[fw] + st["ph"][a][fw])
            vis[fw] *= 1.0 - 0.7 * (1.0 - fo[fw]) * cr
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

        kind = st["kind"][a]
        # ореол — только в своём окне высоты полёта, края окна мягкие;
        # у салюта горит всегда, он и есть свет
        if glow > 0.02:
            e_ = max(edge, 1e-6)
            gw = np.clip((prog - (g_a - e_)) / e_, 0.0, 1.0) * \
                np.clip(((g_b + e_) - prog) / e_, 0.0, 1.0)
            # салют светится сам: ракета и вспышка — всегда, осколок — пока
            # горячий, ореол гаснет по мере того, как он остывает (и не
            # стоит лишнего: осколков сотня на каждый взрыв)
            gw = np.where(kind == KIND_FW, 1.0 - prog,
                          np.where(kind >= KIND_SHELL, 1.0, gw))
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
        # След — у тех, кто сейчас быстро летит: ударные искры вверх,
        # ракета (огненный хвост) и осколки салюта (штрихи во все стороны).
        # Копии чаще радиуса сливаются в сплошной хвост, а не в пунктир.
        spd_t = float(np.clip(S.get("spark_speed", 1.0), 0.01, 2.0))
        vxn, vyn = st["vxn"][a], st["vy"][a]
        vxn = np.where(kind >= KIND_SHELL, vxn, 0.0)   # фон и удар — след вертикальный
        sp = np.hypot(vxn, vyn)
        is_fw = kind >= KIND_SHELL
        tr = np.where(is_fw, max(trail, 0.5), trail)
        fast = np.flatnonzero(np.where(is_fw, sp > np.where(kind == KIND_FW, 90.0, 40.0),
                                       vyn < -350.0 * max(0.15, spd_t))
                              & (a_core > 0.05) & (tr > 0.01) & (kind != KIND_FLASH))
        if fast.size:
            k_len = np.where(kind[fast] == KIND_SHELL, 0.06,
                             np.where(kind[fast] == KIND_FW, 0.035, 0.012)) \
                + 0.02 * tr[fast]
            length = sp[fast] * k_len
            ux = (-vxn[fast] / np.maximum(sp[fast], 1e-3)).tolist()
            uy = (-vyn[fast] / np.maximum(sp[fast], 1e-3)).tolist()
            len_l = length.tolist()
            tr_l = tr[fast].tolist()
            cap_l = np.where(kind[fast] == KIND_FW, 3, 6).tolist()
            for j, i in enumerate(fast.tolist()):
                rc, ac, spr = rc_l[i], ac_l[i], core_spr[inv_l[i]]
                cnt = int(min(cap_l[j], max(2, len_l[j] / max(0.8 * rc, 1.0))))
                step = len_l[j] / cnt
                for k in range(1, cnt + 1):
                    q_ = k / (cnt + 1.0)
                    rg = rc * (1.0 - 0.55 * q_)
                    set_op(ac * tr_l[j] * 0.55 * (1.0 - q_))
                    xx = xs_l[i] + ux[j] * step * k
                    yy = ys_l[i] + uy[j] * step * k
                    draw(R(xx - rg, yy - rg, rg * 2.0, rg * 2.0), spr, src)
        p.setCompositionMode(mode0)
        p.setOpacity(base_op)
        p.setRenderHint(QPainter.SmoothPixmapTransform, had_sm)
