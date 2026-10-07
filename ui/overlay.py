"""Прозрачный click-through оверлей AuraFusion (smooth/bars/circle/combo)."""
import ctypes
import math
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget


def soft_cap(v, cap, ratio=0.3):
    cap = float(np.clip(cap, 0.1, 1.0))
    if cap >= 1.0:
        return v
    over = v - cap
    return np.where(over > 0, cap + over * ratio, v).astype(np.float32)


class SysFX:
    """Приближение/тряска ВСЕГО экрана через Magnification API (системная лупа).

    Единственный способ гнуть настоящий экран на Windows (оверлей рисует
    только поверх). Не работает в Exclusive Fullscreen. ВАЖНО: при выходе
    обязательно close()/reset(), иначе экран останется приближённым."""

    def __init__(self):
        self.ok = False
        self.active = False
        try:
            self.mag = ctypes.windll.magnification
            self.ok = bool(self.mag.MagInitialize())
        except Exception as e:
            print(f"[sysfx] init: {e}")
        if not self.ok:
            print("[sysfx] Magnification unavailable")

    def set(self, mag: float, ox: int, oy: int) -> bool:
        if not self.ok:
            return False
        try:
            return bool(self.mag.MagSetFullscreenTransform(ctypes.c_float(mag), int(ox), int(oy)))
        except Exception:
            return False

    def reset(self):
        self.set(1.0, 0, 0)
        self.active = False

    def close(self):
        try:
            self.reset()
            if self.ok:
                self.mag.MagUninitialize()
        except Exception:
            pass
        self.ok = False


REGION_PRESETS = {
    "full": (0.0, 0.0, 1.0, 1.0),
    "center": (0.25, 0.25, 0.5, 0.5),
    "top": (0.0, 0.0, 1.0, 0.35),
    "bottom": (0.0, 0.65, 1.0, 0.35),
}


def resolve_region(cfg):
    """Область замера цвета как доли экрана (rx, ry, rw, rh)."""
    key = (cfg.get("color_region", "full") or "full")
    if key == "custom":
        try:
            rx = float(cfg.get("color_rx", 0.0))
            ry = float(cfg.get("color_ry", 0.0))
            rw = float(cfg.get("color_rw", 1.0))
            rh = float(cfg.get("color_rh", 1.0))
        except Exception:
            return (0.0, 0.0, 1.0, 1.0)
    else:
        rx, ry, rw, rh = REGION_PRESETS.get(key, REGION_PRESETS["full"])
    rx = min(max(0.0, rx), 0.9)
    ry = min(max(0.0, ry), 0.9)
    rw = min(max(0.1, rw), 1.0 - rx)
    rh = min(max(0.1, rh), 1.0 - ry)
    return (rx, ry, rw, rh)


