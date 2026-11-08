"""Искры: выпуск по звуку, полёт и отрисовка. Подмешивается в класс Overlay.

Как искры слушают музыку
------------------------
Источник — спектр прямо из звукового движка (self._spec_raw), без
авто-баланса и без сглаживания атаки/отпускания: сглаживание размазывает
удар на несколько кадров, а авто-баланс растягивает тихий кусок трека до
полной высоты. Из спектра core.onsets.BandOnsets делает по каждой полосе:

    energy — ровный уровень полосы относительно громкости трека;
    onset  — резкий прирост полосы прямо сейчас (удар).

Потоки частиц:

    фон    — энергия полосы. Поток и высота растут с уровнем частоты:
             тянущийся аккорд даёт спокойный ровный поток на своих частотах,
             тишина — ничего.
    удар   — атака полосы. Веер искр вылетает в тот же кадр, где пришёл
             удар, ровно из его частот: бочка слева, хэт справа. Число искр
             и высота — от силы удара.
    бас    — сильный удар баса в громком месте: плотный широкий веер из
             точки удара («Пучок на басу»).

Как искры летят
---------------
Как настоящая искра над костром: резкий старт, торможение о воздух по
экспоненте и лёгкое всплывание тёплым воздухом. Скорость меняется плавно,
без вершины и излома — искра не «упирается» в потолок, а мягко замедляется
и уплывает. Плюс у каждой свой разлёт вбок и своё покачивание (ползунок
«Хаос разлёта»), поэтому фонтан колышется, а не летит по линейке.

Яркость гаснет за всю жизнь искры, а не в её конце: свежая искра горит
белым, остывает до своего цвета, тускнеет и мельчает по пути вверх и
растворяется. Раньше искра летела на полной яркости до вершины и там
гасла за долю секунды — на глаз это выглядело как резкое исчезновение.

Положение считается по формуле от момента вылета, а не накоплением по
кадрам: не зависит ни от fps, ни от того, как часто Qt перерисовывает окно.
"""
import math
import time

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRadialGradient

from core.onsets import BandOnsets

# Постоянная торможения о воздух, с, при скорости 1.0. Чем меньше, тем
# резче старт и тем быстрее искра выходит на медленное всплывание.
TAU_BG = 0.50         # фон поднимается мягко
TAU_HIT = 0.26        # удар выстреливает
LIFE_K = 3.2          # жизнь ≈ LIFE_K × τ (к этому времени пройдено ~96% пути)
BUOY = 0.18           # доля высоты, которую искра добирает всплыванием
FADE_IN = 0.05        # с, искра разгорается (без мелькания точками)
JITTER = 0.22         # разброс высоты, σ логнормального множителя
BG_SHARE = 0.55       # фон занимает не больше этой доли буфера
BASS_THR = 0.55       # сила удара баса (атака × громкость) для пучка
BASS_GAP = 0.12       # с, не чаще одного пучка
SWAY_RAMP = 0.25      # с, покачивание нарастает после вылета, а не с места
QUIET_HOLD = 0.35     # с тишины, после которых искры начинают гаснуть быстрее

KIND_BG, KIND_HIT, KIND_BASS = 0, 1, 2

_FIELDS_F = ("x", "y", "vx", "vy", "life", "full", "x0", "y0", "v0", "tau",
             "buoy", "amp", "heat", "cr", "cg", "cb",
             # покачивание: размах, частота, фаза
             "sw", "om", "ph")


