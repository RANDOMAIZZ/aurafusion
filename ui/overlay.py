"""Прозрачный click-through оверлей AuraFusion (smooth/bars/circle/combo)."""
import ctypes
import math
import threading
import time

import logging

import numpy as np
from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                          QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

from core import beat as beat_mod
from core.screen_color import lift_rgb

log = logging.getLogger("aurafusion.overlay")

# --- полоса панели задач -------------------------------------------------------
# Панель задач на автоскрытии не поднимается, когда оверлей включён. Причина в
# том, что оверлей — окно WindowStaysOnTopHint размером во весь экран: он
# накрывает и горячую полосу у нижнего (или верхнего, бокового) края, и полосу,
# на которую панель выезжает. Панель выезжает, но оказывается ПОД оверлеем, и
# визуально её как будто нет.
#
# Лечится тем, что окно занимает рабочую область без панели задач. Когда панель
# не скрыта, Qt это уже учитывает в availableGeometry(). А на автоскрытии она
# возвращает весь экран, поэтому полосу приходится убирать самому.
_TASKBAR_CACHE = {}

# Сколько пикселей экрана оверлей оставляет панели задач. 1 — пробный вариант:
# панель на автоскрытии поднимается по самой кромке, возможно хватит одного
# пикселя. Если начнёт прятаться под оверлеем или перестаёт подниматься —
# поставить сюда толщину панели (30 при 100% масштабе), это проверенный
# вариант, а текущий экспериментальный.
TASKBAR_KEEP = 1


def taskbar_rects():
    """Прямоугольники панелей задач: основной и на вторых мониторах.

    Источник — сами окна панели. Реестр (TaskbarSize/TaskbarPos) на Windows 10
    22H2 пустой, а угадывать толщину по DPI нельзя: промах на 20 пикселей
    либо оставит панель под оверлеем, либо срежет фигуру. Приём у окна панели в
    том, что на автоскрытии оно не схлопывается в пару пикселей, а просто
    уезжает за край экрана целиком — настоящую толщину видно и там."""
    if "rs" in _TASKBAR_CACHE:
        return _TASKBAR_CACHE["rs"]
    out = []
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]

        u32 = ctypes.windll.user32
        for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = u32.FindWindowW(cls, None)
            if not hwnd:
                continue
            rc = _RECT()
            if u32.GetWindowRect(hwnd, ctypes.byref(rc)):
                w, h = rc.r - rc.l, rc.b - rc.t
                if w > 40 and h > 10:
                    out.append((int(rc.l), int(rc.t), int(rc.r), int(rc.b)))
    except Exception:
        out = []
    _TASKBAR_CACHE["rs"] = out
    return out


