"""Общий цвет с экрана: сочный-доминантный (как оверлей) + FX.

Работает без Qt-зависимостей внутри (numpy), сэмплинг делает Overlay через grabWindow
и кладёт сюда сырой кадр; этот модуль считает цель и сглаживает.
Так и оверлей, и RGB-ПК получают ОДИН И ТОТ ЖЕ цвет — требование 'ПК как визуализатор'.
"""
import colorsys
import threading
import time

import numpy as np


def _rgb_hue_np(px):
    """Оттенки 0..1 для массива пикселей Nx3 (float 0..1)."""
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-9
    rr = (mx == r) & m
    gg = (mx == g) & m & ~rr
    bb = m & ~rr & ~gg
    h[rr] = ((((g[rr] - b[rr]) / d[rr]) % 6.0)) / 6.0
    h[gg] = (((b[gg] - r[gg]) / d[gg]) + 2.0) / 6.0
    h[bb] = (((r[bb] - g[bb]) / d[bb]) + 4.0) / 6.0
    return h


def _center_weights(h, w):
    """Вес пикселя по положению: центр важнее краёв (видео в центре,
    края — таскбар/рамки). 1.0 в центре → ~0.2 в углах, итог 0.35..1.0."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    gx = (xx / max(1, w - 1) - 0.5) * 2.0
    gy = (yy / max(1, h - 1) - 0.5) * 2.0
    return (0.35 + 0.65 * np.exp(-(gx * gx + gy * gy) / 1.2)).reshape(-1).astype(np.float32)


def _to_rgb255(v):
    v = (np.clip(np.asarray(v, dtype=np.float32).reshape(-1), 0, 1) * 255).astype(int)
    return (int(v[0]), int(v[1]), int(v[2]))


def _lift_dim(target):
    """Тусклый средний тон тёмного кадра поднимаем до видимого (V~0.35),
    оттенок сохраняем. Свет из ничего не делаем."""
    t = np.asarray(target, dtype=np.float32)
    mx = float(t.max())
    if 8.0 / 255.0 <= mx < 90.0 / 255.0:
        t = t * (90.0 / 255.0 / mx)
    return t


def _dim_fallback(flat, val, cw):
    """Нейтральный тон тёмного/серого кадра: взвешенное среднее + подъём."""
    vw = np.clip(val, 0, 1) * cw
    if float(vw.sum()) > 1e-6:
        t = (flat * vw[:, None]).sum(axis=0) / vw.sum()
    else:
        t = np.array([0.06, 0.06, 0.06], dtype=np.float32)
    return _lift_dim(t)


def average_target(arr01):
    """Плавный средний цвет: взвешенное среднее кадра, яркое и насыщенное
    влияет сильнее. Переходы мягкие, но при двух сильных цветах даёт
    промежуточный оттенок, которого нет на экране."""
    a = np.asarray(arr01, dtype=np.float32)
    if a.ndim != 3 or a.shape[2] != 3:
        return (0, 0, 0)
    h, w = int(a.shape[0]), int(a.shape[1])
    flat = a.reshape(-1, 3)
    mx = flat.max(axis=1)
    mn = flat.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    val = np.clip(mx, 0, 1)
    cw = _center_weights(h, w)
    w = (sat * val + 0.05) * val * cw
    if float(w.sum()) <= 1e-6:
        return (15, 15, 15)
    return _to_rgb255((flat * w[:, None]).sum(axis=0) / w.sum())


def kmeans_target(arr01, k=3, iters=6, lock=None):
    """K-means: делит пиксели на k кластеров в RGB, возвращает центр самого
    весомого. Сиды — из прошлого кадра (не мигает), мелкий кластер-примесь
    (<8% веса) отбрасывается в нейтральный тон. На смешанных сценах точнее
    среднего, чуть тяжелее."""
    a = np.asarray(arr01, dtype=np.float32)
    if a.ndim != 3 or a.shape[2] != 3:
        return (0, 0, 0)
    h, w = int(a.shape[0]), int(a.shape[1])
    flat = a.reshape(-1, 3)
    mx = flat.max(axis=1)
    mn = flat.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    val = np.clip(mx, 0, 1)
    cw = _center_weights(h, w)
    m = (sat > 0.22) & (val > 0.18)
    if int(m.sum()) <= max(60, k * 4):
        return _to_rgb255(_dim_fallback(flat, val, cw))
    pts = flat[m]
    w = (sat[m] ** 1.5) * val[m] * cw[m]
    n = pts.shape[0]
    k = max(1, min(int(k), n))
    centers = None
    if lock is not None:
        prev = lock.get("km")
        if (isinstance(prev, np.ndarray) and prev.shape == (k, 3)
                and bool(np.isfinite(prev).all())):
            centers = prev.astype(np.float64)
    if centers is None:
        idx = np.linspace(0, n - 1, k).astype(int)  # детерминированные сиды
        centers = pts[idx].astype(np.float64)
    for _ in range(max(1, int(iters))):
        d2 = ((pts[None, :, :] - centers[:, None, :]) ** 2).sum(axis=2)
        assign = np.argmin(d2, axis=0)
        new = centers.copy()
        for j in range(k):
            jm = assign == j
            if jm.sum() and float(w[jm].sum()) > 1e-9:
                new[j] = (pts[jm] * w[jm][:, None]).sum(axis=0) / w[jm].sum()
        if np.allclose(new, centers):
            centers = new
            break
        centers = new
    d2 = ((pts[None, :, :] - centers[:, None, :]) ** 2).sum(axis=2)
    assign = np.argmin(d2, axis=0)
    cwsum = np.array([w[assign == j].sum() for j in range(k)])
    win = int(np.argmax(cwsum))
    # угон мелочью запрещён: кластер-победитель должен занимать >=4%
    # площади кадра, иначе это иконка/шум — в нейтральный тон
    if int((assign == win).sum()) < max(8, int(flat.shape[0] * 0.04)):
        return _to_rgb255(_dim_fallback(flat, val, cw))
    if lock is not None:
        lock["km"] = centers.copy()
    return _to_rgb255(centers[win])


def juicy_target(arr01, lock=None, hold=2):
    """Сочный-доминантный цвет кадра (v2).

    Голосуют только живые пиксели (пороги sat/val), вес = насыщенность ×
    яркость × центр кадра (видео обычно в центре, края — таскбар/рамки).
    Побеждает сглаженная hue-гистограмма (36 бинов) — фантомных средних
    оттенков, которых нет на экране, не возникает. Временно — гистерезис
    через lock: переключение, только если новый оттенок уверенно держится
    `hold` замеров подряд. Ахромат (серый/чёрный) возвращается как есть,
    без подкраски."""
    a = np.asarray(arr01, dtype=np.float32)
    if a.ndim != 3 or a.shape[2] != 3:
        return (0, 0, 0)
    h, w = int(a.shape[0]), int(a.shape[1])
    flat = a.reshape(-1, 3)
    mx = flat.max(axis=1)
    mn = flat.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    val = mx
    # центр кадра важнее краёв: 1.0 в центре → ~0.2 в углах
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    gx = (xx / max(1, w - 1) - 0.5) * 2.0
    gy = (yy / max(1, h - 1) - 0.5) * 2.0
    cw = (0.35 + 0.65 * np.exp(-(gx * gx + gy * gy) / 1.2)).reshape(-1).astype(np.float32)
    m = (sat > 0.22) & (val > 0.18)
    vivid_ok = False
    if int(m.sum()) > 60:
        sel = flat[m]
        sat_m = sat[m]
        w = (sat[m] ** 1.5) * np.clip(val[m], 0, 1) * cw[m]
        hues = _rgb_hue_np(sel)
        nb = 36
        hist_raw, _ = np.histogram(hues, bins=nb, range=(0.0, 1.0), weights=w)
        wsum = float(hist_raw.sum())
        # угон мелочью запрещён: победитель должен держать >=6% живого веса,
        # иначе это иконка/шум на тёмной странице — идём в нейтральный тон
        if wsum > 1e-9 and float(hist_raw.max()) / wsum >= 0.06:
            vivid_ok = True
    if vivid_ok:
        hist = hist_raw.astype(np.float64)
        hist = 0.5 * hist + 0.25 * (np.roll(hist, 1) + np.roll(hist, -1))
        win = int(np.argmax(hist))
        bw = 1.0 / nb
        lo, hi = win * bw, (win + 1) * bw
        if win == 0:  # красный на границе 0/1 — добрать хвост с другой стороны
            inb = (hues < hi) | (hues >= 1.0 - bw)
        elif win == nb - 1:
            inb = (hues >= lo) | (hues < bw)
        else:
            inb = (hues >= lo) & (hues < hi)
        # угон мелочью запрещён-2: победитель должен занимать >=4% площади
        # кадра, иначе это иконка/шум на тёмной странице — в нейтральный тон
        if int(inb.sum()) < max(8, int(flat.shape[0] * 0.04)):
            vivid_ok = False
    if vivid_ok:
        if lock is not None:
            cur = lock.get("bin")
            hold_n = max(1, int(hold))
            if cur is not None and 0 <= cur < nb and cur != win:
                if hist[win] > hist[cur] * 1.2:
                    lock["n"] = int(lock.get("n", 0)) + 1
                    if lock["n"] >= hold_n:
                        lock["bin"] = win
                        lock["n"] = 0
                    else:
                        win = cur
                else:
                    win = cur
                    lock["n"] = 0
            else:
                lock["bin"] = win
                lock["n"] = 0
        bw = 1.0 / nb
        lo, hi = win * bw, (win + 1) * bw
        if win == 0:
            inb = (hues < hi) | (hues >= 1.0 - bw)
        elif win == nb - 1:
            inb = (hues >= lo) | (hues < bw)
        else:
            inb = (hues >= lo) & (hues < hi)
        if int(inb.sum()) < 8:
            inb = np.ones_like(hues, dtype=bool)
        psel, s_in, wsel = sel[inb], sat_m[inb], w[inb]
        if len(wsel) > 16:
            # сочное ядро: усредняем только самую насыщенную половину веса
            # бина — вымытый хвост (дымка, jpeg-муть, полутона) белит результат
            o = np.argsort(s_in)[::-1]
            cum = np.cumsum(wsel[o])
            take = max(8, int(np.searchsorted(cum, cum[-1] * 0.5)))
            keep = o[:take]
            psel, wsel = psel[keep], wsel[keep]
        target = (psel * wsel[:, None]).sum(axis=0) / max(1e-6, wsel.sum())
    else:
        if lock is not None:
            lock["bin"] = None
            lock["n"] = 0
        target = _dim_fallback(flat, val, cw)  # тёмный/серый экран — видимый средний тон
    target = (np.clip(target, 0, 1) * 255).astype(int)
    return (int(target[0]), int(target[1]), int(target[2]))


class ScreenColor:
    """Потокобезопасное хранилище цвета. feed_frame() зовётся из GUI-потока 2 раза/сек."""

    def __init__(self, cfg_getter, initial=(0, 200, 255)):
        self._cfg = cfg_getter
        self.lock = threading.Lock()
        self.raw = np.array(initial, dtype=np.float32)      # сырая цель с экрана
        self.shown = np.array(initial, dtype=np.float32)    # после FX (линия + ПК)
        self.custom = np.array(initial, dtype=np.float32)   # для палитры баров
        self._lock = {"bin": None, "n": 0}  # гистерезис победившего оттенка
        self._t0 = time.time()
        self._breathe = 1.0
        self._beat_boost = 1.0
        self.bass = 0.0

    def feed_frame(self, arr01):
        try:
            cfg0 = self._cfg()
            hold = cfg0.get("color_hold", 1)
            algo = (cfg0.get("color_algo", "juicy") or "juicy")
            if self._lock.get("algo") != algo:  # смена алгоритма — сброс гистерезиса
                self._lock = {"bin": None, "n": 0, "algo": algo}
            if algo == "average":
                tgt = average_target(arr01)
            elif algo == "kmeans":
                tgt = kmeans_target(arr01, lock=self._lock)
            else:
                tgt = juicy_target(arr01, self._lock, hold)
        except Exception:
            return
        cfg = self._cfg()
        a = float(np.clip(cfg.get("auto_speed", 0.6), 0.05, 1.0))
        with self.lock:
            tgt_a = np.array(tgt, dtype=np.float32)
            if float(tgt_a.max()) < float(self.raw.max()):
                a *= 0.4  # гаснем медленнее, чем вспыхиваем — не мигаем на тёмном
            self.raw += (tgt_a - self.raw) * a

    def tick(self, custom_color):
        """Вызывать каждый кадр оверлея/RGB. Возвращает (fill_rgb, custom_rgb, breathe, beat_boost)."""
        cfg = self._cfg()
        fx = cfg.get("color_fx", "smooth")
        k = float(np.clip(cfg.get("color_speed", 0.12), 0.02, 0.5))
        follow = bool(cfg.get("follow_screen", True))
        with self.lock:
            base = self.raw.copy() if follow else np.array(cfg.get("fill_color", [0, 200, 255]), dtype=np.float32)
        tgt_cust = np.array(custom_color, dtype=np.float32)
        if fx == "instant":
            self.shown = base.copy()
            self.custom = tgt_cust.copy()
            self._breathe, self._beat_boost = 1.0, 1.0
        elif fx == "rainbow":
            t = time.time() - self._t0
            h = (t * 0.06 * (0.3 + k * 6)) % 1.0
            r, g, b = colorsys.hsv_to_rgb(h, 0.85, 1.0)
            tgt = np.array([r * 255, g * 255, b * 255], dtype=np.float32)
            self.shown += (tgt - self.shown) * min(1.0, k * 2)
            self.custom = self.shown.copy()
            self._breathe, self._beat_boost = 1.0, 1.0
        elif fx == "breathe":
            self.shown += (base - self.shown) * k
            self.custom += (tgt_cust - self.custom) * k
            t = time.time() - self._t0
            self._breathe = 0.62 + 0.38 * (0.5 + 0.5 * float(np.sin(2 * np.pi * t / 3.0)))
            self._beat_boost = 1.0
        elif fx == "beat":
            self.shown += (base - self.shown) * k
            self.custom += (tgt_cust - self.custom) * k
            self._breathe = 1.0
            self._beat_boost = 1.0 + min(1.0, float(self.bass)) * 0.9
        else:
            self.shown += (base - self.shown) * k
            self.custom += (tgt_cust - self.custom) * k
            self._breathe, self._beat_boost = 1.0, 1.0
        return (tuple(int(v) for v in np.clip(self.shown, 0, 255)),
                tuple(int(v) for v in np.clip(self.custom, 0, 255)),
                float(self._breathe), float(self._beat_boost))