def _new_state(cap, n_bands):
    st = {k: np.zeros(cap, np.float32) for k in _FIELDS_F}
    st["born"] = np.zeros(cap, np.float64)
    st["kind"] = np.zeros(cap, np.int8)
    st["bin"] = np.zeros(cap, np.int32)
    st["tau"][:] = 1.0
    st["y"][:] = 1e9
    st.update({"cap": cap, "t": None, "seq": None, "last_raw": None,
               "det": BandOnsets(n_bands), "acc_bg": np.zeros(n_bands, np.float32),
               "acc_hit": np.zeros(n_bands, np.float32), "last_bass": -10.0,
               "last_fresh": None, "quiet": 0.0,
               "punch_hits": 0, "punch_req": 0, "punch_ok": 0, "punch_drop": 0})
    return st


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


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
        st["quiet"] = 0.0 if has_sig else st["quiet"] + dt
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
            # Фон: поток полосы ~ energy^1.3, подогнан под среднюю жизнь
            # фоновой искры так, чтобы при полной громкости по всему спектру
            # фон занимал около половины буфера. Плюс немного на каждую
            # звучащую полосу: одиночная тянущаяся нота занимает две-три
            # полосы из 64 и иначе была бы почти не видна.
            life_bg = LIFE_K * TAU_BG / max(c["spd"], 0.15) ** 0.7
            e = np.clip(energy, 0.0, 1.6)
            rate = ((0.5 * cap / life_bg) / nb * np.power(e, 1.3)
                    + 1.0 * (e > 0.12)) * dens
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
            # Бас: сильный удар в громком месте — плотный широкий веер.
            # Сила: самая сильная атака баса × громкость баса на фоне трека,
            # поэтому в тихом куплете та же бочка пучка не даёт.
            lo_n = max(2, nb // 6)
            lo = onset[:lo_n]
            s_b = float(lo.max()) * float(min(1.2, np.max(energy[:lo_n])))
            mono = time.monotonic()
            if (c["bass"] > 0.01 and s_b > BASS_THR
                    and mono - st["last_bass"] >= BASS_GAP):
                st["last_bass"] = mono
                st["punch_hits"] += 1
                n_b = int(c["bass"] * (12.0 + 36.0 * min(1.0, s_b)) * min(2.0, dens))
                st["punch_req"] += n_b
                if n_b > 0:
                    reqs.append((np.full(n_b, int(np.argmax(lo)), np.int32), KIND_BASS))
                    st["bass_s"] = min(1.0, s_b)

        if reqs:
            self._spark_emit(st, reqs, c, energy, onset, x0, y_base, w, H, px, now)

        self._spark_move(st, dt, now, H)

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
                slots = self._spark_take(st, m, free)
                if kind == KIND_BASS:
                    st["punch_ok"] += len(slots)
                    st["punch_drop"] += m - len(slots)
                bands = bands[:len(slots)]
                m = len(slots)
                if m == 0:
                    continue
            self._spark_launch(st, slots, bands, kind, c, energy, onset,
                               x0, y_base, w, H, px, now)

    def _spark_take(self, st, m, free):
        """m слотов: сначала свободные, потом почти погасшие фоновые искры.

        Удар важнее догорающего фона. Видимую искру не трогаем — её пропажа
        одним кадром заметна глазом. Может вернуть меньше m."""
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
        chaos = c["chaos"]
        spd_f = max(c["spd"], 0.15) ** 0.7
        if kind == KIND_BG:
            # Фон: высота от уровня полосы, до половины фигуры.
            h = 0.5 * h_fig * (0.08 + 0.92 * np.power(e, 1.2))
            tau = TAU_BG * np.ones(m)
            amp = 0.30 + 0.45 * np.minimum(e, 1.2)
            heat = np.zeros(m)
            xs = (bands + rng.rand(m)) / nb
            vx = rng.standard_normal(m) * 12.0 * px * (1.0 + 2.0 * chaos)
        else:
            # Удар: высота от силы атаки и от громкости полосы. «Высота
            # всплеска» поднимает потолок удара над фоном, «разгон» —
            # насколько сильный удар выстреливает резче слабого.
            h_hit = min(top, h_fig * (0.5 + c["burst_h"]))
            if kind == KIND_BASS:
                strength = np.full(m, float(st.get("bass_s", 1.0)))
                xs = (bands + 0.5 + rng.standard_normal(m) * 0.6) / nb
                # широкий веер из одной точки
                vx = rng.standard_normal(m) * 110.0 * px * (1.0 + chaos)
            else:
                strength = np.minimum(1.0, o / 1.2)
                xs = (bands + rng.rand(m)) / nb
                # веер удара: с хаосом шире, искры удара брызжут в стороны
                vx = rng.standard_normal(m) * 45.0 * px * (1.0 + 2.0 * chaos)
            h = h_hit * (0.22 + 0.78 * np.power(strength, 0.8)) \
                * (0.45 + 0.55 * np.minimum(e, 1.0))
            tau = TAU_HIT / (1.0 + 0.25 * c["boost"] * strength)
            amp = 0.55 + 0.6 * strength
            heat = 0.4 + 0.6 * strength
        # Разброс высоты логнормальный: искры одной полосы не выстраиваются
        # в линию. Потолок мягкий только у самого верха.
        h = h * np.exp(rng.standard_normal(m) * JITTER)
        knee = 0.8 * top
        over = h > knee
        if over.any():
            h[over] = knee + (top - knee) * np.tanh((h[over] - knee) / (top - knee))
        h = np.maximum(h, 6.0 * px)
        # Время торможения: от ползунка скорости и чуть случайно у каждой.
        tau = np.clip(tau / spd_f * (0.85 + 0.3 * rng.rand(m)), 0.08, 3.0)
        # Жизнь: торможение плюс «Затухание» — сколько искра ещё плывёт и
        # тлеет после того, как почти остановилась.
        full = tau * LIFE_K * (0.85 + 0.15 * c["hover"]) * (0.85 + 0.3 * rng.rand(m))
        # Путь: основная часть — выстрел с торможением (v0·τ), остаток —
        # всплывание за всю жизнь. Вместе ровно h к концу жизни.
        main = (1.0 - BUOY) * h / (1.0 - math.exp(-LIFE_K))
        st["x0"][idx] = x0 + w * np.clip(xs, 0.0, 1.0)
        st["y0"][idx] = y_base - 1.0
        st["vx"][idx] = vx
        st["v0"][idx] = main / tau
        st["tau"][idx] = tau
        st["buoy"][idx] = BUOY * h / full
        st["full"][idx] = full
        st["life"][idx] = full
        # Покачивание: у каждой искры свой размах, частота и фаза — фонтан
        # колышется, как искры над костром, а не летит по линейке.
        st["sw"][idx] = chaos * (8.0 + 22.0 * rng.rand(m)) * px
        st["om"][idx] = 2.0 * np.pi * (0.5 + 1.3 * rng.rand(m))
        st["ph"][idx] = rng.rand(m) * 2.0 * np.pi
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
        st["vy"][idx] = -st["v0"][idx]

    # ------------------------------------------------------------ полёт
    def _spark_move(self, st, dt, now, H):
        life = st["life"]
        alive = life > 0.0
        if not alive.any():
            return
        # Короткие паузы в музыке (между нотами, брейк на долю такта) искры
        # не трогают: раньше любая просадка звука ниже порога вчетверо
        # ускоряла угасание, и фонтан посреди трека разом «сдувался».
        # Только если тихо дольше QUIET_HOLD, догорают быстрее — до 2×.
        q = float(np.clip((st["quiet"] - QUIET_HOLD) / 0.5, 0.0, 1.0))
        life[alive] -= dt * (1.0 + q)
        a = np.flatnonzero(life > 0.0)
        dead = alive & (life <= 0.0)
        if a.size:
            age = (now - st["born"][a]).astype(np.float32)
            tau = st["tau"][a]
            ex = np.exp(-age / tau)
            # выстрел с торможением о воздух + ровное всплывание:
            # y = y0 - v0·τ·(1 - e^(-t/τ)) - b·t,  v = -(v0·e^(-t/τ) + b)
            y = st["y0"][a] - st["v0"][a] * tau * (1.0 - ex) - st["buoy"][a] * age
            st["y"][a] = y
            st["vy"][a] = -(st["v0"][a] * ex + st["buoy"][a])
            # горизонталь: толчок гаснет за ~0.5 с, поверх — покачивание,
            # которое нарастает после вылета
            ramp = np.minimum(1.0, age / SWAY_RAMP)
            om, ph, sw = st["om"][a], st["ph"][a], st["sw"][a]
            st["x"][a] = (st["x0"][a] + st["vx"][a] * 0.5 * (1.0 - np.exp(-age / 0.5))
                          + sw * np.sin(om * age + ph) * ramp)
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

        Ядра и след рисуются сложением света (CompositionMode_Plus): где
        искр много, середина раскаляется к белому, а не перекрывается
        кругами. Ореолы — обычным наложением."""
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
        """Видимость искр a (0..1) и её части: разгорание, угасание, возраст,
        доля прожитого. Одна формула и для рисунка, и для выбора, какую
        искру можно незаметно заменить.

        Угасание идёт через всю жизнь: короткое плато в начале, потом ровный
        S-образный спад до нуля. Искра тускнеет по пути, а не гаснет разом."""
        life = st["life"][a]
        full = np.maximum(st["full"][a], 0.01)
        age = np.maximum(full - life, 0.0)
        u = np.clip(age / full, 0.0, 1.0)            # 0 — вылет, 1 — конец
        fin = _smooth(age / FADE_IN)
        fo = 1.0 - _smooth((u - 0.12) / 0.88)
        ef = np.clip((st["y"][a] + 8.0) / 60.0, 0.0, 1.0)   # у верхнего края гаснет
        vis = (0.45 + 0.55 * np.minimum(st["amp"][a], 1.0)) * fin * fo * ef
        return vis, fin, fo, age, u

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
        # мельчает вместе с яркостью, но не до точки: тающая искра остаётся
        # мягким пятнышком, которое растворяется
        rad = (1.1 + 4.6 * amp * sz) * (0.45 + 0.55 * np.sqrt(fo)) * (0.6 + 0.4 * fin)
        rad = np.maximum(rad, 0.9)
        ys = st["y"][a]
        xs = st["x"][a]
        a_core = np.clip(alpha / 255.0 * vis, 0.0, 1.0)

        pc = np.stack((st["cr"][a], st["cg"][a], st["cb"][a]), axis=1).astype(np.float32)
        pc = pc * (0.75 + 0.45 * np.minimum(amp, 1.0))[:, None]
        # свежая искра удара горит белым и остывает до своего цвета
        hot = st["heat"][a] * np.exp(-age / 0.22)
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

        # ореол — только в своём окне жизни искры, края окна мягкие
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
        # Ядра и след — сложением света. Ореолы выше — обычным наложением:
        # сложение для больших спрайтов в Qt вдвое дороже, а мягкий
        # гауссов ореол и так не даёт колец.
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for i in range(len(xs_l)):
            ac = ac_l[i]
            if ac < 0.004:
                continue
            rc = rc_l[i]
            set_op(ac)
            draw(R(xs_l[i] - rc, ys_l[i] - rc, rc * 2.0, rc * 2.0), core_spr[inv_l[i]], src)
        # След — у тех, кто сейчас быстро летит вверх. Скорость падает
        # плавно, поэтому и след укорачивается плавно, а не обрывается.
        # Копии чаще радиуса сливаются в сплошной хвост, а не в пунктир.
        if trail > 0.01:
            spd_t = float(np.clip(S.get("spark_speed", 1.0), 0.01, 2.0))
            vy = st["vy"][a]
            v_min = 250.0 * max(0.15, spd_t)
            fast = np.flatnonzero((vy < -v_min) & (a_core > 0.03))
            if fast.size:
                vy_l = vy[fast].tolist()
                for j, i in enumerate(fast.tolist()):
                    rc, ac, spr = rc_l[i], ac_l[i], core_spr[inv_l[i]]
                    sp = -vy_l[j]
                    length = sp * (0.012 + 0.020 * trail)
                    # хвост проявляется по мере того, как скорость выше порога
                    k_t = min(1.0, (sp - v_min) / v_min)
                    cnt = int(min(5, max(2, length / max(0.8 * rc, 1.0))))
                    step = length / cnt
                    for k in range(1, cnt + 1):
                        q_ = k / (cnt + 1.0)
                        rg = rc * (1.0 - 0.55 * q_)
                        set_op(ac * trail * 0.55 * k_t * (1.0 - q_))
                        yy = ys_l[i] + step * k
                        draw(R(xs_l[i] - rg, yy - rg, rg * 2.0, rg * 2.0), spr, src)
        p.setCompositionMode(mode0)
        p.setOpacity(base_op)
        p.setRenderHint(QPainter.SmoothPixmapTransform, had_sm)