def work_geometry(screen, avoid=True):
    """Область экрана, которую можно занимать оверлеем.

    Без панели задач. Когда панель видна и не на автоскрытии, Qt это уже учёл в
    availableGeometry() — просто берём его. А на автоскрытии availableGeometry()
    равен полному экрану, и тогда убираем полосу сами: иначе оверлей накроет
    и горячую зону, и место, куда панель выезжает."""
    g = QRect(screen.geometry())
    if not avoid:
        return g
    a = QRect(screen.availableGeometry())
    if a != g:
        return a                       # панель видна и уже вырезана
    for bl, bt, br, bb in taskbar_rects():
        # панель на другом мониторе — этот экран не трогаем
        if min(g.right(), br) < max(g.left(), bl) or min(g.bottom(), bb) < max(g.top(), bt):
            continue
        tw, th = br - bl, bb - bt
        # ориентация: панель почти во всю ширину/высоту экрана
        if tw >= g.width() - 8:
            thick = th
            side = 2 if (bb - g.bottom()) >= (g.top() - bt) else 3      # снизу/сверху
        else:
            thick = tw
            side = 0 if (g.left() - bl) >= (br - g.right()) else 1      # слева/справа
        thick = int(max(0, min(thick, g.height() // 3 if side in (2, 3) else g.width() // 3)))
        if thick <= 0:
            continue
        # Минимум 1 px вместо всей толщины: пользователь попросил попробовать
        # так — оставить панели задач ровно один пиксель. Если Windows с
        # автоскрытием поднимает панель по этой кромке, хватит и её; если нет,
        # вернём полную толщину. Переключается одним числом TASKBAR_KEEP.
        keep = TASKBAR_KEEP
        cut = min(thick, keep)
        if side == 2:
            a.setBottom(g.bottom() - cut)
        elif side == 3:
            a.setTop(g.top() + cut)
        elif side == 0:
            a.setLeft(g.left() + cut)
        else:
            a.setRight(g.right() - cut)
        break
    # страховка от вырожденной геометрии
    if a.width() < g.width() // 3 or a.height() < g.height() // 3:
        return g
    return a


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

    def __init__(self, enabled=True):
        self.ok = False
        self.active = False
        self.mag = None
        # Инициализация ленивая и повторяемая. MagInitialize захватывает
        # Magnification API на весь сеанс, поэтому его незачем звать, пока
        # лупа выключена. Но и «создать один раз в конструкторе» нельзя:
        # приложение стартует с sys_fx=false, а включить его можно позже —
        # и тогда лупа не поднялась бы никогда.
        if enabled:
            self.ensure()

    def ensure(self) -> bool:
        """Поднять лупу, если ещё не поднята. Повторные попытки безопасны."""
        if self.ok:
            return True
        try:
            self.mag = ctypes.windll.magnification
            self.ok = bool(self.mag.MagInitialize())
        except Exception as e:
            print(f"[sysfx] init: {e}")
            self.ok = False
        if not self.ok:
            print("[sysfx] Magnification unavailable")
        return self.ok

    def set(self, mag: float, ox: int, oy: int) -> bool:
        if not self.ensure():
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

# Стили оверлея: ключ из конфига -> подпись. Один список на десктоп и веб,
# иначе подписи в двух интерфейсах постепенно разъезжаются.
STYLES = [
    ("smooth", "Плавная волна"),
    ("bars", "Бары"),
    ("peak", "Пики"),
    ("particles", "Частицы"),
    ("sparks", "Искры"),
    ("orbs", "Шарики"),
    ("mesh", "Сетка"),
    ("stack", "Слои"),
    ("fill", "Заливка"),
    ("ribbon", "Лента"),
    ("circle", "Круг"),
    ("arc", "Дуга"),
    ("spiral", "Спираль"),
    ("radial", "Кольца"),
    ("rays", "Лучи"),
    ("combo", "Бары+волна"),
]

LINE_STYLES = [
    ("neon", "Неон"), ("thin", "Тонкая"), ("fat", "Жирная"), ("double", "Двойная"),
    ("dash", "Пунктир"), ("gradient", "Градиент"), ("dotted", "Точки"),
    ("outline", "Контур"), ("steps", "Ступени"), ("megaglow", "Яркое ядро"),
]

# --- что какая фигура умеет -----------------------------------------------------
# У фигур разный набор настроек: у круга нет смысла в «зеркале вверх+вниз»,
# а у волны нет смысла в «центре на экране». Списки ниже решают, что показывать
# в панели для выбранного стиля, а что прятать. Один источник на десктоп и веб.
BASE_STYLES = {"smooth", "bars", "peak", "particles", "sparks", "orbs", "mesh",
               "stack", "fill", "ribbon", "combo"}
# Границы числа линий: меньше 8 фигуру рассыпается, больше 512 — незаметно
# медленно и глазу не отличить от 400.
#
# Потолок поднят с 256, потому что от него считается бюджет частиц у искр:
# «линии» × 4. При 256 линиях выходило 1024 частицы, и верхние деления
# ползунка плотности просто не помещались в буфер.
LINE_MIN, LINE_MAX = 8, 512
CENTER_STYLES = {"circle", "arc", "spiral", "radial", "rays"}
STROKE_STYLES = {"smooth", "fill", "ribbon"}          # где работает тип линии
FILL_STYLES = {"smooth", "fill", "ribbon", "stack"}   # где есть заливка
MIRROR_STYLES = {"smooth"}

# Настройки, общие для любой фигуры: их нельзя прятать, и они общие для всех
# сразу — их нельзя и разделять по стилям, иначе звук и RGB поедут.
COMMON_KEYS = {
    "style", "fill_color", "bars", "sensitivity", "dynamics",
    "bass_gain", "mid_gain", "treble_gain", "auto_balance",
    "overlay_opacity", "overlay_fps", "monitor",
    "color_mode", "shape_color", "color_fx", "custom_color", "alpha", "color_dark",
    "beat_pulse", "beat_strength",
    "shake", "zoom", "flash", "vignette", "bg_dim", "dim_pulse",
    "sys_fx", "sys_mode", "sys_zoom", "sys_shake",
}


def per_style_keys(style):
    """Настройки, которые у каждой фигуры свои — их видно не у всех, и
    значения хранятся отдельно (style_settings[стиль])."""
    keys = {"height_scale", "line_count", "line_smooth"}
    if style != "sparks":
        # Ореол вокруг фигуры есть у всех, кроме искр: у них своё свечение
        # частиц, и два ореола наложились бы вдвое жирнее и вдвое дороже.
        # Ползунок у искр не показываем — он всё равно ничего бы не делал.
        keys |= {"shape_glow"}
    if style == "sparks":
        # скорость, разгон и плотность — только у искр, у остальных фигур их нет
        keys |= {"spark_speed", "spark_boost", "spark_density", "spark_glow",
                "spark_glow_start", "spark_glow_end", "spark_size",
                "spark_burst_count", "spark_burst_height", "spark_hover",
                "spark_bass_burst"}
    if style in BASE_STYLES:
        keys |= {"viz_x", "viz_y", "viz_w"}
    if style in CENTER_STYLES:
        keys |= {"fig_x", "fig_y", "fig_size"}
    if style in STROKE_STYLES:
        keys |= {"line_style", "line_width"}
    if style in FILL_STYLES:
        keys |= {"fill_alpha"}
    if style in MIRROR_STYLES:
        keys |= {"mirror"}
    if style == "smooth":
        keys |= {"wave_ceiling"}
    return keys


def style_keys(style):
    """Всё, что показываем для этой фигуры: общие плюс её собственные."""
    return COMMON_KEYS | per_style_keys(style)


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
        self._cfg_raw = cfg_getter
        self.audio = audio
        self.sc = screencolor
        cfg = self._cfg()
        self.disp = np.zeros(cfg.get("bars", 64), dtype=np.float32)
        # буфер фигуры: длина = line_count, вообще говоря не равна bars
        self.lines = np.zeros(cfg.get("bars", 64), dtype=np.float32)
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
        self._last_tick = time.monotonic()
        # сцена Qt для размытия ореола; создаётся лениво при первом кадре
        # со свечением — иначе платили бы за неё даже при свечении 0
        self._gfx = None
        # Где на экране окно веб-панели (x, y, w, h) в пикселях экрана.
        # Сообщает браузер через /api/panel_geom. Пока его нет, панель целиком
        # попадает в кадр замера, а в ней цветные кружки цвета экрана и цвета
        # ПК: оверлей измеряет сам себя через панель, и цвет начинает гулять.
        self._panel_geom = None
        self._panel_geom_src = ""     # откуда пришла геометрия: win32/браузер
        self._panel_on_top = False    # окно панели на переднем плане?
        self._last_sample = None
        # Счётчики отбракованных кадров. Цвет держится на прошлом значении
        # не «из-за лени», а когда кадр не дошёл: слишком тёмный, или после
        # вырезания оверлея не осталось ничего живого. Раньше обе причины
        # выглядели одинаково — «цвет завис», и непонятно где искать.
        self._stat = {"ok": 0, "dark": 0, "masked": 0, "err": 0,
                      "busy": 0, "skip": 0, "None": 0}
        # Счётчики пучка на басу: сколько ударов поймано, сколько частиц
        # запрошено, сколько выпущено и сколько запросилось, но места в
        # буфере не было. Без них «пучок то появляется, то нет» нечем было
        # лечить — причина была не видна.
        self._stat["punch_hits"] = 0
        self._stat["punch_req"] = 0
        self._stat["punch_ok"] = 0
        self._stat["punch_drop"] = 0
        self._stat["spark_alive"] = 0
        self._stat["spark_cap"] = 0
        # Честный fps оверлея. Считаем сами окна, а не верим настройке:
        # раньше в панели показывалось заданное значение, а не то, что
        # выдаёт машина, и просадки были не видны вообще.
        # Имя не _fps: метод _fps() уже есть и отдаёт ЗАДАННУЮ в настройках
        # скорость. Атрибут с тем же именем затёр бы метод, и приложение падало
        # бы при старте на TypeError: 'float' object is not callable.
        self._fps_n = 0
        self._fps_at = time.monotonic()
        self._fps_real = 0.0
        self._panel_blocked = False
        self._panel_cover = 0.0
        self._wave = np.zeros(1024, dtype=np.float32)
        self._level = 0.0
        # Детектор темпа. Держим здесь, а не в audio_engine: оверлей — единственное
        # место, где спектр уже нормализован (авто-баланс, dynamics, сглаживание),
        # и только здесь можно честно ловить удары. От нуля, потому что при
        # включении темп неизвестен и набирается за несколько секунд.
        self._beat = beat_mod.BeatTracker(fps=self._fps())
        self._beat_pulse = 0.0
        self._beat_fired = False
        self._beat_t = 0.0
        self._beat_in = np.zeros(cfg.get("bars", 64), dtype=np.float32)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        screen = QApplication.primaryScreen()
        self.setGeometry(work_geometry(screen, self._cfg().get("avoid_taskbar", True)))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.apply_fps()
        # (x, y, w, h) окна, которое не должно голосовать за цвет, в координатах
        # экрана. По умолчанию None: панель живёт в браузере, и её положение
        # приложению неизвестно, — поэтому маска exclude_viz считает, что
        # голосовать нечему. Поле оставлено на случай, если панель снова
        # станет отдельным окном поверх экрана.
        self._region_flash_until = 0.0  # до какого момента рисовать рамку области захвата
        self._last_region = None  # последний виденный набор параметров области
        # лупа всего экрана (Zoom+тряска через Magnification API) — только если
        # она реально включена, иначе не трогаем API вовсе
        self.sysfx = SysFX(enabled=bool(cfg.get("sys_fx", False)))
        self.color_timer = QTimer(self)
        self.color_timer.timeout.connect(self.sample_screen_color)
        # Снимать экран стоит 33 мс — Qt тянет кадр 1920x1080, а уменьшить
        # дешевле нечем: через GDI BitBlt кадр приходит чёрным, DWM своё
        # содержимое так не отдаёт. Раньше этот вызов висел на главном потоке,
        # и при частом интервале панель и оверлей заметно замирали.
        # Теперь съёмка ушла в отдельный поток: главный поток только просит,
        # поэтому короткий интервал больше не рвёт отрисовку. Поток один, и
        # запросы не копятся: пока идёт съёмка, следующий пропускается.
        self._grab_gate = threading.Lock()
        self._grab_want = threading.Event()
        self._grab_busy = False
        self._grab_core = None      # ядро, отведённое под съёмку
        self._grab_thread = threading.Thread(target=self._grab_loop,
                                             daemon=True, name="aura-grab")
        self._grab_thread.start()
        self._sample_ms = self._interval_ms()
        self.color_timer.start(self._sample_ms)

    def _interval_ms(self):
        """Интервал замера, зажатный в разумные границы."""
        try:
            return int(np.clip(float(self._cfg().get("color_sample_ms", 60)), 33, 2000))
        except (TypeError, ValueError):
            return 60

    def apply_sample_ms(self):
        """Перезапустить таймер замера на новое значение, без перезапуска
        приложения. Раньше интервал читался только в __init__, ползунок менял
        цифру в конфиге — а по факту ничего."""
        ms = self._interval_ms()
        if ms == getattr(self, "_sample_ms", ms):
            return
        self._sample_ms = ms
        try:
            self.color_timer.stop()
            self.color_timer.start(ms)
        except Exception:
            pass

    def _cfg(self):
        """Конфиг с наложенными настройками текущего стиля.

        У каждой фигуры свои размер, положение и сила: кругу не нужна ширина
        по краям экрана, волне — центр. Чтобы не плодить «15 × N» настроек в
        плоском конфиге, значения лежат в style_settings по имени стиля, а
        отсюда приходят уже подставленными. Десктоп и веб читают одно и то же,
        поэтому переключение стиля работает и там, и там."""
        cfg = self._cfg_raw()
        try:
            ov = (cfg.get("style_settings") or {}).get(cfg.get("style", "smooth"))
        except Exception:
            ov = None
        if not isinstance(ov, dict) or not ov:
            return cfg
        merged = dict(cfg)
        merged.update(ov)
        return merged

    def _fps(self):
        """FPS оверлея как число.

        Раньше здесь стояло max(1, cfg['overlay_fps']) напрямую: значение из
        панели приходило строкой («60»), и сравнение int со str роняло
        paint/tick — приложение вообще не стартовало. Теперь строка и мусор
        не проходят."""
        try:
            return max(1.0, float(self._cfg().get("overlay_fps", 60)))
        except (TypeError, ValueError):
            return 60.0

    def apply_fps(self):
        self.timer.start(max(8, int(1000 / self._fps())))

    def apply_opacity(self):
        try:
            self.setWindowOpacity(float(np.clip(self._cfg().get("overlay_opacity", 1.0), 0.1, 1.0)))
        except Exception:
            pass

    def move_to_monitor(self, idx):
        try:
            screens = QApplication.screens()
            if 0 <= idx < len(screens):
                self.setGeometry(work_geometry(screens[idx],
                                               self._cfg().get("avoid_taskbar", True)))
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
            screens = QApplication.screens()
            want = int(cfg.get("color_monitor", -1))
            if 0 <= want < len(screens):
                screen = screens[want]
            else:
                screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            rx, ry, rw, rh = resolve_region(cfg)
            # Интерфейс закрывает почти весь экран — измерять тогда нечего,
            # кроме него самого. Берём последний удачный цвет и кадр не мутим.
            # Раньше оверлей честно измерял тёмный интерфейс и красил себя его
            # цветом, а интерфейс показывал цвет ПК, который брался из этого же
            # оверлея. Замкнутый круг: цвет то догонял, то прыгал, а на резкой
            # смене картинки уходил в радугу.
            if self._panel_covers(screen):
                if not self._panel_blocked:
                    self._panel_blocked = True
                    log.warning("интерфейс закрывает экран (%.0f%%) — цвет с экрана "
                                "не берётся, держится последний",
                                self._panel_cover * 100)
                return
            self._panel_blocked = False
            # Съёмка ушла в отдельный поток: она занимает 33 мс, и на главном
            # потоке такой блок при коротком интервале рвал отрисовку. Здесь
            # только решаем, можно ли вообще мерить, и отдаём запрос.
            with self._grab_gate:
                if self._grab_busy:
                    self._stat["busy"] += 1
                    return
                self._grab_busy = True
            self._grab_job = {
                "screen": screen,
                "cfg": cfg,
                "region": (rx, ry, rw, rh),
                "exclude_viz": bool(cfg.get("exclude_viz", True)),
            }
            self._grab_want.set()
        except Exception:
            with self._grab_gate:
                self._grab_busy = False

    def _tune_grab_thread(self):
        """Отобрать поток съёмки от потока отрисовки.

        Съёмка кадра занимает 33 мс и раньше шла на равных правах с
        отрисовкой. На этой машине это стоило 8 fps: 13 вместо 21. Проверено
        A/B — с выключенной съёмкой fps выше, и найск окна панели тут ни при
        чём (0.2 мс раз в 400 мс).

        Что делаем:
        - приоритет ниже обычного: планировщик Windows отдаст ядро отрисовке,
          а съёмка доберёт своё в паузах;
        - запрет приоритетного буста, чтобы поток не вытеснял главный;
        - отдельное ядро: поток закрепляется за последним логическим ядром,
          и это ядро убирается из маски процесса. Остальное приложение
          (звук, BLE, сервер, отрисовка) на него не попадает.

        Ядер 4 и меньше не трогаем: там отъезд одного ядра дороже самой
        съёмки.
        """
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentThread.restype = ctypes.c_void_p
            k32.GetCurrentThread.argtypes = []
            k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            k32.SetThreadPriority.restype = ctypes.c_int
            k32.SetThreadAffinityMask.argtypes = [ctypes.c_void_p,
                                                  ctypes.c_size_t]
            k32.SetThreadAffinityMask.restype = ctypes.c_size_t
            k32.SetThreadPriorityBoost.argtypes = [ctypes.c_void_p, ctypes.c_int]
            k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p,
                                                   ctypes.c_size_t]
            k32.GetProcessAffinityMask.argtypes = [ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_size_t),
                                                   ctypes.POINTER(ctypes.c_size_t)]
            k32.GetActiveProcessorCount.argtypes = [ctypes.c_uint16]
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.GetCurrentProcess.argtypes = []

            THREAD_PRIORITY_BELOW_NORMAL = -1
            h = k32.GetCurrentThread()
            k32.SetThreadPriority(h, THREAD_PRIORITY_BELOW_NORMAL)
            k32.SetThreadPriorityBoost(h, 0)          # 0 — буст выключен

            n = int(k32.GetActiveProcessorCount(0xFFFF))   # ALL_PROCESSOR_GROUPS
            if n < 4:
                return
            proc = k32.GetCurrentProcess()
            sysmask = ctypes.c_size_t()
            curmask = ctypes.c_size_t()
            if not k32.GetProcessAffinityMask(proc,
                                              ctypes.byref(sysmask),
                                              ctypes.byref(curmask)):
                return
            last = max(n - 1, 63) if n > 64 else n - 1
            one = ctypes.c_size_t(1 << last)
            if not (curmask.value & one.value):
                return
            k32.SetThreadAffinityMask(h, one)
            # это ядро убираем у всего остального процесса
            k32.SetProcessAffinityMask(proc, ctypes.c_size_t(curmask.value & ~one.value))
            self._grab_core = last
        except Exception:
            pass    # нет прав или старая система — просто работаем как есть

    def _grab_loop(self):
        """Поток съёмки: снять кадр, привести к 96x54, отдать цвету.

        Здесь ничего не трогает виджеты: QScreen.grabWindow, QImage и numpy
        работают вне главного потока. Общие с ним данные закрыты own_lock
        у ScreenColor, а _last_sample перезаписывается целиком и читается
        только для разбора.
        """
        self._tune_grab_thread()
        while True:
            self._grab_want.wait()
            self._grab_want.clear()
            try:
                self._grab_once()
            except Exception:
                self._stat["err"] += 1
            finally:
                with self._grab_gate:
                    self._grab_busy = False

    def _grab_once(self):
        job = getattr(self, "_grab_job", None)
        if not job:
            return
        screen = job["screen"]
        rx, ry, rw, rh = job["region"]
        pix = screen.grabWindow(0).scaled(480, 270, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = pix.toImage().convertToFormat(QImage.Format_RGB888)
        arr = qimage_to_sample(img, (rx, ry, rw, rh))
        if arr is None:
            self._stat["None"] += 1
            return
        w, h = 96, 54
        if float(arr.max()) <= 0.01:
            self._stat["dark"] += 1
            return  # почти чёрный кадр (exclusive fullscreen, выкл. экран) —
            # замера нет, держим последний цвет вместо нырка в чёрный
        if job["exclude_viz"]:
            self._blackout_viz(arr, w, h, job["cfg"], screen.geometry(),
                               (rx, ry, rw, rh))
            live = int((arr.reshape(-1, 3).max(axis=1) > 0.01).sum())
            self._stat["live"] = live
            if float(arr.max()) <= 0.01:
                # Вырезали оверлей и не осталось НИЧЕГО живого. Маска для
                # искр раздувается вместе с их свечением и накрывает весь
                # кадр — вырезать becomes' то же самое, что не мерить вовсе,
                # и цвет навсегда замирает на прошлом. Лучше лишний оверлей в
                # кадре, чем мёртвый цвет: он занимает часть площади, и при
                # K-means кластеру нужно больше 4%, чтобы вообще выиграть.
                raw = qimage_to_sample(img, (rx, ry, rw, rh))
                if raw is None or float(raw.max()) <= 0.01:
                    self._stat["masked"] += 1
                    return
                arr = raw
                self._stat["fallback"] = self._stat.get("fallback", 0) + 1
            else:
                self._stat["ok"] += 1
        else:
            self._stat["ok"] += 1
        self.sc.feed_frame(arr)
        # последний сэмпл держим для разбора: из другого процесса экран
        # не снять (grabWindow отдаёт чёрный кадр), а посмотреть, что
        # именно измеряется, нужно
        self._last_sample = arr.copy()

    # Высота фигур, доля высоты экрана при height_scale = 1. Один источник
    # правды для отрисовки и для маски exclude_viz: раньше маска считалась
    # отдельно и разъезжалась с геометрией (бары дорастали выше, чем резала
    # маска, и подмешивали свой свет в замер цвета экрана).
    FIG_BARS = 0.42      # бары / пики / частицы: от базовой линии вверх
    FIG_SMOOTH = 0.38    # плавная волна
    FIG_RIBBON = 0.17    # лента: симметричный пояс вокруг базовой линии
    FIG_CIRCLE = 0.14    # радиус круга (от min(W, H))
    FIG_SPOKE = 0.22     # длина луча (от min(W, H))

    def _fig_size(self, cfg):
        return float(np.clip(cfg.get("fig_size", 1.0), 0.2, 3.0))

    def _fit_center(self, pos, half, size):
        """Центр фигуры, приподнятый так, чтобы она целиком влезла в экран.

        Базовая линия по умолчанию 0.93 — почти самый низ экрана, и
        симметричные фигуры (заливка, лента) уезжали за нижний край, где их
        обрезал монитор. Раньше их рисовали как попало, а маска замера считала
        честные габариты — то есть замер видел обрезанную картинку.

        size — полный размер области в тех же единицах, что и pos/half
        (доли экрана в маске, пиксели в отрисовке)."""
        hi = max(0.0, size - half)
        lo = min(half, hi)
        return float(np.clip(pos, lo, hi))

    def _fit_lines(self, S, d):
        """Привести спектр к нужному числу линий этой фигуры и сгладить.

        Число линий у каждой фигуры своё: у баров это столбики, у круга и
        спирали — спицы и витки, у сетки — столбцы. Плавность тоже своя:
        круглые фигуры раньше не сглаживались вообще, поэтому выглядели
        рванее волны при тех же настройках."""
        src = np.asarray(d, dtype=np.float32)
        n = int(np.clip(S.get("line_count", 0) or 0, LINE_MIN, LINE_MAX))
        if n <= 0:
            n = max(LINE_MIN, min(LINE_MAX, len(src)))
        if not len(src):
            return np.zeros(n, dtype=np.float32)
        if len(src) != n:
            # передискретизация: больше линий, чем полос, тянем интерполяцией,
            # меньше — усредняем куски спектра
            src = np.interp(np.linspace(0, 1, n),
                            np.linspace(0, 1, len(src)), src).astype(np.float32)
        sm = float(np.clip(S.get("line_smooth", 0.0), 0.0, 1.0))
        if sm > 0.01:
            src = self.spatial_smooth(src, sm)
        return src

    def _glow_margin(self, cfg, hs=1.0):
        """Насколько ореол вылезает за габариты фигуры, в долях экрана.

        Маска exclude_viz отрезает оверлей из кадра замера, чтобы он не
        измерял сам себя. Но считалась она по габаритам фигуры, а не по
        ореолу. У искр при свечении 300 и высоте 1.98 самый широкий проход
        даёт радиус около 76 пикселей — это 7% высоты экрана, и столько
        света уходило обратно в замер: замером собственного ореола оверлей
        меняет цвет у себя же.

        Обратная связь выглядит так: оверлей подмешивает свой цвет в кадр,
        следующий замер берёт уже смешанный цвет, отклонение растёт от круга
        к кругу, и на резкой смене картинки на экране петля раскачивается по
        цвету — оверлей переливается радугой, которой на экране нет.

        Радиус берём в пикселях от настоящего размера окна и делим на его
        высоту: в долях экрана ореол не зависит от разрешения, в пикселях
        зависит.
        """
        try:
            glow = float(np.clip(cfg.get("spark_glow", 0.0), 0.0, 3.0))
        except (TypeError, ValueError):
            glow = 0.0
        try:
            sg = float(np.clip(cfg.get("shape_glow", 0.0), 0.0, 3.0))
        except (TypeError, ValueError):
            sg = 0.0
        if glow <= 0.02 and sg <= 0.02:
            return 0.0
        hs = float(np.clip(hs, 0.01, 2.0))
        r_px = 0.0
        if glow > 0.02:
            # худший случай: amp максимален (1.4), частица не успела погаснуть.
            # Радиус берём от spark_size, а не от height_scale: это тот же
            # радиус, что и в draw_sparks. Иначе при разведённых ползунках
            # маска exclude_viz резервировала бы не тот размер.
            rad = 1.1 + 4.6 * 1.12 * float(np.clip(cfg.get("spark_size", 1.0), 0.2, 3.0))
            # самый широкий проход: k = 2.9 при свечении 3.0
            r_px = rad * (1.0 + 1.9 * glow)
        # общий ореол фигуры у остальных стилей: около 14 пикселей на 1080p
        r_px = max(r_px, 14.0 * sg)
        return (r_px + 4.0) / float(max(1, self.height()))

    def _panel_covers(self, screen):
        """Мешает ли окно панели замеру цвета.

        По умолчанию НЕ мешает, и это осознанно. Панель почти всегда открыта
        в браузере во весь экран — но тогда на экране нет ничего, кроме неё,
        и она сама является тем контентом, цвет которого пользователь хочет
        видеть. Раньше здесь стояло «мешает», и выходило так: пользователь
        открывает панель, чтобы посмотреть настройки, — цвет замирает
        наглухо. Перезагрузка и переподъём не помогали, потому что причина
        была не в зависании.

        Галка panel_blocks возвращает старое поведение — для тех случаев,
        когда панель развёрнута и мерять её цвет действительно не нужно.

        Доля и передний план по-прежнему считаются: они показываются в
        панели, чтобы было видно, что приложение вообще видит окно.
        """
        pg = getattr(self, "_panel_geom", None)
        self._panel_cover = 0.0
        if pg is None or screen is None:
            return False
        sg = screen.geometry()
        if sg.width() <= 0 or sg.height() <= 0:
            return False
        ix0, iy0 = max(pg[0], sg.left()), max(pg[1], sg.top())
        ix1 = min(pg[0] + pg[2], sg.left() + sg.width())
        iy1 = min(pg[1] + pg[3], sg.top() + sg.height())
        if ix1 <= ix0 or iy1 <= iy0:
            return False
        self._panel_cover = (ix1 - ix0) * (iy1 - iy0) / float(sg.width() * sg.height())
        if not self._cfg().get("panel_blocks", False):
            return False
        return bool(getattr(self, "_panel_on_top", False)) and self._panel_cover > 0.6

    def _extent(self, cfg, style=None):
        """Габариты фигуры в долях экрана: (x0, x1, top, bot).

        Считается из тех же констант, по которым рисует paintEvent, поэтому
        маска exclude_viz всегда совпадает с тем, что реально на экране."""
        style = style or cfg.get("style", "smooth")
        hs = float(np.clip(cfg.get("height_scale", 0.55), 0.01, 2.0))
        vw = float(np.clip(cfg.get("viz_w", 0.92), 0.05, 1.0))
        vx = float(cfg.get("viz_x", 0.5))
        x0 = max(0.0, min(1.0 - vw, vx - vw / 2.0))
        x1 = min(1.0, x0 + vw)
        yb = float(cfg.get("viz_y", 0.93))
        pulse = 1.0
        if cfg.get("beat_pulse"):
            pulse = 1.0 + float(getattr(self, "_bass", 0.0)) * float(cfg.get("beat_strength", 0.35)) * 1.5
        try:
            peak = min(1.6, float(np.max(self.disp))) if len(self.disp) else 0.0
        except Exception:
            peak = 0.0
        peak = min(1.4, peak * pulse)
        W, H = max(1, self.width()), max(1, self.height())
        aspect = W / H
        short = min(1.0, aspect)            # min(W, H) / H
        if style in ("radial", "rays", "arc", "spiral"):
            # круговые фигуры живут в центре экрана; short уже в долях высоты,
            # поэтому радиус делить на H ещё раз нельзя
            r = self.FIG_CIRCLE * (0.6 + hs) * short * self._fig_size(cfg)
            ln = self.FIG_SPOKE * hs * 2.0 * short * self._fig_size(cfg) * max(0.12, peak)
            R = min(1.0, r + ln)
            fx = float(np.clip(cfg.get("fig_x", 0.5), 0.0, 1.0))
            fy = float(np.clip(cfg.get("fig_y", 0.5), 0.0, 1.0))
            return (max(0.0, fx - R * aspect), min(1.0, fx + R * aspect),
                    max(0.0, fy - R), min(1.0, fy + R))
        if style == "circle":
            r = self.FIG_CIRCLE * (0.6 + hs) * short * self._fig_size(cfg)
            ln = self.FIG_SPOKE * hs * 2.0 * short * self._fig_size(cfg) * max(0.12, peak)
            R = min(1.0, r + ln)
            fx = float(np.clip(cfg.get("fig_x", 0.5), 0.0, 1.0))
            fy = float(np.clip(cfg.get("fig_y", 0.5), 0.0, 1.0))
            return x0, x1, max(0.0, fy - R), min(1.0, fy + R)
        if style == "ribbon":
            th = self.FIG_RIBBON * hs * (0.22 + min(1.4, peak))
            cy = self._fit_center(yb, th, 1.0)
            return x0, x1, max(0.0, cy - th), min(1.0, cy + th * 0.8)
        if style == "fill":
            mh = min(self.FIG_SMOOTH * hs * 2.0 * peak, 1.0)
            cy = self._fit_center(yb, mh * 0.925, 1.0)
            mh = min(mh, max(0.02, cy))
            return x0, x1, max(0.0, cy - mh), min(1.0, cy + mh * 0.85)
        if style == "sparks":
            # искры улетают выше любой другой фигуры
            up = max(min(0.75, 0.16 + hs * 0.42) * peak, 0.03)
            g = self._glow_margin(cfg, hs)
            return (max(0.0, x0 - g), min(1.0, x1 + g),
                    max(0.0, yb - up - g), min(1.0, yb + 0.01 + g))
        if style in ("bars", "peak", "particles", "orbs", "mesh", "stack"):
            up = max(self.FIG_BARS * hs * 2.0 * peak, 0.03)
            g = self._glow_margin(cfg, hs)
            return (max(0.0, x0 - g), min(1.0, x1 + g),
                    max(0.0, yb - up - g), min(1.0, yb + 0.01 + g))
        # плавная волна (+ зеркало)
        up = max(self.FIG_SMOOTH * hs * 2.0 * peak, 0.03)
        bot = yb + up * 0.9 if cfg.get("mirror") else yb
        return x0, x1, max(0.0, yb - up), min(1.0, bot + 0.01)

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
            # 2) габариты фигуры — из того же расчёта, что и сама отрисовка
            zx0, zx1, ztop, zbot = self._extent(cfg)
            # небольшой запас на сглаживание/тряску линии (без цветового ключа)
            zx0 = max(0.0, zx0 - 0.01)
            zx1 = min(1.0, zx1 + 0.01)
            ztop = max(0.0, ztop - 0.02)
            zbot = min(1.05, zbot + 0.02)
            qx0, qx1 = _mx(zx0, zx1)
            qy0, qy1 = _my(ztop, zbot)
            # Маска не должна съедать кадр. Запас на свечение считается в
            # пикселях и при широком ореоле (свечение искр до 3.0) раздувает
            # окно на всю высоту кадра: вырезается ровно всё, замер падает, и
            # цвет замирает. Поэтому окно ужимается до не более 45% площади —
            # этого хватает, чтобы убрать саму фигуру, и оставляет сигнал.
            fxs = qx1 - qx0
            fys = qy1 - qy0
            if fxs * fys > 0.45:
                k = (0.45 / max(1e-6, fxs * fys)) ** 0.5
                cx, cy = (qx0 + qx1) * 0.5, (qy0 + qy1) * 0.5
                qx0, qx1 = cx - fxs * k * 0.5, cx + fxs * k * 0.5
                qy0, qy1 = cy - fys * k * 0.5, cy + fys * k * 0.5
            arr[(fx >= qx0) & (fx <= qx1) & (fy >= qy0) & (fy <= qy1)] = 0.0
            # Окно панели НЕ вырезается. Пользователь попросил прямо: из
            # кадра режется только сам оверлей, больше ничего. Когда панель
            # развёрнута во весь экран, на экране нет ничего, кроме неё, —
            # значит она и есть тот контент, цвет которого нужен. Вырезать
            # её было значило бы оставлять кадр пустым и не мерять вовсе.
        except Exception:
            # Раньше тут был голый pass: если расчёт габаритов падал, маска
            # просто не вырезалась, и об этом не узнавал никто. Теперь считаем
            # и пишем в лог — иначе поведение цвета выглядит «непонятным».
            self._stat["mask_err"] = self._stat.get("mask_err", 0) + 1
            log.exception("вырезание оверлея из кадра не удалось")

    def update_sysfx(self, S, has_sig, demo):
        """SYS-приближение/тряска всего экрана через Magnification API.
        DWM-трансформ — дорогая операция: в тишине сбрасываем 1:1 один раз
        и молчим, иначе зовём API не чаще 30 Гц и только при изменении."""
        if not S.get("sys_fx"):
            if getattr(self.sysfx, "active", False):
                self.sysfx.reset()
                self._sys_last_sent = None
            return
        # Лупа могла остаться неподнятой: приложение стартует с выключенным
        # sys_fx, а включить его можно в любой момент. ensure() поднимет её
        # здесь, а не только при запуске.
        if not self.sysfx.ensure():
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
        # Метка последнего тика: на неё смотрит сторож в web_main. По ней видно,
        # что оверлей перестал обновляться — из-за зависшего звука, зависшего
        # таймера или потерянного окна. Без неё «зависший» оверлей неотличим
        # от работающего, пока смотришь на картинку.
        self._last_tick = time.monotonic()
        self._fps_n += 1
        now = self._last_tick
        if now - self._fps_at >= 1.0:
            self._fps_real = self._fps_n / float(now - self._fps_at)
            self._fps_n = 0
            self._fps_at = now
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
        # Настоящий бас: энергия полосы 20-250 Гц прямо из FFT, без
        # автобалансировки и без сглаживания. Всё остальное, что есть в
        # приложении, — это уже выровненный спектр, а в нём удар баса
        # растянут на 15-20 кадров и неотличим от плавного подъёма. Пучок
        # на басу ловится только по этому значению.
        self._bass_raw = float(bass_raw)
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
        # Копия спектра ДО сглаживания по времени. Детектору темпа нужен
        # именно он: после attack/release удар становится пологим, и на
        # быстром темпе (160 BPM это 22.5 кадра на удар при 60 fps) половина
        # атак просто терялась. Сырой спектр — единственное место, где
        # скачок энергии ещё виден как скачок.
        self._beat_in = spec
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

        # ---------- темп и доля ----------
        # Кормим детектор нормализованным спектром. Он ловит удары по росту
        # энергии и сам ищет период повтора — это ровнее, чем порог по басу:
        # порог срабатывал дважды на один удар на треках с быстрым затуханием
        # и не срабатывал вовсе там, где бас широкий.
        #
        # Время отдаём шагом 1/fps, а не настенными часами: тогда темп не
        # плывёт, когда система подвисает. Если смотреть на time.time(), то при
        # просадке с 60 до 20 fps детектор решил бы, что музыка замедлилась
        # втрое, и перестал бы попадать в удары.
        fps_now = self._fps()
        if abs(fps_now - self._beat.fps) > 1.0:
            # сменили fps оверлея — пересобираем историю под новую частоту
            self._beat = beat_mod.BeatTracker(fps=fps_now)
        self._beat_t = getattr(self, "_beat_t", 0.0) + 1.0 / fps_now
        try:
            self._beat_fired = bool(self._beat.update(
                getattr(self, "_beat_in", self.lines), dt=1.0 / fps_now,
                has_sig=bool(has_sig or demo)))
        except Exception:
            self._beat_fired = False
        self._beat_pulse = float(self._beat.pulse(self._beat_t))
        # Число линий и плавность — настройки самой фигуры. Считаем один раз
        # здесь, до всех рисовалок: раньше каждая сглаживала спектр сама, а
        # круг, лучи, кольца, дуга и спираль не сглаживали вовсе, и число
        # линий у всех было общим — столбики баров и спицы круга не могли
        # различаться по числу.
        #
        # Результат кладём в отдельный буфер self.lines, а не в self.disp:
        # длина self.lines равна line_count и обычно не совпадает с длиной
        # спектра (bars). Если бы писали в self.disp, на следующем кадре
        # проверка «len(spec) != len(self.disp)» решила бы, что данные
        # сменились, обнулила буфер — и сглаживание атаки/отпускания перестало
        # бы работать. Рисунок дёргался по сырому спектру во всех стилях.
        self.lines = self._fit_lines(S, self.disp)
        # авто-скрытие
        if S.get("auto_hide") and not demo:
            self._silence = 0.0 if has_sig else self._silence + 1.0 / self._fps()
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
        self.step_sparks(S)
        self.update()

    # ---------- геометрия ----------
    def zone(self, W, H):
        S = self._cfg()
        w = W * float(S.get("viz_w", 0.92))
        x0 = max(0, min(W - w, W * float(S.get("viz_x", 0.5)) - w / 2))
        # Y — положение БАЗЫ фигуры, как и обещает подпись: 1.0 это самый низ
        # экрана, 0.0 самый верх. Раньше при включённом зеркале база
        # поднималась настолько, чтобы вторая половина влезла на экран, и для
        # крупных фигур Y=1 означал 58% высоты вместо низа — ползунок не
        # работал. Теперь ползунок выставляет ровно то, что написано; если
        # зеркало уходит за нижний край, это следствие Y=1, а не его порча.
        #
        # Делим не на H (высоту окна), а на высоту ВСЕГО экрана и прибавляем
        # сдвиг окна. Окно укорочено на полосу панели задач, и при делении на
        # H фигура целиком поднималась на эту полосу: Y=0.93 в окне 1050 px
        # попадал в 976, а не в 1004. Ползунок показывает «низ экрана», и
        # двигать его должен к низу экрана, а не к низу обрезанного окна.
        scr = self.screen()
        full_h = H
        top_off = 0
        if scr is not None:
            g = scr.geometry()
            if g.height() > 0:
                full_h = g.height()
            top_off = g.top() - self.y()
        y_full = float(np.clip(S.get("viz_y", 0.93), 0.0, 1.0)) * full_h + top_off
        # подстраховка от совсем вырожденных значений, ползунок такого не даёт
        return x0, float(np.clip(y_full, 0.0, H)), w

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
        # Полупрозрачное окно не очищает буфер между кадрами: без этого
        # каждый кадр ложится поверх предыдущего и волна оставляет бесконечный
        # гостинг — след не гаснет никогда. CompositionMode_Source затирает
        # кадр целиком, дальше рисуем как обычно.
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(0, 0, W, H, Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        n = len(self.lines)
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
        # режим цвета фигур считаем один раз на кадр, а не в каждом элементе
        self._cm, self._cc = self._shape_colors(S)
        # Ореол всей фигуры. У искр он свой, встроенный в частицы — если
        # добавить ещё и общий, свечение станет вдвое жирнее и вдвое дороже.
        if style != "sparks":
            try:
                sg = float(np.clip(S.get("shape_glow", 0.0), 0.0, 3.0))
            except Exception:
                sg = 0.0
            if sg > 0.02:
                self._draw_glow(p, W, H, n, alpha, style, sg)
        self._draw_figure(p, W, H, n, alpha, style)
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

    def _draw_figure(self, p, W, H, n, alpha, style):
        """Нарисовать выбранную фигуру. Вынесено из paintEvent, потому что
        тот же самый рисунок нужен для ореола: без общего метода пришлось бы
        дублировать все шестнадцать веток стиля."""
        if style == "bars":
            self.draw_bars(p, W, H, n, alpha)
        elif style == "peak":
            self.draw_bars(p, W, H, n, alpha, caps=True)
        elif style == "circle":
            self.draw_circle(p, W, H, n, alpha)
        elif style == "combo":
            self.draw_bars(p, W, H, n, alpha)
            self.draw_smooth(p, W, H, n)
        elif style == "fill":
            self.draw_fill(p, W, H, n)
        elif style == "ribbon":
            self.draw_ribbon(p, W, H, n)
        elif style == "radial":
            self.draw_radial(p, W, H, n, alpha)
        elif style == "rays":
            self.draw_rays(p, W, H, n, alpha)
        elif style == "particles":
            self.draw_particles(p, W, H, n, alpha)
        elif style == "sparks":
            self.draw_sparks(p, W, H, n, alpha)
        elif style == "arc":
            self.draw_arc(p, W, H, n, alpha)
        elif style == "spiral":
            self.draw_spiral(p, W, H, n, alpha)
        elif style == "mesh":
            self.draw_mesh(p, W, H, n, alpha)
        elif style == "stack":
            self.draw_stack(p, W, H, n, alpha)
        elif style == "orbs":
            self.draw_orbs(p, W, H, n, alpha)
        else:
            self.draw_smooth(p, W, H, n)

    def _blur_buf(self, img, radius, p, target):
        """Размыть маленький буфер и сразу наложить на холст.

        Считает QGraphicsBlurEffect, он же на C++. Свой разделимый гаусс на
        numpy выходил 7 мс на буфер 320x180, а Qt укладывается в 0.7 — в десять
        раз быстрее и без новой зависимости.

        Важно, что scene.render берёт и размытие, и растяжение за один проход:
        отдельно размывать в маленькую картинку и потом ещё раз растягивать
        через drawImage стоило бы лишних миллисекунд. Сцена и эффект создаются
        один раз — каждый кадр меняются только картинка и радиус.
        """
        try:
            if self._gfx is None:
                from PySide6.QtWidgets import (QGraphicsBlurEffect,
                                               QGraphicsPixmapItem,
                                               QGraphicsScene)
                scene = QGraphicsScene(self)
                item = QGraphicsPixmapItem()
                fx = QGraphicsBlurEffect()
                item.setGraphicsEffect(fx)
                scene.addItem(item)
                self._gfx = (scene, item, fx)
            scene, item, fx = self._gfx
            fx.setBlurRadius(max(0.01, float(radius)))
            item.setPixmap(QPixmap.fromImage(img))
            sw, sh = float(img.width()), float(img.height())
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            scene.render(p, target, QRectF(0.0, 0.0, sw, sh))
            p.restore()
        except Exception:
            # лучше ореол без размытия, чем вовсе ничего
            p.drawImage(target, img)

    def _draw_glow(self, p, W, H, n, alpha, style, glow):
        """Ореол вокруг всей фигуры — аналог CSS-овского drop-shadow.

        Настоящего размытия в QPainter нет. Первый вариант был такой: рисовать
        тот же рисунок вчетверо меньше и растягивать обратно, Qt при этом
        сглаживает. Ореола не получалось — растянутая копия ложилась ровно
        под фигуру и полностью ею закрывалась. Растянуть чуть больше нельзя:
        размытие тогда растёт как доля экрана, и на 1920 это сотни пикселей
        вместо положения в 10.

        Поэтому размываем по-настоящему, но в уменьшенном буфере: радиус
        получается маленьким, а вместе с ним и работа. Ореол рисуется ДО
        основной фигуры, иначе лёг бы поверх неё.
        """
        shrink = 4
        sw, sh = max(8, int(W) // shrink), max(8, int(H) // shrink)
        img = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p2 = QPainter(img)
        try:
            p2.scale(1.0 / shrink, 1.0 / shrink)
            p2.setRenderHint(QPainter.Antialiasing, False)
            # В буфере фигура рисуется в полном масштабе координат, но в
            # четверть пикселя: тонкая линия превращается в еле заметную
            # точку. Поднимаем альфу, иначе ореол был бы почти пустым.
            self._draw_figure(p2, W, H, n, min(255, int(alpha * 2.2)), style)
        except Exception:
            pass
        finally:
            p2.end()
        # Радиус в пикселях полного кадра: 100 -> 5, 300 -> 14. И пересчёт в буфер,
        # где он и правда маленький. При 96 полосах зазор между ними около
        # трёх пикселей, так что на «полосах» любой ореол шире зальёт
        # промежутки и сольёт их в сплошную массу — там держите 100.
        p.save()
        # Одно наложение с альфой. Раньше тут было три одинаковых drawImage,
        # но это даёт ровно ту же картинку, что и одно с суммарной альфой,
        # только втрое дороже.
        p.setOpacity(min(0.75, 0.22 + 0.16 * glow))
        self._blur_buf(img, (1.5 + 4.0 * glow) / shrink, p,
                       QRectF(0.0, 0.0, float(W), float(H)))
        p.restore()

    def _fill_path(self, xs, ys, y_base, S):
        """Залитая область под кривой. Стиль «контур» заливку не рисует —
        раньше верхняя половина при контуре оставалась пустой, а зеркало
        всё равно заливалось, и картинка получалась кривой."""
        if S.get("line_style") == "outline":
            return None
        path = QPainterPath()
        path.moveTo(xs[0], y_base)
        path.lineTo(xs[0], ys[0])
        for i in range(1, len(xs) - 1):
            xc = (xs[i] + xs[i + 1]) / 2
            yc = (ys[i] + ys[i + 1]) / 2
            path.quadTo(xs[i], ys[i], xc, yc)
        path.lineTo(xs[-1], ys[-1])
        path.lineTo(xs[-1], y_base)
        path.closeSubpath()
        return path

    def _stroke_line(self, p, xs, ys, y_base, S, n, bright, lw, fr, fg, fb, inward=1):
        """Обводка кривой выбранным стилем. Раньше стили жили в одной
        ветке else(), «ступени» там не обрабатывались и рисовались точно
        так же, как «неон», а «контур» — просто сплошной линией."""
        style = S.get("line_style", "thin")
        M = len(xs)
        alpha = int(S.get("alpha", 230))

        if style == "thin":
            pen = QPen(bright)
            pen.setWidth(1)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for i in range(M - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            return

        if style == "gradient":
            pal = self.palette if len(self.palette) == n else build_palette("neon", (0, 220, 255), n)
            for i in range(M - 1):
                pi = int(i / max(1, M - 2) * (n - 1))
                r, g, b = pal[min(pi, len(pal) - 1)]
                pen = QPen(QColor(r, g, b, alpha))
                pen.setWidth(lw)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            return

        if style == "dotted":
            p.setPen(QPen(QColor(fr, fg, fb, 70)))
            for i in range(M - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(bright))
            step = max(2, M // 48)
            rad = max(2, lw + 1)
            for i in range(0, M, step):
                p.drawEllipse(QRectF(float(xs[i]) - rad / 2, float(ys[i]) - rad / 2,
                                 float(rad), float(rad)))
            return

        if style == "steps":
            # настоящая лестница: высота держится до правого края ступени
            seg = max(2, M // 24)
            fa = int(S.get("fill_alpha", 90))
            for i in range(0, M - 1, seg):
                j = min(M - 1, i + seg)
                xa, xb = int(xs[i]), int(xs[j])
                top = int(ys[i])
                bot = int(y_base)
                p.fillRect(xa, top, max(1, xb - xa), max(1, bot - top),
                           QColor(fr, fg, fb, fa))
            pen = QPen(bright)
            pen.setWidth(lw)
            pen.setCapStyle(Qt.FlatCap)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            for i in range(0, M - 1, seg):
                j = min(M - 1, i + seg)
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[j]), int(ys[j]))
            return

        if style in ("fat", "megaglow"):
            pen = QPen(bright)
            pen.setWidth(lw * 2 + 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for i in range(M - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            if style == "megaglow":
                core = QPen(QColor(255, 255, 255, alpha))
                core.setWidth(max(1, lw - 1))
                core.setCapStyle(Qt.RoundCap)
                p.setPen(core)
                for i in range(M - 1):
                    p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            return

        # neon / double / dash / outline — всё, что сводится к сплошной линии
        pen = QPen(bright if style != "outline" else QColor(fr, fg, fb, alpha))
        pen.setWidth(lw if style != "double" else max(1, lw - 1))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if style == "dash":
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        for i in range(M - 1):
            p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
        if style == "double":
            # вторая линия смещена внутрь фигуры, а не всегда вниз
            off = 9 * inward
            pen2 = QPen(QColor(fr, fg, fb, max(60, alpha - 90)))
            pen2.setWidth(max(1, lw - 1))
            pen2.setCapStyle(Qt.RoundCap)
            p.setPen(pen2)
            for i in range(M - 1):
                p.drawLine(int(xs[i]), int(ys[i] + off), int(xs[i + 1]), int(ys[i + 1] + off))

    def draw_smooth(self, p, W, H, n):
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        bass = self._bass
        pulse = (1 + bass * float(S.get("beat_strength", 0.35)) * 1.5) if S.get("beat_pulse") else 1.0
        max_h = H * 0.38 * float(S.get("height_scale", 0.55)) * 2 * pulse
        s = np.asarray(self.lines, dtype=np.float32)
        M = 160
        xp = np.linspace(0, 1, len(s))
        xq = np.linspace(0, 1, M)
        yq = np.interp(xq, xp, s).astype(np.float32)
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
        path = self._fill_path(xs, ys, y_base, S)
        if path is not None:
            p.fillPath(path, QBrush(grad))
        lw = int(S.get("line_width", 3))
        bright = QColor(min(255, fr + 40), min(255, fg + 40), min(255, fb + 40), int(S.get("alpha", 230)))
        self._stroke_line(p, xs, ys, y_base, S, n, bright, lw, fr, fg, fb, inward=1)
        if mirror_ys is not None:
            grad2 = QLinearGradient(0, y_base, 0, y_base + max_h)
            grad2.setColorAt(0, QColor(fr, fg, fb, min(255, fa + 40)))
            grad2.setColorAt(1, QColor(fr, fg, fb, max(0, fa - 50)))
            path2 = self._fill_path(xs, mirror_ys, y_base, S)
            if path2 is not None:
                p.fillPath(path2, QBrush(grad2))
            # зеркало обводится тем же стилем, иначе при «контуре» и «двойной»
            # нижняя половина оставалась совсем без линии
            self._stroke_line(p, xs, mirror_ys, y_base, S, n, bright, lw, fr, fg, fb, inward=-1)
        p.setPen(QPen(QColor(255, 255, 255, 45)))
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def _rgb(self, S):
        """Цвет волны с учётом эффектов дыхания и пульса."""
        fr, fg, fb = (int(v) for v in np.clip(self.sc.shown, 0, 255))
        if S.get("color_fx") in ("breathe", "beat"):
            m = float(getattr(self.sc, "_breathe", 1.0)) * float(getattr(self.sc, "_beat_boost", 1.0))
            fr, fg, fb = min(255, int(fr * m)), min(255, int(fg * m)), min(255, int(fb * m))
        return fr, fg, fb

    def _shape_colors(self, S):
        """Режим цвета фигур — бары, круг, лучи и прочие.

        Раньше они всегда брали фиксированную палитру из «цвета баров» и не
        реагировали на картинку, хотя волна реагировала: оверлей выглядел
        наполовину живым.

          screen  — цвет с экрана, как у волны;
          tint    — палитра, подкрашенная цветом экрана;
          palette — как раньше, своя палитра.

        Если замер экрана выключен, брать нечего — оба «живые» режима
        откатываются в палитру, иначе фигуры погасли бы в чёрный."""
        mode = S.get("shape_color", "screen")
        if mode == "palette" or not S.get("follow_screen", True):
            self._apply_dark(self.palette, None, S, "palette")
            return "palette", None
        r, g, b = self._rgb(S)
        m = "solid" if mode == "screen" else "tint"
        self._apply_dark(self.palette, (r, g, b), S, m)
        return (m, (r, g, b))

    def _apply_dark(self, pal, cc, S, mode):
        """Посчитать поднятые цвета фигуры один раз на кадр.

        Раньше ограничение темноты жило только в коде искр, и остальные
        пятнадцать фигур его не видели. Теперь поднимается вся палитра целиком,
        один раз за кадр: для каждой фигуры поштучно считать было бы пять
        сотен вычислений вместо одного вектора, а искры — отдельная копия
        этого же кода, которая со временем разъехалась бы с общей.
        """
        try:
            dark = float(np.clip(S.get("color_dark", 0.25), 0.0, 1.0))
        except (TypeError, ValueError):
            dark = 0.25
        n = len(pal) or 1
        if cc is not None and mode == "solid":
            base = np.tile(np.asarray(cc, dtype=np.float32), (n, 1))
        else:
            base = np.asarray(pal[:n], dtype=np.float32).reshape(n, 3)
            if cc is not None and mode == "tint":
                base = base * 0.4 + np.asarray(cc, dtype=np.float32) * 0.6
        self._base = lift_rgb(base, dark)

    def _shape_rgb(self, i):
        """Базовый цвет элемента i, без учёта его громкости.

        Берём из self._base — палитру, уже поднятую ограничением темноты в
        _shape_colors. Отдельный режим «solid» не нужен: там все элементы
        одного цвета, и в _base он и так размножен на все строки.
        """
        base = getattr(self, "_base", None)
        if base is None or not len(base):
            pal = self.palette
            if not len(pal):
                return (255, 255, 255)
            r, g, b = pal[i] if i < len(pal) else pal[-1]
            return float(r), float(g), float(b)
        row = base[i] if i < len(base) else base[-1]
        return float(row[0]), float(row[1]), float(row[2])

    def _bar_color(self, i, v, alpha):
        """Цвет элемента фигуры: базовый цвет по режиму, затем яркость от
        значения спектра. Раньше брался pal[i] без проверки длины — при
        рассинхроне disp и палитры (аудио ещё не подхватило новую длину
        bars) это роняло paintEvent с IndexError."""
        r, g, b = self._shape_rgb(i)
        boost = 0.55 + min(1.0, float(v)) * 0.7
        return QColor(min(255, int(r * boost)), min(255, int(g * boost)),
                      min(255, int(b * boost)), alpha)

    def _bar_heights(self, S, n, H):
        """Высоты столбиков со сглаживанием и пульсом — как у волны.
        Раньше бары шли сырым спектром без сглаживания и без пульса, из-за
        чего картинка дёргалась и отличалась от волны при тех же ползунках."""
        d = np.asarray(self.lines, dtype=np.float32)[:n]
        if len(d) < n:
            d = np.pad(d, (0, n - len(d)), mode="edge" if len(d) else "constant")
        s = d
        pulse = (1 + self._bass * float(S.get("beat_strength", 0.35)) * 1.5) if S.get("beat_pulse") else 1.0
        max_h = H * self.FIG_BARS * float(S.get("height_scale", 0.55)) * 2 * pulse
        return np.clip(s, 0.0, 1.6) * max_h

    def draw_bars(self, p, W, H, n, alpha, caps=False):
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        gap = 3
        bw = max(2.0, (w - gap * (n + 1)) / n)
        hs = self._bar_heights(S, n, H)
        rad = max(1.0, min(3.0, bw / 2.5))
        p.setPen(Qt.NoPen)
        for i in range(n):
            h = max(2.0, float(hs[i]))
            x = x0 + gap + i * (bw + gap)
            v = float(self.lines[i]) if i < len(self.lines) else 0.0
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y_base - h, bw, h), rad, rad)
            p.fillPath(path, QBrush(self._bar_color(i, v, alpha)))
        if caps:
            self._draw_peak_hold(p, x0, y_base, n, hs, bw, gap, alpha)
        pen = QPen(QColor(255, 255, 255, 60))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def _draw_peak_hold(self, p, x0, y_base, n, hs, bw, gap, alpha):
        """Пиковые колпачки как у VU-метра: держат максимум и медленно падают."""
        pk = getattr(self, "_peaks", None)
        if pk is None or len(pk) != n:
            pk = np.zeros(n, dtype=np.float32)
        pk = np.where(hs >= pk, hs, pk * 0.94).astype(np.float32)
        self._peaks = pk
        top = float(np.max(hs)) if len(hs) else 1.0
        for i in range(n):
            h = float(pk[i])
            if h < 3.0:
                continue
            x = x0 + gap + i * (bw + gap)
            c = self._bar_color(i, min(1.0, h / top) if top > 1.0 else 1.0, alpha)
            c.setAlpha(min(255, alpha + 60))
            p.fillRect(QRectF(x, y_base - h - 4, bw, 3), QBrush(c))

    def draw_circle(self, p, W, H, n, alpha):
        S = self._cfg()
        cx, cy, base, span = self._polar_geometry(W, H, S)
        base_r = base * (1 + self._level * 0.12)
        pen = QPen()
        pen.setWidth(3)
        pen.setCapStyle(Qt.RoundCap)
        for i in range(n):
            v = float(self.lines[i]) if i < len(self.lines) else 0.0
            ang = 2 * math.pi * i / n - math.pi / 2
            r1 = base_r
            r2 = base_r + 4 + v * span
            pen.setColor(self._bar_color(i, v, alpha))
            p.setPen(pen)
            p.drawLine(int(cx + r1 * math.cos(ang)), int(cy + r1 * math.sin(ang)),
                       int(cx + r2 * math.cos(ang)), int(cy + r2 * math.sin(ang)))
        pen.setColor(QColor(255, 255, 255, 70))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawEllipse(QRectF(cx - base_r, cy - base_r, base_r * 2, base_r * 2))

    # ---------- новые вариации ----------
    def _wave_curve(self, S, n, M):
        """Общая подготовка кривой: спектр -> сглаженный вектор долей (xq, yq)."""
        d = np.asarray(self.lines, dtype=np.float32)[:n]
        if len(d) < n:
            d = np.pad(d, (0, n - len(d)), mode="edge" if len(d) else "constant")
        s = d
        xp = np.linspace(0, 1, len(s))
        xq = np.linspace(0, 1, M)
        yq = np.interp(xq, xp, s).astype(np.float32)
        return xq, yq

    def _pulse(self, S):
        return (1 + self._bass * float(S.get("beat_strength", 0.35)) * 1.5) if S.get("beat_pulse") else 1.0

    def draw_fill(self, p, W, H, n):
        """Заливка: спектр симметрично вверх и вниз от базовой линии.
        В отличие от «волны + зеркало» симметрия тут встроена, поэтому не
        зависит от тумблера mirror и не разъезжается с маской замера."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        M = max(8, min(320, int(w // 3)))
        xq, yq = self._wave_curve(S, n, M)
        max_h = H * self.FIG_SMOOTH * float(S.get("height_scale", 0.55)) * 2 * self._pulse(S)
        # симметричная линза: приподнять, чтобы нижняя половина не уехала
        # за нижний край экрана (y_base по умолчанию 0.93)
        cy_px = self._fit_center(y_base, max_h * 0.925, H)
        max_h = min(max_h, max(0.02 * H, cy_px))
        yq = np.clip(yq, 0.0, 1.6)
        xs = x0 + xq * w
        top = cy_px - yq * max_h
        bot = cy_px + yq * max_h * 0.85
        fr, fg, fb = self._rgb(S)
        fa = int(S.get("fill_alpha", 90))
        grad = QLinearGradient(0, cy_px - max_h, 0, cy_px + max_h)
        grad.setColorAt(0.0, QColor(fr, fg, fb, max(0, fa - 30)))
        grad.setColorAt(0.5, QColor(fr, fg, fb, min(255, fa + 80)))
        grad.setColorAt(1.0, QColor(fr, fg, fb, max(0, fa - 50)))
        path = QPainterPath()
        path.moveTo(xs[0], top[0])
        for i in range(1, M - 1):
            path.quadTo(xs[i], top[i], (xs[i] + xs[i + 1]) / 2, (top[i] + top[i + 1]) / 2)
        path.lineTo(xs[-1], cy_px)
        for i in range(M - 2, 0, -1):
            path.quadTo(xs[i], bot[i], (xs[i] + xs[i - 1]) / 2, (bot[i] + bot[i - 1]) / 2)
        path.lineTo(xs[0], cy_px)
        path.closeSubpath()
        if S.get("line_style") != "outline":
            p.fillPath(path, QBrush(grad))
        lw = int(S.get("line_width", 3))
        bright = QColor(min(255, fr + 40), min(255, fg + 40), min(255, fb + 40), int(S.get("alpha", 230)))
        self._stroke_line(p, xs, top, cy_px, S, n, bright, lw, fr, fg, fb, inward=1)
        self._stroke_line(p, xs, bot, cy_px, S, n, bright, lw, fr, fg, fb, inward=-1)
        # ось заливки: при «контуре» обе кромки сходятся и линия поверх них
        # читалась серой полосой поперёк всей фигуры
        p.setPen(QPen(QColor(255, 255, 255, 22)))
        p.drawLine(int(x0), int(cy_px), int(x0 + w), int(cy_px))

    def draw_ribbon(self, p, W, H, n):
        """Лента: горизонтальная полоса, толщина которой идёт по спектру.
        Заливка поцветная, из палитры — как у аппаратных лент."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        M = max(8, min(360, int(w // 3)))
        xq, yq = self._wave_curve(S, n, M)
        th = H * self.FIG_RIBBON * float(S.get("height_scale", 0.55)) * (0.22 + np.clip(yq, 0.0, 1.4))
        xs = x0 + xq * w
        # лента лежит на базовой линии, поэтому поднимаем её целиком вверх —
        # иначе нижняя кромка уходила за край экрана и обрезалась
        th_max = H * self.FIG_RIBBON * float(S.get("height_scale", 0.55)) * (0.22 + min(1.4, float(np.max(np.clip(yq, 0.0, 1.4)))))
        cy_px = self._fit_center(y_base, th_max, H)
        top = cy_px - th
        bot = cy_px + th * 0.8
        alpha = int(S.get("alpha", 230))
        p.setPen(Qt.NoPen)
        for i in range(M - 1):
            pi = int(i / max(1, M - 2) * (n - 1))
            r, g, b = self._shape_rgb(pi)
            path = QPainterPath()
            path.moveTo(xs[i], top[i])
            path.lineTo(xs[i + 1], top[i + 1])
            path.lineTo(xs[i + 1], bot[i + 1])
            path.lineTo(xs[i], bot[i])
            path.closeSubpath()
            p.fillPath(path, QBrush(QColor(r, g, b, alpha)))
        lw = int(S.get("line_width", 3))
        fr, fg, fb = self._rgb(S)
        bright = QColor(255, 255, 255, int(S.get("alpha", 230)))
        self._stroke_line(p, xs, top, cy_px, S, n, bright, lw, fr, fg, fb, inward=1)
        self._stroke_line(p, xs, bot, cy_px, S, n, bright, lw, fr, fg, fb, inward=-1)

    # ---------- вариации на круглых и сеточных фигурах ----------
    def _polar_geometry(self, W, H, S):
        """Центр и масштаб круглых фигур: дуга, спираль, кольца, лучи, круг.

        Раньше центр был намертво в середине экрана и размер брался из
        высоты — сдвинуть или увеличить круг было нечем. Теперь положение
        и размер настраиваются (fig_x / fig_y / fig_size)."""
        short = float(min(W, H))
        hs = float(S.get("height_scale", 0.55))
        size = float(np.clip(S.get("fig_size", 1.0), 0.2, 3.0))
        return (W * float(np.clip(S.get("fig_x", 0.5), 0.0, 1.0)),
                H * float(np.clip(S.get("fig_y", 0.5), 0.0, 1.0)),
                short * self.FIG_CIRCLE * (0.6 + hs) * size,
                short * self.FIG_SPOKE * hs * 2.0 * size)

    def draw_arc(self, p, W, H, n, alpha):
        """Дуга: спектр разложен по дуге окружности. От «круга» отличается
        незамкнутостью и размахом по углу — читается как радар."""
        S = self._cfg()
        cx, cy, base, span = self._polar_geometry(W, H, S)
        pulse = self._pulse(S)
        span_ang = math.radians(260.0)
        start = -math.pi / 2 - span_ang / 2
        lw = max(2.0, min(W, H) * 0.006)
        r0 = base * 0.92 + 4
        p.setBrush(Qt.NoBrush)
        for i in range(n):
            v = (float(self.lines[i]) if i < len(self.lines) else 0.0) * pulse
            a0 = start + span_ang * (i / n)
            a1 = start + span_ang * ((i + 1) / n)
            r1 = r0 + v * span * 0.8
            pen = QPen(self._bar_color(i, v, alpha))
            pen.setWidth(lw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(int(cx + r0 * math.cos(a0)), int(cy + r0 * math.sin(a0)),
                       int(cx + r1 * math.cos(a1)), int(cy + r1 * math.sin(a1)))
        guide = QPen(QColor(255, 255, 255, 34))
        guide.setWidth(1)
        p.setPen(guide)
        p.drawArc(QRectF(cx - r0, cy - r0, r0 * 2, r0 * 2),
                  int(-math.degrees(start) * 16), int(math.degrees(span_ang) * 16))

    def draw_spiral(self, p, W, H, n, alpha):
        """Спираль: спектр намотан по Archimedes. Радиус растёт с углом и
        дышит по спектру — единственная фигура, у которой длина сама растёт."""
        S = self._cfg()
        cx, cy, base, span = self._polar_geometry(W, H, S)
        pulse = self._pulse(S)
        turns = 2.6
        steps = max(64, n * 3)
        lw = max(1.5, min(W, H) * 0.004)
        pts = []
        for s in range(steps + 1):
            t = s / steps
            ang = -math.pi / 2 + t * turns * 2 * math.pi
            i = min(n - 1, int(t * n))
            v = (float(self.lines[i]) if i < len(self.lines) else 0.0) * pulse
            r = base * 0.22 + t * base * 1.05 + v * span * 0.30
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang), i, v))
        p.setBrush(Qt.NoBrush)
        for s in range(steps):
            x1, y1, i1, v1 = pts[s]
            x2, y2 = pts[s + 1][0], pts[s + 1][1]
            pen = QPen(self._bar_color(i1, v1, alpha))
            pen.setWidth(lw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

    def draw_mesh(self, p, W, H, n, alpha):
        """Сетка: столбцы — частоты, строки — уровень. Каждая частота
        заполняет снизу столько ячеек, сколько набрала громкости."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        rows = 7
        d = np.asarray(self.lines, dtype=np.float32)[:n]
        if len(d) < n:
            d = np.pad(d, (0, n - len(d)), mode="edge" if len(d) else "constant")
        d = np.clip(d, 0.0, 1.4)
        gap = 3
        cw = max(1.5, (w - gap * (n + 1)) / n)
        hs = float(S.get("height_scale", 0.55))
        row_h = H * self.FIG_BARS * hs * 2.0 / rows
        p.setPen(Qt.NoPen)
        for i in range(n):
            x = x0 + gap + i * (cw + gap)
            cells = int(min(rows, round(float(d[i]) * rows)))
            for rw in range(cells):
                # чем выше ячейка, тем ярче — объём читается без обводки
                f = (rw + 1) / rows
                a = int(alpha * (0.32 + 0.68 * f))
                p.setBrush(QBrush(self._bar_color(i, f, max(0, min(255, a)))))
                p.drawRect(QRectF(x, y_base - (rw + 1) * row_h, cw, row_h * 0.8))

    def draw_stack(self, p, W, H, n, alpha):
        """Слои: спектр, нарезанный на полосы по частотам, каждая полоса —
        свой участок и свой оттенок. В отличие от «слоёв по высоте» не
        зависит от того, где на спектре сейчас энергия."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        bands = 4
        M = max(16, min(360, int(w // 2)))
        hs = float(S.get("height_scale", 0.55))
        total = H * self.FIG_BARS * hs * 1.35
        band_h = total / bands
        d = np.asarray(self.lines, dtype=np.float32)[:n]
        if len(d) < n:
            d = np.pad(d, (0, n - len(d)), mode="edge" if len(d) else "constant")
        p.setBrush(Qt.NoBrush)
        for bi in range(bands):
            lo, hi = bi * n // bands, max(bi * n // bands + 1, (bi + 1) * n // bands)
            seg = np.clip(self.spatial_smooth(d[lo:hi], 0.35), 0.0, 1.5)
            if not len(seg):
                continue
            # каждая полоса нормируется по своему пику, иначе верхние
            # полосы (тихие верха) прижимались к базовой линии и читались
            # как ровные линейки, а слои переставали различаться
            seg = seg / max(0.08, float(np.max(seg)))
            xq = np.linspace(0, 1, M)
            yq = np.interp(xq, np.linspace(0, 1, len(seg)), seg).astype(np.float32)
            xs = x0 + xq * w
            base_y = y_base - (bands - 1 - bi) * band_h
            ys = base_y - yq * band_h * 0.92
            path = QPainterPath()
            path.moveTo(xs[0], base_y)
            for k in range(1, M - 1):
                path.quadTo(xs[k], ys[k], (xs[k] + xs[k + 1]) / 2, (ys[k] + ys[k + 1]) / 2)
            path.lineTo(xs[-1], base_y)
            path.closeSubpath()
            c = self._shape_rgb(min(n - 1, lo + (hi - lo) // 2))
            p.fillPath(path, QBrush(QColor(min(255, int(c[0])), min(255, int(c[1])),
                                           min(255, int(c[2])), int(alpha * 0.30))))
            pen = QPen(QColor(min(255, int(c[0] + 60)), min(255, int(c[1] + 60)),
                              min(255, int(c[2] + 60)), min(255, alpha)))
            pen.setWidth(2)
            p.setPen(pen)
            for k in range(M - 1):
                p.drawLine(int(xs[k]), int(ys[k]), int(xs[k + 1]), int(ys[k + 1]))

    def draw_orbs(self, p, W, H, n, alpha):
        """Шарики: круги с радиусом по спектру, полупрозрачные — в местах
        перекрытия цвет набирает плотность."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        gap = 2
        cw = max(3.0, (w - gap * (n + 1)) / n)
        pulse = self._pulse(S)
        p.setPen(Qt.NoPen)
        for i in range(n):
            v = (float(self.lines[i]) if i < len(self.lines) else 0.0) * pulse
            vc = min(1.2, max(0.0, v))
            # радиус держим в пределах шага: иначе на басах шарики слипались
            # в одно пятно и фигура превращалась в кляксу
            rad = cw * (0.5 + 1.4 * min(1.0, vc))
            x = x0 + gap + i * (cw + gap) + cw / 2
            y = y_base - rad
            p.setBrush(QBrush(self._bar_color(i, v, int(alpha * 0.40))))
            p.drawEllipse(QRectF(x - rad, y - rad, rad * 2, rad * 2))
        pen = QPen(QColor(255, 255, 255, 45))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def draw_radial(self, p, W, H, n, alpha):
        """Кольца: вложенные полярные кривые, у каждой свой кусок спектра.

        Радиус идёт по спектру вдоль угла, поэтому круг распускается на
        ударах. Сделано замкнутой кривой, а не спицами — иначе «кольца» были
        неотличимы от «лучей», а это две разные вариации."""
        S = self._cfg()
        cx, cy, base, span = self._polar_geometry(W, H, S)
        pulse = self._pulse(S)
        short = float(min(W, H))
        rings = 4
        per = max(24, n // rings)
        amp = span * 0.9
        lw = max(2.0, short * 0.005)
        p.setBrush(Qt.NoBrush)
        for ri in range(rings):
            r0 = base * (0.30 + 0.32 * ri)
            # опорная окружность — видно, где кольцо покоится
            guide = QPen(QColor(255, 255, 255, 38))
            guide.setWidth(1)
            p.setPen(guide)
            p.drawEllipse(QRectF(cx - r0, cy - r0, r0 * 2, r0 * 2))
            pts = []
            for k in range(per):
                i = int(k / per * n)
                v = min(1.4, (float(self.lines[i]) if i < len(self.lines) else 0.0) * pulse)
                ang = 2 * math.pi * k / per - math.pi / 2
                rr = r0 + 4 + v * amp * (0.28 + 0.20 * ri)
                pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang), i, v))
            for k in range(per):
                x1, y1, i1, v1 = pts[k]
                x2, y2 = pts[(k + 1) % per][0], pts[(k + 1) % per][1]
                pen = QPen(self._bar_color(i1, v1, alpha))
                pen.setWidth(lw)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(int(x1), int(y1), int(x2), int(y2))

    def draw_rays(self, p, W, H, n, alpha):
        """Лучи: спицы из центра экрана, длина — по спектру."""
        S = self._cfg()
        cx, cy, base, span = self._polar_geometry(W, H, S)
        r0 = base * 0.35
        ln_max = span * 1.6
        pulse = self._pulse(S)
        lw = max(1, int(S.get("line_width", 3)))
        for i in range(n):
            v = float(self.lines[i]) * pulse if i < len(self.lines) else 0.0
            ang = 2 * math.pi * i / n - math.pi / 2
            r2 = r0 + v * ln_max
            c = self._bar_color(i, v, alpha)
            pen = QPen(c)
            pen.setWidth(lw)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(int(cx + r0 * math.cos(ang)), int(cy + r0 * math.sin(ang)),
                       int(cx + r2 * math.cos(ang)), int(cy + r2 * math.sin(ang)))

    def draw_particles(self, p, W, H, n, alpha):
        """Частицы: точки-пружинки на базовой линии. Позиция интегрируется
        отдельно от спектра, поэтому видно инерцию — точка догоняет столбик
        и по инерции перелетает его."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        gap = 3
        bw = max(2.0, (w - gap * (n + 1)) / n)
        max_h = H * self.FIG_BARS * float(S.get("height_scale", 0.55)) * 2 * self._pulse(S)
        d = np.asarray(self.lines, dtype=np.float32)[:n]
        if len(d) < n:
            d = np.pad(d, (0, n - len(d)), mode="edge" if len(d) else "constant")
        target = np.clip(d, 0.0, 1.5)
        pos = getattr(self, "_ppos", None)
        vel = getattr(self, "_pvel", None)
        if pos is None or len(pos) != n:
            pos = target.copy()
            vel = np.zeros(n, dtype=np.float32)
        stiff = float(np.clip(0.55 - S.get("line_smooth", 0.0) * 0.35, 0.05, 0.55))
        vel = (vel * 0.80 + (target - pos) * stiff).astype(np.float32)
        pos = np.clip(pos + vel, 0.0, 1.5).astype(np.float32)
        self._ppos, self._pvel = pos, vel
        p.setPen(Qt.NoPen)
        # Кисти и цвета создаются заранее: раньше на каждый шарик на каждом
        # кадре выделялись QBrush и QColor — это сотни мелких объектов в
        # секунду рядом с потоками BLE и звука, где это добавляет риск
        # падения нативного кода.
        # QBrush() по умолчанию NoBrush: setColor() не меняет узор, и
        # без SolidPattern ничего не рисуется.
        brush = QBrush(Qt.SolidPattern)
        plain = QBrush(Qt.SolidPattern)
        for i in range(n):
            v = float(pos[i])
            x = x0 + gap + i * (bw + gap) + bw / 2
            y = y_base - v * max_h
            rad = max(1.5, bw * 0.42)
            c = self._bar_color(i, v, alpha)
            glow = QColor(c)
            glow.setAlpha(max(0, alpha // 3))
            brush.setColor(glow)
            p.setBrush(brush)
            p.drawEllipse(QRectF(x - rad * 2.1, y - rad * 2.1, rad * 4.2, rad * 4.2))
            plain.setColor(c)
            p.setBrush(plain)
            p.drawEllipse(QRectF(x - rad, y - rad, rad * 2, rad * 2))
        pen = QPen(QColor(255, 255, 255, 60))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))

    def step_sparks(self, S):
        """Шаг искр: выпуск, полёт, гибель. Отдельно от отрисовки.

        Физика живёт здесь, а не в draw_sparks, и это не деление ради деления.
        Если шагать прямо в paintEvent, частицы двигаются только когда Qt
        удосужится перерисовать окно: оверлей скрыт настройкой «скрывать при
        тишине», окно свёрнуто или система держит его в фоне — и искры
        замирают в воздухе, а потом разом срываются. Плюс стенд, который
        зовёт tick() в цикле без реального времени, вообще не мог проверить
        полёт: за 150 кадров проходило 0.05 с, частицы лежали на базовой
        линии, и «плотные частицы» выглядели как редкие брызги.

        Здесь шаг привязан к тику, как у остального: тик идёт по таймеру
        overlay_fps, dt настоящий, частицы всегда в своём месте.
        """
        W, H = int(self.width()), int(self.height())
        # Бюджет частиц = line_count × 4, потолок 2048.
        #
        # Потолок стоял 512, и он был не декоративным: на «Количество линий» 256
        # (максимум ползунка) нужное место равнялось 1024, обрезалось до 512 —
        # и ползунок плотности выше ~2.6 не делал ровно ничего, буфер был полон
        # и без того. Теперь верх достаточно высокий, чтобы ползунок плотности
        # до 8.0 имел смысл.
        #
        # 2048 — это 8 МБ на восемь массивов float32, само по себе дёшево. Дорого
        # отрисовка: три прохода ореола на частицу при свечении, поэтому на
        # 2048 частицах с сильным свечением кадр будет тяжёлым. Об этом в
        # README, там же — сколько fps на скольких частицах.
        cap = int(np.clip(S.get("line_count", 64) * 4, 32, 2048))
        st = getattr(self, "_spark", None)
        if st is None or st["cap"] != cap:
            st = {"cap": cap, "t": time.time(),
                  "x": np.zeros(cap, np.float32), "y": np.zeros(cap, np.float32),
                  "vy": np.zeros(cap, np.float32), "life": np.zeros(cap, np.float32),
                  "full": np.ones(cap, np.float32),
                  "amp": np.zeros(cap, np.float32),
                  "bin": np.zeros(cap, np.int32), "head": 0, "prev": 0.0,
                  "acc": 0.0, "facc": 0.0,
                  "tau": np.full(cap, 0.42, np.float32)}
            self._spark = st

        now = time.time()
        dt = float(np.clip(now - st["t"], 0.0, 0.1))   # тик мог подвиснуть
        st["t"] = now
        x0, y_base, w = self.zone(W, H)
        # Высота полёта. Раньше этот же height_scale задавал ещё и размер
        # частицы, и одна ручка дёргала сразу обе величины: чтобы сделать
        # искры крупнее, приходилось поднимать и высоту. Теперь размер —
        # отдельный ползунок spark_size, а здесь только высота.
        hs = float(np.clip(S.get("height_scale", 0.55), 0.01, 2.0))
        n = len(self.lines)
        # Скорость подъёма: ползунок. Меняет и толчок, и торможение, поэтому
        # быстрые искры не просто улетают выше, а ещё и живут заметно меньше —
        # иначе на большой скорости они улетали бы за экран и тухли там.
        spd = float(np.clip(S.get("spark_speed", 1.0), 0.05, 4.0))
        # Насколько сильно громкая частота разгоняет искры. Это отдельная от
        # базовой скорости ручка: базовая отвечает за «фон», boost — за то,
        # насколько колонка выстреливает вверх на своём пике.
        boost = float(np.clip(S.get("spark_boost", 1.0), 0.0, 6.0))
        # Плотность: во сколько раз больше частиц при том же бюджете.
        dens = float(np.clip(S.get("spark_density", 1.0), 0.15, 8.0))
        # Сколько частиц в одном всплеске: множитель к росту спектра.
        # Раньше был зашит как 0.7 — подобрать густоту пучка можно было
        # только правкой кода.
        burst_n = float(np.clip(S.get("spark_burst_count", 0.7), 0.0, 8.0))
        # Потолок подъёма всплеска, в долях высоты экрана. Частицы быстрого
        # потока не летят выше этого уровня: сначала считывается vy, потом
        # скорость срезается до потолка. Раньше предела не было вовсе, и на
        # разгоне 3 быстрые искры уходили на 2100 px при экране 720 — за
        # верхний край, где их не видно.
        burst_h = float(np.clip(S.get("spark_burst_height", 0.35), 0.02, 0.95))

        # --- где вылетать: по всей площади, а громкая частота горит ярче ---
        # Искры выпускаются по всей ширине, но место выбирается не равномерно,
        # а по весу, равному высоте спектра: у высокой частоты частиц больше и
        # они крупнее. Поэтому столбик частоты читается прямо в искрах — там,
        # где музыка громче, фонтан плотнее и выше, а где тихо, одни брызги.
        #
        # Раньше была одна точка вылета (центр тяжести спектра), и фигура
        # читалась как один столб дыма в случайном месте.
        vals = np.asarray(self.lines, dtype=np.float32) if n else np.zeros(1, np.float32)
        vals = np.clip(vals, 0.0, 1.4)
        # Вес полосы = её высота плюс постоянная доля. Без этой добавки тихие
        # полосы почти не получали частиц вовсе, и по краям экрана было пусто:
        # искры шли по всей площади только там, где музыка громкая. С добавкой
        # высота задаёт ПЛОТНОСТЬ (громкая — густо), но не само наличие.
        weights = 0.30 + vals
        tot = float(weights.sum())
        if tot > 1e-6:
            cdf = np.cumsum(weights, dtype=np.float64)
            cdf /= cdf[-1]
        else:
            cdf = None
        peak = float(vals.max()) if len(vals) else 0.0

        # --- насколько полосы выросли с прошлого тика ---
        # Это и есть всплеск: не громкость вообще, а скачок конкретной
        # частоты. Медленные фоновые частицы на него не смотрят, а быстрые
        # вылетают именно оттуда и бьют тем сильнее, чем выше был скачок.
        # Нормируем на максимум роста, иначе одна громкая полоса задавала бы
        # скорость всем, а тихие всплески не летели бы совсем.
        pv = st.get("prev_vals")
        if pv is None or len(pv) != n:
            pv = st["prev_vals"] = vals.copy()
            rise = np.zeros(n, np.float32)
        else:
            rise = np.maximum(vals - pv, 0.0, dtype=np.float32)
            pv[:] = vals
        r_tot = float(rise.sum())
        r_max = float(rise.max()) if n else 0.0
        # Веса быстрых: рост полосы плюс малая доля высоты. Доля нужна на
        # ровном спектре (удар по басу при не меняющихся остальных полосах):
        # роста там нет нигде, и быстрые искры разлетелись бы по одной точке.
        fw = rise + 0.05 * vals
        f_tot = float(fw.sum())
        fcdf = None
        if f_tot > 1e-6:
            fcdf = np.cumsum(fw, dtype=np.float64)
            fcdf /= fcdf[-1]

        # Громкость самой музыки, а не баса: искры летят по всей площади, пока
        # что-то играет, и замирают в тишине. Бас для этого не годится — он
        # узкий, на музыке без баса фонтан был бы пустым.
        loud = float(np.clip(getattr(self, "_level", 0.0), 0.0, 1.0))
        if peak > 0.02:
            loud = max(loud, min(1.0, peak * 0.7))
        alive = loud > 0.004

        # --- сколько частиц выпускать ---
        # Пока играет музыка, по всей площади идёт ровный негустой фон, а на
        # всплеске поверх него летит пучок. Раньше поток целиком зависел от
        # баса, и на подвижной музыке без низов искры почти не появлялись.
        prev = st["prev"]
        st["prev"] = loud
        # Пучок на долю. Когда темп найден, всплеск бьём по фазе, а не по
        # порогу громкости: удар приходит ровно там, где надо, и один раз,
        # а не два. Порог остаётся запасным вариантом — пока темп не
        # определён (первые секунды трека, тихая музыка, шум).
        beat_ok = self._beat is not None and self._beat.bpm > 0.0
        if beat_ok:
            fired = bool(self._beat_fired)
        else:
            fired = bool(loud > 0.03 and loud > prev * 1.10 + 0.004)
        # сколько энергии в текущем ударе: на доле бьём сильнее
        pulse = float(getattr(self, "_beat_pulse", 0.0)) if beat_ok else 0.0

        # Два потока, и это весь смысл режима. Медленный фон летит ровно и
        # разгону не подвержен: он показывает, что музыка играет. Быстрые
        # частицы вылетают оттуда, где полоса выросла, и обгоняют фон.
        # Раньше разгон бил по всем частицам колонки сразу, включая фоновые,
        # и эффекта обгона не было: поднимался весь столбик целиком.
        base_kick = 0.30 + 0.45 * loud
        # --- сильный удар баса: отдельный пучок из одной точки ---
        #
        # Детектор бита ловит удар по росту энергии НИЖНЕЙ ПОЛОВЫ спектра, а
        # это уже середина: удар по малому барабату проходит там же, где и
        # бочка. Для «выстрела пучком» нужен именно бас, поэтому своя
        # энергия: среднее квадратов по самым низким полосам (шестая часть
        # спектра) и резкий скачок этой энергии.
        #
        # Порог не фиксированный, а от своей же истории: удар считается
        # сильным, если бас подскочил в полтора-два раза выше среднего за
        # последние доли секунды. На тихой дорожке порог опускается сам, на
        # громкой — поднимается, иначе пучок летел бы на каждой ноте.
        bass_punch = 0.0
        bass_x = 0.5
        nbass = 0
        burst_s = float(np.clip(S.get("spark_bass_burst", 0.5), 0.0, 1.0))
        if n >= 2 and burst_s > 0.01:
            lo_n = max(2, n // 6)
            # Энергия баса 20-250 Гц прямо из движка звука — сырая, без
            # автобалансировки спектра. В выровненном спектре удар баса
            # растягивается на 15-20 кадров и неотличим от плавного
            # подъёма: за 25 секунд не ловилось ни одного удара, а порог
            # avg*1.7 был недостижим — базовая энергия гуляла 0.4..0.97, а
            # порог стоял на 1.0..1.5.
            br = float(getattr(self, "_bass_raw", 0.0))
            # БАЗОВАЯ ЛИНИЯ, а не пик. Раньше сравнивали с оболочкой-пиком,
            # и это ломало две вещи: срабатывало слишком часто (41 удар за
            # 20 секунд — пучок бы не бил по бочке, а постоянно плевался),
            # и следующий удар той же силы уже не проходил — пик-то держался.
            # Базовая линия — медленное среднее: между ударами бас держится
            # на своём обычном уровне, и на нём удара быть не может.
            baseline = float(st.get("bass_base", br))
            prev_br = float(st.get("prev_br", br))
            # Порог снизу — относительно уровня музыки, а не абсолютный:
            # энергия баса 20-250 Гц по шкале движка очень мала (десятки
            # тысячных), и абсолютный порог 0.004 отсекал всё напрочь.
            floor = max(1e-7, float(getattr(self, "_level", 0.0)) * 0.0002)
            # Рост — удар это нарастание, а не ровный гул. Без этого на
            # басовой линии с ровным гулом срабатывало бы постоянно.
            rising = br > prev_br * 1.01
            if rising and br > max(baseline * 2.0, floor):
                # Сила удара: во сколько раз бас превысил базовую линию.
                ratio = br / max(baseline, 1e-9)
                bass_punch = float(np.clip((ratio - 2.0) / 3.0, 0.0, 1.0))
                # Не чаще одного раза на 120 мс: бас держится несколько
                # кадров после удара, и без этой паузы один удар давал
                # два-три пучка подряд.
                now = time.monotonic()
                last_p = float(st.get("last_punch", -10.0))
                if now - last_p >= 0.12:
                    st["last_punch"] = now
                    st["punch_hits"] = st.get("punch_hits", 0) + 1
                    # где ударило: полоса с самым громким басом.
                    raw = getattr(self, "_beat_in", None)
                    if raw is None or len(raw) != len(self.lines):
                        raw = np.asarray(self.lines, dtype=np.float32)
                    lo = np.asarray(raw[:lo_n], dtype=np.float32)
                    k_b = int(np.argmax(lo)) if len(lo) else 0
                    # Точка вылета — по полосе, где бас ударил. Бас это самые
                    # низкие полосы, а они в спектре слева, поэтому пучок
                    # всегда слева. Но у самого края экрана половина пучка
                    # уходит за границу, и его не видно: отступаем от края.
                    bass_x = (k_b + 0.5) / float(lo_n)
                    bass_x = float(np.clip(bass_x, 0.035, 0.30))
                    # Частиц должно хватать на видимый пучок даже при
                    # несильном ударе — иначе получится плевок, а не пучок.
                    nbass = int(burst_s * (12.0 + 40.0 * bass_punch))
                    st["punch_req"] = st.get("punch_req", 0) + nbass
            st["bass_base"] = baseline * 0.97 + br * 0.03
            st["prev_br"] = br
            st["be_now"] = br
            st["be_avg_now"] = baseline
            st["be_thr_now"] = baseline * 2.0
            st["be_rise_now"] = br - baseline
            # История настоящего баса: видно, есть ли в нём резкие удары.
            brh = st.get("br_hist")
            if brh is None or len(brh) != 120:
                brh = st["br_hist"] = np.zeros(120, np.float32)
            brh[1:] = brh[:-1]
            brh[0] = br
        if not alive:
            flow = 0.0
            fast = 0.0
            nbass = 0
        else:
            # Фон: пока играет музыка, по всей площади идёт ровный поток.
            # Важная поправка — поток НЕ должен упираться в bass: раньше он
            # целиком зависел от баса, и на музыке без низов искры почти не
            # появлялись. Здесь минимум 0.10 от громкости: тихо, но видно.
            flow = (0.10 + 0.55 * loud) * cap * dens * dt
            # Всплеск: чем больше выросло спектра суммарно, тем гуще пучок.
            # burst_n задаёт густоту вручную, дефолт 0.7 — ~105 частиц в
            # секунду против ~165 фоновых, то есть пучок акцент поверх фона.
            fast = r_tot * burst_n * dens * (1.0 + 0.6 * pulse)
            if fired:
                fast += (6.0 + loud * 55.0) * dens * (1.0 + 0.6 * pulse)
        st["acc"] = min(st["acc"] + flow, cap * 0.35)  # не копим в долг
        nb = int(st["acc"])
        st["acc"] -= nb
        st["facc"] = min(st["facc"] + fast, cap * 0.35)
        nf = int(st["facc"])
        st["facc"] -= nf
        for q in range(nb + nf + nbass):
            is_fast = q >= nb
            is_bass = q >= (nb + nf)
            i = st["head"]
            st["head"] = (st["head"] + 1) % cap
            # Слот занят — пропускаем выпуск, а не перезаписываем.
            #
            # Раньше новый выстрел всегда писал в слот по кругу, даже если
            # частица там ещё жива. Пока мест хватало, это было незаметно, но
            # стоило заполнить буфер — и новая частица без жалости стирала
            # ту, что не догасла. На глаз это ровно «искры исчезают не
            # плавно»: жизнь ни одной частицы не падала ниже 0.15, хвост
            # обрывался на середине. Теперь лишний выстрел просто не
            # вылетает, а старая частица спокойно догасает.
            if st["life"][i] > 0.0:
                if is_bass:
                    # Места нет: буфер забит фоном (у пользователя 910 из 928
                    # занято). Пучок при этом молча пропадал — а это ровно то,
                    # что было: «на басу пучок не выскакивает».
                    # Для пучка место ДЕЛАЕМ: он вытесняет самую старую
                    # частицу. Кольцо буфера идёт от самой старой, так что
                    # вытесняется та, что и так доживает свои доли секунды.
                    st["punch_drop"] = st.get("punch_drop", 0) + 1
                    st["life"][i] = 0.0
                    st["y"][i] = 1e9
                else:
                    continue
            if is_bass:
                st["punch_ok"] = st.get("punch_ok", 0) + 1
            j = np.random.rand()
            # выбираем полосу по весу = высота спектра в ней; быстрые — по
            # весу = насколько полоса выросла, то есть летят из всплеска
            pick = fcdf if is_fast else cdf
            k = int(np.searchsorted(pick, np.random.rand())) if pick is not None else 0
            if k >= n:
                k = n - 1
            amp = float(vals[k]) if n else 0.0
            if is_bass:
                # Пучок на басовом ударе: полоса та, где бас и ударил, а
                # скорость — в разы выше обычной. Вот это и есть «пучком».
                lo_n2 = max(2, n // 6)
                k = min(int(bass_x * lo_n2), lo_n2 - 1)
                k = max(0, k)
                amp = max(amp, float(vals[k]) if n else 0.0)
                drive = base_kick * (1.6 + 2.4 * bass_punch)
            elif is_fast:
                # Сила всплеска этой полосы: 1 там, где скачок сильнее всех.
                # Даже на нулевом разгоне быстрая частица вдвое быстрее фона:
                # обгон виден даже при boost = 0.
                rel = float(np.clip(rise[k] / max(r_max, 1e-4), 0.0, 1.0))
                drive = base_kick * (1.0 + boost * rel)
            else:
                # Фон летит ровно и медленно, без разгона: высота полёта
                # квадратична по скорости, поэтому даже небольшая разница
                # видна глазом. Скорость фона — исходная, ползунок разгона
                # на него не действует.
                drive = base_kick
            if is_bass:
                # Пучок вылетает из одной точки: разброс по горизонтали крошечный,
                # иначе это не пучок, а ещё одна полоса. Точка — та, где бас
                # ударил, поэтому по спектру видно, куда пришёлся удар.
                spread = 0.012 * (1.0 + 0.6 * np.random.rand())
                st["x"][i] = x0 + w * float(np.clip(bass_x + (np.random.rand() - 0.5) * spread,
                                                   0.0, 1.0))
            else:
                st["x"][i] = x0 + w * ((k + np.random.rand()) / max(1, n))
            st["y"][i] = y_base - 1.0
            # Стартовая скорость подъёма. Раньше было (260 + loud*900), и при
            # дефолтах фон поднимался на 40 px — то есть на 1080p это была
            # узкая полоска у самого низа, а не фонтан. Плотность, размер и
            # высоту в такой полоске менять бесполезно: она и так сплошная.
            # Теперь фон поднимается на ~15% высоты экрана, и картинка имеет
            # вертикальную структуру, в которой ползунки видно.
            #
            # Множитель scale — размер экрана. Гравитация задана в пикселях
            # в секунду, поэтому без него фон на 720p занимал треть экрана, а
            # на 4K был узкой полоской: одно и то же настройка значила
            # разное на разных мониторах.
            scale = max(0.35, H / 720.0)
            vy = (-(400.0 + loud * 1350.0) * hs * (0.55 + 0.75 * j)
                  * drive * spd * scale)
            if is_bass:
                # Пучок летит заметно быстрее фона: ради этого всё и затевалось,
                # «выстрел из сопла», а не ещё одна струйка. Чем сильнее удар,
                # тем быстрее — сила удара идёт прямо в скорость.
                vy *= (1.0 + 1.8 * bass_punch)
            if is_fast or is_bass:
                # Потолок высоты всплеска. Вершина баллистического полёта —
                # vy²/2g, поэтому ограничить высоту можно ограничением
                # скорости: vy_max = sqrt(2·g·(остаток)). Из полного потолка
                # вычитаем доезд после вершины по экспоненте (~vy·tau·0.28),
                # иначе реальная вершина вылезала за ползунок на треть:
                # ставил 0.10, а частица поднималась на 119 px вместо 72.
                # tau считаем от исходной скорости — она заведомо больше
                # срезанной, так что потолок выходит с запасом.
                tau0 = 0.42 - 0.30 * float(np.clip(vy * vy / (2.0 * 980.0 * spd)
                                                  / max(1.0, H), 0.0, 1.0))
                room = max(1.0, burst_h * H - abs(vy) * max(tau0, 0.05) * 0.28)
                vy_max = (2.0 * 980.0 * spd * room) ** 0.5
                if vy < -vy_max:
                    vy = -vy_max
            st["vy"][i] = vy
            # Срок жизни: подъём до вершины ПЛЮС хвост, в который частица
            # гаснет. Раньше life = полёт × 1.08, то есть на растворение
            # оставалось 8% полёта — у быстрой искры это 0.1 с, и она
            # пропадала с вершины рывком. Теперь хвост отдельный и зависит
            # от высоты: чем выше долетела, тем дольше висит.
            #
            # ВРЕМЯ ПОДЪЁМА, а не «вверх и обратно». Здесь была ошибка:
            # стояло 2*vy0/(g*spd) — это время полного хода вверх и обратно,
            # то есть из старой физики, где частица после вершины ПАДАЛА
            # вниз. Падать она перестала: после вершины скорость гасится
            # экспонентой и частица висит. А множитель «жизнь × 1.6» на
            # вершине убрали — он как раз и маскировал зависание. Вместе
            # получилось, что 57% жизни частица проводит на вершине.
            # Теперь подъём честный, а зависание вынесено в отдельную ручку
            # spark_hover: 0 — исчезает у вершины, 1 — как было.
            vy0 = abs(float(st["vy"][i]))
            t_flight = vy0 / (980.0 * spd)
            # Высота вершины в пикселях: v²/2g. Нормируем на высоту экрана, иначе
            # «высокая» означала бы разную вещь на 720p и 4K. Уже срезанной
            # по потолку скорости, поэтому дальше она не растёт.
            h_norm = float(np.clip(vy0 * vy0 / (2.0 * 980.0 * spd)
                                   / max(1.0, H), 0.0, 1.0))
            st["tau"][i] = 0.42 - 0.30 * h_norm      # чем выше, тем тормознее
            hover = float(np.clip(S.get("spark_hover", 1.0), 0.0, 2.0))
            st["full"][i] = max(t_flight * (1.0 + hover) + 0.12 + 0.45 * h_norm,
                                (0.7 + 0.9 * float(np.random.rand())) / max(0.40, spd * drive))
            st["life"][i] = st["full"][i]
            st["bin"][i] = k
            # размер запоминаем сразу: в полёте частица должна оставаться той,
            # какой вылетела, а не мерить спектр заново каждый кадр
            st["amp"][i] = amp

        # --- баллистика: только вверх, с замедлением ---
        g = 980.0
        st["vy"] = st["vy"] + g * spd * dt   # vy отрицательная = вверх
        st["y"] = st["y"] + st["vy"] * dt
        st["life"] = st["life"] - dt

        # Дошла до вершины: vy дошла до нуля. Раньше частица в этот момент
        # удалялась мгновенно, life обнулялся — и в кадре, где фонтан ещё
        # стоял ярким, он разом исчезал целиком. Видно было как обрыв:
        # «наверху висят точки, и разом их нет».
        #
        # Теперь после вершины скорость гасится ПО ФОРМУЛЕ, а не множителем
        # на кадр: vy *= exp(-dt/tau). Разница существенная — множитель 0.82
        # на кадр тормозил тем сильнее, чем выше fps: на 60 кадрах частица
        # висела на вершине вдвое дольше, чем на 120, и картинка зависела от
        # настроек. Экспонента по dt от fps не зависит.
        #
        # tau задаётся при вылете по высоте вершины: чем выше долетела
        # искра, тем медленнее она оседает, и тем дольше висит.
        top = st["vy"] >= 0.0
        if top.any():
            tau = np.maximum(st["tau"], 0.05)
            st["vy"][top] = np.minimum(0.0, st["vy"][top] * np.exp(-dt / tau[top]))
        # Жизнь тает сама, ровно, без множителя 1.6: раньше он ускорял
        # угасание, и в последние 8% жизни частица гасла вчетверо быстрее,
        # чем поднималась, — отсюда и ощущение обрыва.

        dead = st["life"] <= 0.0
        # Улетела за верхний край окна — тоже смерть. Частица там не рисуется
        # (draw_sparks отбрасывает всё выше y = -8), но слот в буфере держит
        # до конца жизни. На большом разгоне таких было 77 из 304, четверть
        # бюджета, занятая невидимым. Теперь слот освобождается сразу.
        dead |= st["y"] < -8.0
        if dead.any():
            st["life"][dead] = 0.0
            st["y"][dead] = 1e9
        # Счётчики пучка и заполненность буфера — наружу, чтобы «пучок не
        # появляется» было видно по числам, а не по догадкам.
        for k in ("punch_hits", "punch_req", "punch_ok", "punch_drop",
                  "be_now", "be_avg_now", "be_thr_now", "be_rise_now"):
            self._stat[k] = st.get(k, 0)
        self._stat["br_hist"] = [round(float(v) * 1e6, 2)
                                 for v in st.get("br_hist", np.zeros(0))[:50]]
        self._stat["spark_alive"] = int((st["life"][:cap] > 0.0).sum())
        self._stat["spark_cap"] = cap

    def draw_sparks(self, p, W, H, n, alpha):
        """Искры: пучком вылетают из одной точки на базовой линии, летят
        вверх и гаснут.

        Отличие от «частиц» принципиальное: там точки-пружины прыгают по
        базовой линии и не исчезают. Здесь толчок даёт всплеск баса, дальше
        частица летит сама, замедляясь, и умирает — то есть бит виден как
        выстрел из одной точки, а не как столбик спектра.

        Здесь только отрисовка: состояние частиц двигает step_sparks() из
        tick(), поэтому картинка одинакова при любом порядке перерисовок."""
        S = self._cfg()
        x0, y_base, w = self.zone(W, H)
        st = getattr(self, "_spark", None)
        if st is None or not len(st["x"]):
            return
        cap = st["cap"]

        # --- рисуем живые, у краёв экрана и по ту сторону базы убираем ---
        # Свечение — несколько проходов от широкого слабого ореола к самой
        # искре, аналог CSS-овского drop-shadow. В браузере это одна строка
        # filter, в Qt приходится рисовать втрое: широкая обводка с малой
        # альфой, средняя, и только потом сама точка.
        #
        # Проходов три на кадр, а не один, поэтому при нулевом свечении
        # возвращаемся к одиночной отрисовке — иначе платим втрое за то,
        # что выключено.
        glow = float(np.clip(S.get("spark_glow", 0.5), 0.0, 3.0))
        # Где по высоте полёта горит ореол. Полёт идёт от базовой линии
        # (0.0) до вершины (1.0), поэтому окно задаётся двумя ползунками:
        # начало и конец. Края окна мягкие, иначе был бы видимый стык на
        # частицах. По умолчанию горит внизу и гаснет к вершине; если
        # поднять начало к 0.6, снизу искры чистые, а загораются у верха.
        g_a = float(np.clip(S.get("spark_glow_start", 0.0), 0.0, 1.0))
        g_b = float(np.clip(S.get("spark_glow_end", 0.55), 0.0, 1.0))
        if g_b < g_a:
            g_a, g_b = g_b, g_a
        # ширина мягкого края окна — чтобы переход не был ступенькой
        edge = max(0.08, 0.5 * (g_b - g_a)) if g_b > g_a else 0.5 * (1.0 - g_a)
        passes = ((2.9, 0.10), (1.8, 0.22), (1.0, 1.0))
        # --- расчёты целиком на numpy, до цикла ---
        # Раньше всё считалось по одной частице прямо в цикле. Там стояло
        # три np.clip на скаляр, и один из них — на height_scale, который в
        # кадре не меняется вообще. np.clip на скаляре стоит 2.5 мкс чистого
        # диспетчера, таких вызовов выходило 2100 на кадр — 5 мс, десятая
        # часть бюджета. Считаем массивами один раз.
        # Высота полёта (height_scale) в размере частицы больше не участвует:
        # размер задаётся отдельной ручкой spark_size. Промежуточная
        # переменная hs осталась бы мёртвой, а pyflakes бы ругался.
        # Размер частицы — отдельная ручка от высоты полёта.
        sz = float(np.clip(S.get("spark_size", 1.0), 0.2, 3.0))
        life = st["life"][:cap]
        alive = np.flatnonzero(life > 0.0)
        if alive.size == 0:
            return
        y_all = st["y"][:cap]
        alive = alive[y_all[alive] >= -8.0]
        if alive.size == 0:
            return
        f = life[alive] / np.maximum(0.01, st["full"][:cap][alive])
        v = f * f                       # гаснут плавно, а не по ступеням
        # Размер задаёт полоса, из которой частица вылетела, — чем выше
        # там музыка, тем крупнее искра. Плюс угасание: к вершине точка
        # мельчает до нуля.
        #
        # Раньше было (0.45 + 0.55*v): на последнем кадре жизни точка ещё
        # 45% своего размера и просто исчезала — на глаз это и есть «искры
        # не плавно исчезают». Теперь множитель идёт в ноль вместе с
        # прозрачностью, и точка физически тает, а не выключается.
        amp = np.clip(st["amp"][:cap][alive], 0.0, 1.4) * 0.8
        rad = (1.1 + 4.6 * amp * sz) * (0.12 + 0.88 * v)
        keep = rad >= 0.7
        alive, v, amp, rad = alive[keep], v[keep], amp[keep], rad[keep]
        if alive.size == 0:
            return
        # цвет по полосе, яркость — по силе полосы и по доле жизни
        lvl = np.clip(0.3 + 0.7 * (amp * 0.6 + v * 0.4), 0.0, 1.0)
        a0 = alpha * (0.35 + 0.65 * v)
        # сколько ореола по этой высоте: 0 — частица чистая, 1 — полный.
        # Трапеция из двух ограниченных пил: их произведение даёт ровно
        # то же окно с мягкими краями, что и ветки if/elif, но без ветвления
        # на каждой частице.
        if glow > 0.02:
            prog = 1.0 - np.sqrt(v)   # 0 у базовой линии, 1 на вершине
            e = max(edge, 1e-6)
            gw = np.clip((prog - (g_a - e)) / e, 0.0, 1.0) * \
                np.clip(((g_b + e) - prog) / e, 0.0, 1.0)
        else:
            gw = np.zeros_like(v)
        xs = st["x"][:cap][alive]
        ys = st["y"][:cap][alive]
        bins = st["bin"][:cap][alive]
        # Широкие ореолы — без сглаживания. Они огромные и мягкие, ступеньки
        # на них не видны, а сглаживание на 1536 окружностях стоит треть
        # времени кадра. Сама искра рисуется с ним.
        had_aa = p.testRenderHint(QPainter.Antialiasing)
        brush = QBrush(Qt.SolidPattern)
        p.setPen(Qt.NoPen)
        p.setRenderHint(QPainter.Antialiasing, False)
        for i in range(alive.size):
            gw_i = float(gw[i])
            if gw_i <= 0.02:
                continue        # ореола нет — рисуем только точку, одним проходом
            rad_i = float(rad[i])
            x = float(xs[i])
            y = float(ys[i])
            c = self._bar_color(int(bins[i]), float(lvl[i]), 255)
            base_a = float(a0[i])
            for k, wa in passes[:-1]:
                r = rad_i * (1.0 + (k - 1.0) * glow * gw_i)
                c.setAlpha(int(base_a * wa * gw_i))
                brush.setColor(c)
                p.setBrush(brush)
                p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
        p.setRenderHint(QPainter.Antialiasing, had_aa)
        for i in range(alive.size):
            gw_i = float(gw[i])
            # Здесь обязателен обычный float, а не numpy.float32: ниже эти
            # числа идут прямо в QRectF и QColor. PySide6 6.x падает на
            # numpy-скалярах с 0xc0000005 (нарушение доступа) — процесс
            # умирал целиком, без следа в логе. faulthandler показал стек
            # ровно сюда.
            rad_i = float(rad[i])
            if rad_i < 0.7:
                continue
            c = self._bar_color(int(bins[i]), float(lvl[i]), 255)
            # Ядро гасится окном так же, как ореол: в исходном варианте оно
            # тоже шло с множителем gw, и менять это незаметно не выйдет —
            # все настройки подобраны под прежнюю картинку.
            if gw_i <= 0.02:
                c.setAlpha(int(float(a0[i])))
            else:
                c.setAlpha(int(float(a0[i]) * gw_i))
            brush.setColor(c)
            p.setBrush(brush)
            x = float(xs[i])
            y = float(ys[i])
            p.drawEllipse(QRectF(x - rad_i, y - rad_i, rad_i * 2, rad_i * 2))
        pen = QPen(QColor(255, 255, 255, 45))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawLine(int(x0), int(y_base), int(x0 + w), int(y_base))