def qimage_to_sample(img, region, out_w=96, out_h=54):
    """QImage -> сэмпл для детектора: кроп области в точных долях numpy
    (Qt-координаты copy() врут при масштабе экрана 125/150%),
    потом билинейно к out_w x out_h. Возвращает float32 0..1 или None."""
    import numpy as _np
    from PIL import Image as _Image
    w, h = int(img.width()), int(img.height())
    if w < 8 or h < 8:
        return None
    bpl = int(img.bytesPerLine())
    raw = _np.frombuffer(img.bits(), dtype=_np.uint8, count=h * bpl)
    if int(raw.size) < h * bpl:
        return None
    big = raw.reshape(h, bpl)[:, :w * 3].reshape(h, w, 3)
    rx, ry, rw, rh = region
    x0 = min(max(0, int(w * rx)), w - 2)
    y0 = min(max(0, int(h * ry)), h - 2)
    x1 = min(w, max(x0 + 2, int(w * (rx + rw))))
    y1 = min(h, max(y0 + 2, int(h * (ry + rh))))
    crop = big[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    small = _np.asarray(_Image.fromarray(crop).resize(
        (out_w, out_h), _Image.BILINEAR)).astype(_np.float32) / 255.0
    if small.shape != (out_h, out_w, 3):
        return None
    return small


GRADIENTS = {
    "neon": [(0, 255, 200), (0, 150, 255), (180, 80, 255)],
    "green": [(30, 255, 120), (0, 200, 120), (0, 120, 80)],
    "fire": [(255, 220, 80), (255, 120, 30), (255, 40, 60)],
    "white": [(255, 255, 255), (200, 200, 200), (140, 140, 140)],
}


def mix_color(c1, c2, t):
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def build_palette(mode, custom, n):
    if mode == "custom":
        base = tuple(custom)
        dark = (max(0, base[0] // 4), max(0, base[1] // 4), max(0, base[2] // 4))
        return [mix_color(dark, base, i / max(1, n - 1)) for i in range(n)]
    g = GRADIENTS.get(mode, GRADIENTS["neon"])
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        out.append(mix_color(g[0], g[1], t * 2) if t < 0.5
                   else mix_color(g[1], g[2], (t - 0.5) * 2))
    return out


class Overlay(QWidget):
    def __init__(self, cfg_getter, audio, screencolor):
        super().__init__()
        self._cfg = cfg_getter
        self.audio = audio
        self.sc = screencolor
        cfg = self._cfg()
        self.disp = np.zeros(cfg.get("bars", 64), dtype=np.float32)
        self.palette = build_palette(cfg.get("color_mode", "neon"),
                                     tuple(cfg.get("custom_color", [0, 220, 255])),
                                     cfg.get("bars", 64))
        self._last_bars = cfg.get("bars", 64)
        self._last_color = (cfg.get("color_mode"), tuple(cfg.get("custom_color", [0, 220, 255])))
        self._bass = 0.0
        self._bpeak = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self._silence = 0.0
        self._hide_k = 1.0
        self._last_eff_op = -1.0
        self._wave = np.zeros(1024, dtype=np.float32)
        self._level = 0.0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.apply_fps()
        self._panel_geom = None  # (x, y, w, h) панели в координатах экрана, ставит main
        self._region_flash_until = 0.0  # до какого момента рисовать рамку области захвата
        self._last_region = None  # последний виденный набор параметров области
        self.sysfx = SysFX()  # лупа всего экрана (Zoom+тряска через Magnification API)
        self.color_timer = QTimer(self)
        self.color_timer.timeout.connect(self.sample_screen_color)
        self.color_timer.start(500)

    def apply_fps(self):
        self.timer.start(max(8, int(1000 / max(1, self._cfg().get("overlay_fps", 60)))))

    def apply_opacity(self):
        try:
            self.setWindowOpacity(float(np.clip(self._cfg().get("overlay_opacity", 1.0), 0.1, 1.0)))
        except Exception:
            pass

    def move_to_monitor(self, idx):
        try:
            screens = QApplication.screens()
            if 0 <= idx < len(screens):
                self.setGeometry(screens[idx].geometry())
        except Exception:
            pass

    def set_click_through(self, on: bool):
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
            u32 = ctypes.windll.user32
            style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if on else ((style & ~WS_EX_TRANSPARENT) | WS_EX_LAYERED)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception as e:
            print(f"[overlay] click-through: {e}")

    # --- цвет с экрана: один сэмпл кормит и оверлей, и ПК ---
    def sample_screen_color(self):
        cfg = self._cfg()
        if not cfg.get("follow_screen", True):
            return
        try:
            from PySide6.QtGui import QImage
            screens = QApplication.screens()
            want = int(cfg.get("color_monitor", -1))
            if 0 <= want < len(screens):
                screen = screens[want]
            else:
                screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            rx, ry, rw, rh = resolve_region(cfg)
            # рабочая копия покрупнее + кроп области в точных долях numpy
            # (Qt-координаты copy() врут при масштабе экрана 125/150%)
            pix = screen.grabWindow(0).scaled(480, 270, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            img = pix.toImage().convertToFormat(QImage.Format_RGB888)
            arr = qimage_to_sample(img, (rx, ry, rw, rh))
            if arr is None:
                return
            w, h = 96, 54
            if float(arr.max()) <= 0.01:
                return  # почти чёрный кадр (exclusive fullscreen, выкл. экран) —
                # замера нет, держим последний цвет вместо нырка в чёрный
            if cfg.get("exclude_viz", True):
                self._blackout_viz(arr, w, h, cfg, screen.geometry(), (rx, ry, rw, rh))
                if float(arr.max()) <= 0.01:
                    return  # после вырезания волны ничего не осталось — замер врёт
            self.sc.feed_frame(arr)
        except Exception:
            pass

    def _blackout_viz(self, arr, w, h, cfg, scr_geo, region=(0.0, 0.0, 1.0, 1.0)):
        """Хирургическое удаление САМОЙ волны из кадра (не пол-экрана!):
        1) тонкая полоса базовой линии, 2) адаптивная высота по живому спектру
        (тихо = почти ничего не режем), 3) окно панели. Гашеное = чёрное,
        сочный алгоритм чёрное игнорирует.
        Без цветового ключа по sc.shown: он вырезал настоящий цвет экрана,
        когда тот совпадал с текущим, и побеждал чужой оттенок.
        region — область замера (rx, ry, rw, rh): геометрия волны задана
        в координатах экрана, пересчитываем её в координаты сэмпла."""
        try:
            rx, ry, rw, rh = region
            rw = max(0.05, float(rw))
            rh = max(0.05, float(rh))

            def _mx(fx0, fx1):
                return ((fx0 - rx) / rw, (fx1 - rx) / rw)

            def _my(fy0, fy1):
                return ((fy0 - ry) / rh, (fy1 - ry) / rh)
            x0 = float(cfg.get("viz_x", 0.5)) - float(cfg.get("viz_w", 0.92)) / 2
            x1 = x0 + float(cfg.get("viz_w", 0.92))
            yb = float(cfg.get("viz_y", 0.93))
            yy, xx = np.mgrid[0:h, 0:w]
            fx, fy = xx / w, yy / h
            # 1) базовая линия рисуется всегда
            mx0, mx1 = _mx(x0, x1)
            my0, my1 = _my(yb - 0.03, yb + 0.03)
            arr[(fx >= mx0) & (fx <= mx1) & (fy >= my0) & (fy <= my1)] = 0.0
            # 2) высота волны по живому спектру
            try:
                peak = float(np.max(self.disp)) if len(self.disp) else 0.0
            except Exception:
                peak = 0.0
            hs = float(cfg.get("height_scale", 0.55))
            pulse = 1.0
            try:
                if cfg.get("beat_pulse"):
                    pulse = 1.0 + float(getattr(self, "_bass", 0.0)) * float(cfg.get("beat_strength", 0.35)) * 1.5
            except Exception:
                pass
            if cfg.get("style", "smooth") == "circle":
                top = yb - 0.55
            else:
                top = yb - min(0.55, peak * 0.38 * hs * 2.0 * pulse + 0.10)
            # ниже базы волна ничего не рисует (кроме зеркала/круга) — не режем
            style = cfg.get("style", "smooth")
            bot = yb + (0.45 if (cfg.get("mirror") or style == "circle") else 0.01)
            # небольшой запас на сглаживание/тряску линии (без цветового ключа)
            zx0, zx1 = max(0.0, x0 - 0.01), min(1.0, x1 + 0.01)
            ztop, zbot = max(0.0, top - 0.02), min(1.05, bot + 0.02)
            qx0, qx1 = _mx(zx0, zx1)
            qy0, qy1 = _my(ztop, zbot)
            arr[(fx >= qx0) & (fx <= qx1) & (fy >= qy0) & (fy <= qy1)] = 0.0
            # окно панели тоже не голосует (координаты — относительно замеряемого экрана)
            pg = getattr(self, "_panel_geom", None)
            if pg is not None and scr_geo is not None and scr_geo.width() > 0 and scr_geo.height() > 0:
                ix0, iy0 = max(pg[0], scr_geo.left()), max(pg[1], scr_geo.top())
                ix1 = min(pg[0] + pg[2], scr_geo.left() + scr_geo.width())
                iy1 = min(pg[1] + pg[3], scr_geo.top() + scr_geo.height())
                if ix1 > ix0 and iy1 > iy0:
                    px0, px1 = (ix0 - scr_geo.left()) / scr_geo.width(), (ix1 - scr_geo.left()) / scr_geo.width()
                    py0, py1 = (iy0 - scr_geo.top()) / scr_geo.height(), (iy1 - scr_geo.top()) / scr_geo.height()
                    qx0, qx1 = _mx(px0, px1)
                    qy0, qy1 = _my(py0, py1)
                    arr[(fx >= qx0) & (fx <= qx1) & (fy >= qy0) & (fy <= qy1)] = 0.0
        except Exception:
            pass

    def update_sysfx(self, S, has_sig, demo):
        """SYS-приближение/тряска всего экрана через Magnification API.
        DWM-трансформ — дорогая операция: в тишине сбрасываем 1:1 один раз
        и молчим, иначе зовём API не чаще 30 Гц и только при изменении."""
        if not S.get("sys_fx"):
            if getattr(self.sysfx, "active", False):
                self.sysfx.reset()
                self._sys_last_sent = None
            return
        if not self.sysfx.ok:
            return
        bass = min(1.0, float(getattr(self, "_bass", 0.0))) if (has_sig or demo) else 0.0
        if not (has_sig or demo):
            self._bass = 0.0
        if bass < 0.02:  # тишина — вернуть 1:1 один раз, дальше не дёргать DWM
            if getattr(self.sysfx, "active", False):
                self.sysfx.reset()
                self._sys_last_sent = None
            self._sys_mag = 1.0
            return
        zm = float(np.clip(S.get("sys_zoom", 0.5), 0.0, 1.0))
        shk = float(np.clip(S.get("sys_shake", 0.5), 0.0, 1.0))
        mode = S.get("sys_mode", "center")
        if mode not in ("center", "corners"):
            mode = "center"  # сужение убрано — резало глаза
        # лупа всегда на мониторе оверлея (выбора нет — трансформ всё равно общий)
        mx0, my0 = 0.0, 0.0
        W, H = max(1, self.width()), max(1, self.height())
        zrange = 0.25
        t_mag = 1.0 + bass * zm * zrange
        cur_mag = float(getattr(self, "_sys_mag", 1.0))
        k = 0.08 if t_mag >= cur_mag else 0.35
        mag = cur_mag + (t_mag - cur_mag) * k
        self._sys_mag = mag
        if mode == "corners":
            # режим "кидание по углам": база 0 + сглаженная тряска
            bx, by = mx0, my0
            fx = W * (mag - 1.0) / max(mag, 1e-3)
            fy = H * (mag - 1.0) / max(mag, 1e-3)
            b = min(1.0, bass * 1.2)
            raw_ox = mx0 - 0.5 * fx + 2.0 * fx * b
            raw_oy = my0 - 0.5 * fy + 2.0 * fy * b
            ck = 0.4
        else:
            bx = mx0 + (mag - 1.0) * W / (2.0 * mag)
            by = my0 + (mag - 1.0) * H / (2.0 * mag)
            t = time.time()
            mox = max((mag - 1.0) * W * 0.5, 8.0)
            moy = max((mag - 1.0) * H * 0.5, 8.0)
            raw_ox = (np.sin(t * 5.1) + 0.6 * np.sin(t * 9.7 + 1.3)) * 0.5 * bass * shk * mox
            raw_oy = (np.sin(t * 6.3 + 0.7) + 0.6 * np.sin(t * 11.1)) * 0.5 * bass * shk * moy
            ck = 0.25
        cx = float(getattr(self, "_sys_ox", 0.0))
        cy = float(getattr(self, "_sys_oy", 0.0))
        cx += (raw_ox - cx) * ck
        cy += (raw_oy - cy) * ck
        self._sys_ox, self._sys_oy = cx, cy
        ox, oy = int(bx + cx), int(by + cy)
        key = (round(float(mag), 3), ox, oy)
        now = time.time()
        if key == getattr(self, "_sys_last_sent", None):
            return  # не изменилось — DWM не трогаем
        if now - float(getattr(self, "_sys_last_t", 0.0)) < 1.0 / 30.0:
            return  # не чаще 30 Гц
        if self.sysfx.set(mag, ox, oy):
            self.sysfx.active = True
            self._sys_last_sent = key
            self._sys_last_t = now

    @Slot()
    def tick(self):
        S = self._cfg()
        if S.get("bars", 64) != self._last_bars:
            self.disp = np.zeros(S["bars"], dtype=np.float32)
            self._last_bars = S["bars"]
            self.audio.set_bars(S["bars"])
        key = (S.get("color_mode"), tuple(S.get("custom_color", [0, 220, 255])))
        if key != self._last_color:
            self.palette = build_palette(S.get("color_mode", "neon"), tuple(S.get("custom_color", [0, 220, 255])), S.get("bars", 64))
            self._last_color = key
        if len(self.palette) != S.get("bars", 64):
            self.palette = build_palette(S.get("color_mode", "neon"), tuple(S.get("custom_color", [0, 220, 255])), S.get("bars", 64))
        # смена области/монитора замера — показать рамку захвата на 5 секунд
        try:
            rkey = (S.get("color_monitor", -1), S.get("color_region", "full"),
                    round(float(S.get("color_rx", 0.0)), 3), round(float(S.get("color_ry", 0.0)), 3),
                    round(float(S.get("color_rw", 1.0)), 3), round(float(S.get("color_rh", 1.0)), 3))
        except Exception:
            rkey = None
        if self._last_region is None:
            self._last_region = rkey
        elif rkey != self._last_region:
            self._last_region = rkey
            self._region_flash_until = time.time() + 5.0

        spec, wave, level, bass_raw, has_sig = self.audio.snapshot()
        demo = bool(S.get("demo"))
        if len(spec) != len(self.disp):
            self.disp = np.zeros(len(spec), dtype=np.float32)
        if S.get("auto_balance", True) and (has_sig or demo) and len(spec) >= 6:
            b1, b2 = len(spec) // 3, 2 * len(spec) // 3
            parts = [spec[:b1], spec[b1:b2], spec[b2:]]
            balanced = []
            for j, part in enumerate(parts):
                m = float(part.max()) if len(part) else 0.0
                self._bpeak[j] = max(m, float(self._bpeak[j]) * 0.96)
                peak = float(self._bpeak[j])
                if peak > 0.08:
                    balanced.append(np.clip(part * min(1.0 / peak, 5.0), 0.0, 1.5))
                else:
                    balanced.append(part)
            spec = np.concatenate(balanced).astype(np.float32)
        spec = np.tanh(spec * float(S.get("dynamics", 1.8))).astype(np.float32)
        cap = float(np.clip(S.get("max_level", 1.0), 0.1, 1.0))
        if cap < 1.0:
            spec = soft_cap(spec, cap)
        atk = 1.0 - float(np.clip(S.get("attack_wave", 0.45), 0.0, 0.95))
        rel = 1.0 - float(np.clip(S.get("release_wave", 0.88), 0.0, 0.97))
        d = self.disp
        k = np.where(spec >= d, atk, rel).astype(np.float32)
        self.disp = (d + (spec - d) * k).astype(np.float32)
        if cap < 1.0:
            self.disp = soft_cap(self.disp, cap)
        if not has_sig and not demo:
            self.disp *= 0.92
        self._wave = wave
        self._level = level
        try:
            bass_now = float(np.mean(self.disp[:max(2, len(self.disp) // 6)]))
        except Exception:
            bass_now = 0.0
        self._bass = max(bass_now, getattr(self, "_bass", 0.0) * 0.88)
        self.sc.bass = self._bass
        # авто-скрытие
        if S.get("auto_hide") and not demo:
            self._silence = 0.0 if has_sig else self._silence + 1.0 / max(1, S.get("overlay_fps", 60))
            target = 0.0 if self._silence > float(S.get("auto_hide_sec", 3.0)) else 1.0
        else:
            self._silence, target = 0.0, 1.0
        self._hide_k += (target - self._hide_k) * 0.12
        eff = float(np.clip(S.get("overlay_opacity", 1.0), 0.1, 1.0)) * self._hide_k
        if abs(eff - self._last_eff_op) > 0.01:
            try:
                self.setWindowOpacity(eff)
            except Exception:
                pass
            self._last_eff_op = eff
        self.sc.tick(tuple(S.get("custom_color", [0, 220, 255])))
        self.update_sysfx(S, has_sig, demo)
        self.update()

    # ---------- геометрия ----------
    def zone(self, W, H):
        S = self._cfg()
        w = W * float(S.get("viz_w", 0.92))
        x0 = max(0, min(W - w, W * float(S.get("viz_x", 0.5)) - w / 2))
        y_base = max(0, min(H - 4, H * float(S.get("viz_y", 0.93))))
        return x0, y_base, w

    @staticmethod
    def spatial_smooth(v, amount):
        if amount <= 0.01 or len(v) < 3:
            return v
        r = 1 + int(amount * 3.99)
        kernel = np.exp(-0.5 * (np.arange(-r, r + 1) / max(0.5, r / 2)) ** 2)
        kernel /= kernel.sum()
        padded = np.pad(v, r, mode="reflect")
        return np.convolve(padded, kernel, mode="valid").astype(np.float32)

    # ---------- paint ----------
    def paintEvent(self, ev):
        S = self._cfg()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        n = len(self.disp)
        alpha = int(S.get("alpha", 230))
        bass_m = float(getattr(self, "_bass", 0.0))
        sh = float(np.clip(S.get("shake", 0.0), 0.0, 1.0))
        zm = float(np.clip(S.get("zoom", 0.0), 0.0, 1.0))
        if sh > 0.01 or zm > 0.01:
            p.translate(W / 2, H / 2)
            if sh > 0.01:
                amp = bass_m * sh * 30.0
                p.translate(float(np.random.uniform(-amp, amp)), float(np.random.uniform(-amp, amp)))
                p.rotate(bass_m * sh * 4.0)
            if zm > 0.01:
                f = 1.0 + bass_m * zm * 0.5
                p.scale(1.0 + bass_m * zm * 0.15, f)
            p.translate(-W / 2, -H / 2)
        if S.get("bg_dim", 0.0) > 0.01 or S.get("dim_pulse", 0.0) > 0.01:
            dim = min(0.85, float(np.clip(S.get("bg_dim", 0.0), 0, 0.6))
                      + float(np.clip(S.get("dim_pulse", 0.0), 0, 0.6)) * min(1.0, bass_m))
            if dim > 0.01:
                p.fillRect(0, 0, W, H, QColor(0, 0, 0, int(255 * dim)))
        fl = float(np.clip(S.get("flash", 0.0), 0.0, 1.0))
        if fl > 0.01:
            fa = int(min(1.0, bass_m) * fl * 110)
            if fa > 1:
                fr, fg, fb = (int(v) for v in np.clip(self.sc.shown, 0, 255))
                p.fillRect(0, 0, W, H, QColor(fr, fg, fb, fa))
        style = S.get("style", "smooth")
        if style == "bars":
            self.draw_bars(p, W, H, n, alpha)
        elif style == "circle":
            self.draw_circle(p, W, H, n, alpha)
        elif style == "combo":
            self.draw_bars(p, W, H, n, alpha)
            self.draw_smooth(p, W, H, n)
        else:
            self.draw_smooth(p, W, H, n)
        # виньетка: затемнение краёв экрана (0 = выкл)
        try:
            vs = float(np.clip(S.get("vignette", 0.0), 0.0, 1.0))
        except Exception:
            vs = 0.0
        if vs > 0.01:
            from PySide6.QtGui import QRadialGradient
            grad = QRadialGradient(W / 2, H / 2, 0.72 * max(W, H), W / 2, H / 2)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(0.55, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, int(200 * vs)))
            p.fillRect(0, 0, W, H, QBrush(grad))
        # рамка области захвата (5 секунд после смены параметров)
        try:
            if time.time() < self._region_flash_until:
                mons = QApplication.screens()
                want = int(S.get("color_monitor", -1))
                scr = mons[want] if 0 <= want < len(mons) else (self.screen() or QApplication.primaryScreen())
                mine = self.screen()
                if scr is not None and mine is not None and scr.geometry() == mine.geometry():
                    rx, ry, rw, rh = resolve_region(S)
                    x, y = int(rx * W), int(ry * H)
                    ww, hh = max(4, int(rw * W)), max(4, int(rh * H))
                    pen = QPen(QColor(0, 229, 255, 230))
                    pen.setWidth(2)
                    pen.setStyle(Qt.DashLine)
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(x, y, ww, hh)
                    p.drawText(x + 8, y + 22, "захват")
        except Exception:
            pass
        p.end()

    def draw_smooth(self, p, W, H, n):
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        bass = self._bass
        pulse = (1 + bass * float(S.get("beat_strength", 0.35)) * 1.5) if S.get("beat_pulse") else 1.0
        max_h = H * 0.38 * float(S.get("height_scale", 0.55)) * 2 * pulse
        s = self.spatial_smooth(self.disp, float(S.get("wave_smooth", 0.65)))
        M = 160
        xp = np.linspace(0, 1, len(s))
        xq = np.linspace(0, 1, M)
        yq = np.interp(xq, xp, s).astype(np.float32)
        yq = self.spatial_smooth(yq, float(S.get("wave_smooth", 0.65)) * 0.5)
        floor = 0.02
        yq = np.maximum(yq, floor * (0.5 + 0.5 * np.sin(np.linspace(0, 6.28, M) * 0.5)))
        yq = soft_cap(yq, float(np.clip(S.get("wave_ceiling", 1.0), 0.1, 1.0)))
        xs = x0 + xq * w
        ys = y_base - yq * max_h
        mirror_ys = y_base + yq * max_h * 0.9 if S.get("mirror") else None
        fr, fg, fb = (int(v) for v in np.clip(self.sc.shown, 0, 255))
        breathe = float(getattr(self.sc, "_breathe", 1.0))
        beat = float(getattr(self.sc, "_beat_boost", 1.0))
        if S.get("color_fx") in ("breathe", "beat"):
            fr = min(255, int(fr * breathe * beat))
            fg = min(255, int(fg * breathe * beat))
            fb = min(255, int(fb * breathe * beat))
        fa = int(S.get("fill_alpha", 90))
        grad = QLinearGradient(0, y_base - max_h, 0, y_base)
        grad.setColorAt(0, QColor(fr, fg, fb, min(255, fa + 70)))
        grad.setColorAt(1, QColor(fr, fg, fb, max(0, fa - 40)))
        path = QPainterPath()
        path.moveTo(xs[0], y_base)
        path.lineTo(xs[0], ys[0])
        for i in range(1, M - 1):
            xc = (xs[i] + xs[i + 1]) / 2
            yc = (ys[i] + ys[i + 1]) / 2
            path.quadTo(xs[i], ys[i], xc, yc)
        path.lineTo(xs[-1], ys[-1])
        path.lineTo(xs[-1], y_base)
        path.closeSubpath()
        if S.get("line_style") != "outline":
            p.fillPath(path, QBrush(grad))
        if mirror_ys is not None:
            path2 = QPainterPath()
            path2.moveTo(xs[0], y_base)
            path2.lineTo(xs[0], mirror_ys[0])
            for i in range(1, M - 1):
                xc = (xs[i] + xs[i + 1]) / 2
                yc = (mirror_ys[i] + mirror_ys[i + 1]) / 2
                path2.quadTo(xs[i], mirror_ys[i], xc, yc)
            path2.lineTo(xs[-1], mirror_ys[-1])
            path2.lineTo(xs[-1], y_base)
            path2.closeSubpath()
            grad2 = QLinearGradient(0, y_base, 0, y_base + max_h)
            grad2.setColorAt(0, QColor(fr, fg, fb, min(255, fa + 40)))
            grad2.setColorAt(1, QColor(fr, fg, fb, max(0, fa - 50)))
            p.fillPath(path2, QBrush(grad2))
        lw = int(S.get("line_width", 3))
        style = S.get("line_style", "thin")
        bright = QColor(min(255, fr + 40), min(255, fg + 40), min(255, fb + 40), int(S.get("alpha", 230)))
        if style == "thin":
            path_line = QPainterPath()
            path_line.moveTo(xs[0], ys[0])
            for i in range(1, M):
                path_line.lineTo(xs[i], ys[i])
            pen = QPen(bright)
            pen.setWidth(1)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path_line)
        elif style == "gradient":
            pal = self.palette if len(self.palette) == n else build_palette("neon", (0, 220, 255), n)
            for i in range(M - 1):
                pi = int(i / max(1, M - 2) * (n - 1))
                r, g, b = pal[pi]
                pen = QPen(QColor(r, g, b, int(S.get("alpha", 230))))
                pen.setWidth(lw)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
        elif style == "dotted":
            faint = QPen(QColor(fr, fg, fb, 70))
            faint.setWidth(1)
            p.setPen(faint)
            for i in range(len(xs) - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(bright))
            step = max(2, M // 48)
            rad = max(2, lw + 1)
            for i in range(0, M, step):
                p.drawEllipse(int(xs[i] - rad / 2), int(ys[i] - rad / 2), int(rad), int(rad))
        elif style in ("fat", "megaglow"):
            pen = QPen(bright)
            pen.setWidth(lw * 2 + 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for i in range(len(xs) - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            if style == "megaglow":
                core = QPen(QColor(255, 255, 255, int(S.get("alpha", 230))))
                core.setWidth(max(1, lw - 1))
                core.setCapStyle(Qt.RoundCap)
                p.setPen(core)
                for i in range(len(xs) - 1):
                    p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
        else:
            pen = QPen(bright)
            pen.setWidth(lw if style != "double" else max(1, lw - 1))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if style == "dash":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            for i in range(len(xs) - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            if style == "double":
                pen2 = QPen(QColor(fr, fg, fb, max(60, int(S.get("alpha", 230)) - 90)))
                pen2.setWidth(max(1, lw - 1))
                pen2.setCapStyle(Qt.RoundCap)
                p.setPen(pen2)
                for i in range(len(xs) - 1):
                    p.drawLine(int(xs[i]), int(ys[i] + 9), int(xs[i + 1]), int(ys[i + 1] + 9))
        p.setPen(QPen(QColor(255, 255, 255, 45)))
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def draw_bars(self, p, W, H, n, alpha):
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        gap = 3
        bw = max(2.0, (w - gap * (n + 1)) / n)
        max_h = H * 0.42 * float(S.get("height_scale", 0.55)) * 2
        for i, v in enumerate(self.disp):
            h = max(2, v * max_h)
            x = x0 + gap + i * (bw + gap)
            r, g, b = self.palette[i]
            boost = min(1.0, 0.55 + v * 0.7)
            col = QColor(int(r * boost) % 256, int(g * boost) % 256, int(b * boost) % 256, alpha)
            p.fillRect(int(x), int(y_base - h), int(bw), int(h), col)
        pen = QPen(QColor(255, 255, 255, 60))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def draw_circle(self, p, W, H, n, alpha):
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        cx, cy = x0 + w / 2, y_base - H * 0.12
        base_r = min(W, H) * 0.14 * (0.6 + float(S.get("height_scale", 0.55)))
        base_r *= (1 + self._level * 0.12)
        max_len = min(W, H) * 0.22 * float(S.get("height_scale", 0.55)) * 2
        pen = QPen()
        pen.setWidth(3)
        pen.setCapStyle(Qt.RoundCap)
        for i, v in enumerate(self.disp):
            ang = 2 * math.pi * i / n - math.pi / 2
            r1 = base_r
            r2 = base_r + 4 + v * max_len
            r, g, b = self.palette[i]
            pen.setColor(QColor(r, g, b, alpha))
            p.setPen(pen)
            p.drawLine(int(cx + r1 * math.cos(ang)), int(cy + r1 * math.sin(ang)),
                       int(cx + r2 * math.cos(ang)), int(cy + r2 * math.sin(ang)))
        pen.setColor(QColor(255, 255, 255, 70))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawEllipse(int(cx - base_r), int(cy - base_r), int(base_r * 2), int(base_r * 2))
