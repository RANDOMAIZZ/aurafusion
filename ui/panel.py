"""Красивая панель AuraFusion: сайдбар + карточки + живое превью."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QProgressBar, QSlider,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .theme import QSS


def _slider_row(parent, title, lo, hi, val, fmt, on_change, is_int=False, div=100):
    wrap = QVBoxLayout()
    top = QHBoxLayout()
    lab = QLabel(title)
    lab.setStyleSheet("font-size:12px;")
    vlab = QLabel()
    vlab.setObjectName("Value")
    top.addWidget(lab)
    top.addStretch(1)
    top.addWidget(vlab)
    wrap.addLayout(top)
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    def _fmt(v):
        return str(int(v)) if is_int else (fmt % (v / div))
    vlab.setText(_fmt(val))
    def _changed(v):
        vlab.setText(_fmt(v))
        on_change(v / div if not is_int else int(v))
    s.valueChanged.connect(_changed)
    wrap.addWidget(s)
    parent.addLayout(wrap)
    hint = QLabel("")
    hint.setObjectName("Hint")
    return s


class Panel(QWidget):
    def __init__(self, cfg_getter, cfg_setter, audio, overlay, sc, state, hooks):
        super().__init__()
        self._get = cfg_getter
        self._set = cfg_setter  # fn(key, value, save=True)
        self.audio = audio
        self.overlay = overlay
        self.sc = sc
        self.state = state
        self.hooks = hooks  # dict: start_rgb, stop_rgb, test_color, refresh_devices, refresh_audio
        self.setWindowTitle("AuraFusion — музыка + экран + ПК")
        self.setObjectName("Root")
        self.resize(880, 620)
        self.setStyleSheet(QSS)
        self._build()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh_live)
        self._tick.start(200)
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(lambda: self.hooks.get("save", lambda: None)())
        self._save_timer.start(5000)

    # ---------------- каркас ----------------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # сайдбар
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(190)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 14, 12, 14)
        logo = QLabel("◉ AuraFusion")
        logo.setStyleSheet("font-size:16px;font-weight:800;color:#00E5FF;")
        sv.addWidget(logo)
        sub = QLabel("музыка • экран • ПК")
        sub.setObjectName("Hint")
        sv.addWidget(sub)
        sv.addSpacing(10)
        self.nav_btns = {}
        groups = [
            ("✨ Визуал и экран", [("viz", "✨  Визуал"), ("amb", "🖥  Экран → ПК")]),
            ("💡 ПК и система", [("rgb", "💡  Подсветка ПК"), ("snd", "🔊  Звук"), ("sys", "⚙  Система")]),
        ]
        for gtitle, items in groups:
            head = QPushButton("▾ " + gtitle)
            head.setObjectName("NavGroup")
            head.setFlat(True)
            sv.addWidget(head)
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(2)
            sv.addWidget(box)
            for key, title in items:
                b = QPushButton(title)
                b.setObjectName("NavBtn")
                b.setCheckable(True)
                b.clicked.connect(lambda _, k=key: self.goto(k))
                bv.addWidget(b)
                self.nav_btns[key] = b
            head.clicked.connect(lambda _, w=box, h=head, t=gtitle: self._toggle_group(w, h, t))
        sv.addStretch(1)
        self.side_status = QLabel("старт...")
        self.side_status.setObjectName("Hint")
        self.side_status.setWordWrap(True)
        sv.addWidget(self.side_status)
        root.addWidget(side)
        # правая часть
        right = QWidget()
        right.setObjectName("Root")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 14, 16, 14)
        rv.addWidget(self._header())
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        self.pages = {}
        self.pages["viz"] = self._page_viz()
        self.pages["rgb"] = self._page_rgb()
        self.pages["amb"] = self._page_amb()
        self.pages["snd"] = self._page_snd()
        self.pages["sys"] = self._page_sys()
        for k in ("viz", "rgb", "amb", "snd", "sys"):
            self.stack.addWidget(self.pages[k])
        root.addWidget(right, 1)
        self.goto("viz")

    def _toggle_group(self, box, head, title):
        vis = not box.isVisible()
        box.setVisible(vis)
        head.setText(("▾ " if vis else "▸ ") + title)

    def showEvent(self, e):
        super().showEvent(e)
        try:
            self.hooks.get("geom_sync", lambda: None)()
        except Exception:
            pass

    def hideEvent(self, e):
        super().hideEvent(e)
        try:
            self.hooks.get("geom_sync", lambda: None)()
        except Exception:
            pass

    def goto(self, key):
        order = ["viz", "rgb", "amb", "snd", "sys"]
        self.stack.setCurrentWidget(self.pages[key])
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
            b.setProperty("active", "true" if k == key else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        # списки мониторов всегда свежие при заходе на вкладку
        try:
            if key == "amb":
                self.refresh_colormon()
            elif key == "sys":
                self.refresh_overlaymon()
        except Exception:
            pass

    def _header(self):
        card = QWidget()
        card.setObjectName("Card")
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 12, 14, 12)
        # превью экрана
        self.dot_screen = QLabel()
        self.dot_screen.setObjectName("PreviewDot")
        self.dot_screen.setFixedSize(44, 44)
        h.addWidget(self.dot_screen)
        v1 = QVBoxLayout()
        v1.addWidget(QLabel("Экран →"))
        self.lbl_screen_hex = QLabel("#000000")
        self.lbl_screen_hex.setObjectName("Value")
        v1.addWidget(self.lbl_screen_hex)
        h.addLayout(v1)
        # превью ПК
        self.dot_rgb = QLabel()
        self.dot_rgb.setObjectName("PreviewDot")
        self.dot_rgb.setFixedSize(44, 44)
        h.addWidget(self.dot_rgb)
        v2 = QVBoxLayout()
        v2.addWidget(QLabel("ПК 💡"))
        self.lbl_rgb_hex = QLabel("#000000")
        self.lbl_rgb_hex.setObjectName("Value")
        v2.addWidget(self.lbl_rgb_hex)
        h.addLayout(v2)
        # уровень
        vm = QVBoxLayout()
        self.level_bar = QProgressBar()
        self.level_bar.setObjectName("Level")
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(12)
        vm.addWidget(QLabel("Уровень звука"))
        vm.addWidget(self.level_bar)
        self.lbl_status = QLabel("...")
        self.lbl_status.setObjectName("Hint")
        vm.addWidget(self.lbl_status)
        h.addLayout(vm, 1)
        # кнопки
        vb = QVBoxLayout()
        self.btn_rgb = QPushButton("▶ RGB СТАРТ")
        self.btn_rgb.setObjectName("Primary")
        self.btn_rgb.clicked.connect(self._toggle_rgb)
        vb.addWidget(self.btn_rgb)
        self.btn_hide = QPushButton("Скрыть оверлей")
        self.btn_hide.setObjectName("Ghost")
        self.btn_hide.clicked.connect(self._toggle_overlay)
        vb.addWidget(self.btn_hide)
        h.addLayout(vb)
        return card

    # ---------------- страницы ----------------
    def _page_viz(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        g = QGroupBox("Стиль волны")
        gv = QVBoxLayout()
        r = QHBoxLayout()
        r.addWidget(QLabel("Стиль:"))
        self.cmb_style = QComboBox()
        self.cmb_style.addItems(["Плавная волна", "Бары", "Круг", "Бары+волна"])
        keys = ["smooth", "bars", "circle", "combo"]
        cur = self._get().get("style", "smooth")
        self.cmb_style.setCurrentIndex(keys.index(cur) if cur in keys else 0)
        self.cmb_style.currentIndexChanged.connect(lambda i: self._set("style", keys[i]))
        r.addWidget(self.cmb_style, 1)
        gv.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Линия:"))
        self.cmb_line = QComboBox()
        self.cmb_line.addItems(["Неон", "Тонкая", "Жирная", "Двойная", "Пунктир",
                               "Градиент", "Точки", "Контур", "Ступени", "Яркое ядро"])
        lk = ["neon", "thin", "fat", "double", "dash", "gradient",
              "dotted", "outline", "steps", "megaglow"]
        cur2 = self._get().get("line_style", "thin")
        self.cmb_line.setCurrentIndex(lk.index(cur2) if cur2 in lk else 1)
        self.cmb_line.currentIndexChanged.connect(lambda i: self._set("line_style", lk[i]))
        r2.addWidget(self.cmb_line, 1)
        gv.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Цвет баров:"))
        self.cmb_cmode = QComboBox()
        self.cmb_cmode.addItems(["Неон", "Зелёный", "Огонь", "Белый", "Свой..."])
        mk = ["neon", "green", "fire", "white", "custom"]
        cur3 = self._get().get("color_mode", "neon")
        self.cmb_cmode.setCurrentIndex(mk.index(cur3) if cur3 in mk else 0)
        self.cmb_cmode.currentIndexChanged.connect(self._on_cmode)
        r3.addWidget(self.cmb_cmode, 1)
        gv.addLayout(r3)
        hcol = QHBoxLayout()
        hcol.addWidget(QLabel("Цвет волны (если экран выкл):"))
        self.btn_fill = QPushButton("Выбрать")
        self.btn_fill.setObjectName("Ghost")
        self.btn_fill.clicked.connect(self._pick_fill)
        hcol.addWidget(self.btn_fill)
        self.fill_prev = QLabel("   ")
        hcol.addWidget(self.fill_prev)
        gv.addLayout(hcol)
        g.setLayout(gv)
        v.addWidget(g)

        g2 = QGroupBox("Движение и вид")
        g2v = QVBoxLayout()
        C = self._get()
        _slider_row(g2v, "Размер / высота", 5, 200, int(C.get("height_scale", 0.55) * 100),
                    "%.2f", lambda v: self._set("height_scale", v))
        _slider_row(g2v, "Гладкость волны", 0, 100, int(C.get("wave_smooth", 0.65) * 100),
                    "%.2f", lambda v: self._set("wave_smooth", v))
        _slider_row(g2v, "Яркость линии", 40, 255, int(C.get("alpha", 230)), "%d",
                    lambda v: self._set("alpha", v), is_int=True)
        _slider_row(g2v, "Заливка (прозрачность)", 0, 180, int(C.get("fill_alpha", 90)), "%d",
                    lambda v: self._set("fill_alpha", v), is_int=True)
        _slider_row(g2v, "Тряска в такт", 0, 100, int(C.get("shake", 0.0) * 100),
                    "%.2f", lambda v: self._set("shake", v))
        _slider_row(g2v, "Зум в такт", 0, 100, int(C.get("zoom", 0.0) * 100),
                    "%.2f", lambda v: self._set("zoom", v))
        _slider_row(g2v, "Вспышка в такт", 0, 100, int(C.get("flash", 0.0) * 100),
                    "%.2f", lambda v: self._set("flash", v))
        _slider_row(g2v, "Виньетка (0 = выкл)", 0, 100, int(C.get("vignette", 0.0) * 100),
                    "%.2f", lambda v: self._set("vignette", v))
        _slider_row(g2v, "Затемнение экрана", 0, 60, int(C.get("bg_dim", 0.0) * 100),
                    "%.2f", lambda v: self._set("bg_dim", v))
        _slider_row(g2v, "Затемнение в такт", 0, 60, int(C.get("dim_pulse", 0.0) * 100),
                    "%.2f", lambda v: self._set("dim_pulse", v))
        hb = QHBoxLayout()
        self.chk_beat = QCheckBox("Пульс от баса")
        self.chk_beat.setChecked(bool(C.get("beat_pulse", True)))
        self.chk_beat.toggled.connect(lambda v: self._set("beat_pulse", bool(v)))
        hb.addWidget(self.chk_beat)
        self.chk_mirror = QCheckBox("Зеркало вверх+вниз")
        self.chk_mirror.setChecked(bool(C.get("mirror", False)))
        self.chk_mirror.toggled.connect(lambda v: self._set("mirror", bool(v)))
        hb.addWidget(self.chk_mirror)
        g2v.addLayout(hb)
        _slider_row(g2v, "Сила пульса", 0, 100, int(C.get("beat_strength", 0.35) * 100),
                    "%.2f", lambda v: self._set("beat_strength", v))
        self.chk_sysfx = QCheckBox("SYS: приближение/тряска ВСЕГО экрана (лупа Windows)")
        self.chk_sysfx.setChecked(bool(C.get("sys_fx", False)))
        self.chk_sysfx.toggled.connect(lambda v: self._set("sys_fx", bool(v)))
        g2v.addWidget(self.chk_sysfx)
        rsm = QHBoxLayout()
        rsm.addWidget(QLabel("SYS-режим:"))
        self.cmb_sysmode = QComboBox()
        self.cmb_sysmode.addItems(["К центру", "По углам"])
        smk = ["center", "corners"]
        cursm = C.get("sys_mode", "center")
        if cursm not in smk:
            cursm = "center"
            self._set("sys_mode", "center")
        self.cmb_sysmode.setCurrentIndex(smk.index(cursm) if cursm in smk else 0)
        self.cmb_sysmode.currentIndexChanged.connect(lambda i: self._set("sys_mode", smk[i]))
        rsm.addWidget(self.cmb_sysmode, 1)
        g2v.addLayout(rsm)
        _slider_row(g2v, "SYS-приближение", 0, 100, int(C.get("sys_zoom", 0.5) * 100),
                    "%.2f", lambda v: self._set("sys_zoom", v))
        _slider_row(g2v, "SYS-тряска", 0, 100, int(C.get("sys_shake", 0.5) * 100),
                    "%.2f", lambda v: self._set("sys_shake", v))
        syshint = QLabel("Через Magnification API. Не работает в Exclusive Fullscreen. При выкл — экран возвращается.")
        syshint.setObjectName("Hint")
        syshint.setWordWrap(True)
        g2v.addWidget(syshint)
        g2.setLayout(g2v)
        v.addWidget(g2)
        v.addStretch(1)
        return w

    def _page_rgb(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        g = QGroupBox("Устройства OpenRGB")
        gv = QVBoxLayout()
        self.dev_list = QListWidget()
        self.dev_list.setMaximumHeight(110)
        gv.addWidget(self.dev_list)
        hr = QHBoxLayout()
        self.btn_dev_refresh = QPushButton("↻ Найти устройства")
        self.btn_dev_refresh.setObjectName("Ghost")
        self.btn_dev_refresh.clicked.connect(lambda: self.hooks.get("refresh_devices", lambda: None)())
        hr.addWidget(self.btn_dev_refresh)
        self.btn_test = QPushButton("Тест цвета 4с")
        self.btn_test.setObjectName("Ghost")
        self.btn_test.clicked.connect(lambda: self.hooks.get("test_color", lambda: None)())
        hr.addWidget(self.btn_test)
        self.btn_kick = QPushButton("⟳ Дёрнуть режим")
        self.btn_kick.setObjectName("Ghost")
        self.btn_kick.clicked.connect(lambda: self.hooks.get("kick_mode", lambda: None)())
        hr.addWidget(self.btn_kick)
        gv.addLayout(hr)
        r = QHBoxLayout()
        r.addWidget(QLabel("Режим ПК:"))
        self.cmb_rgbmode = QComboBox()
        self.cmb_rgbmode.addItems(["Микс: цвет экран + пульс музыка", "Только экран", "Только музыка"])
        rk = ["mix", "screen", "music"]
        cur = self._get().get("rgb_mode", "mix")
        self.cmb_rgbmode.setCurrentIndex(rk.index(cur) if cur in rk else 0)
        self.cmb_rgbmode.currentIndexChanged.connect(lambda i: self._set("rgb_mode", rk[i]))
        r.addWidget(self.cmb_rgbmode, 1)
        gv.addLayout(r)
        self.chk_follow_rgb = QCheckBox("Авто-цвет под экран (как в визуализаторе)")
        self.chk_follow_rgb.setChecked(bool(self._get().get("follow_screen", True)))
        self.chk_follow_rgb.toggled.connect(self._on_follow)
        gv.addWidget(self.chk_follow_rgb)
        hint_follow = QLabel("Скорость подтяжки — на вкладке «Экран → ПК».")
        hint_follow.setObjectName("Hint")
        gv.addWidget(hint_follow)
        self.sl_sat_rgb = _slider_row(gv, "Насыщенность авто-цвета", 0, 150,
                    int(self._get().get("rgb_saturation", 0.6) * 100), "%.2f",
                    lambda v: self._on_sat(v, getattr(self, "sl_sat_rgb", None)))
        _slider_row(gv, "Баланс R", 30, 150, int(self._get().get("rgb_gain_r", 1.0) * 100),
                    "%.2f", lambda v: self._set("rgb_gain_r", v))
        _slider_row(gv, "Баланс G", 30, 150, int(self._get().get("rgb_gain_g", 1.0) * 100),
                    "%.2f", lambda v: self._set("rgb_gain_g", v))
        _slider_row(gv, "Баланс B", 30, 150, int(self._get().get("rgb_gain_b", 1.0) * 100),
                    "%.2f", lambda v: self._set("rgb_gain_b", v))
        _slider_row(gv, "Порог темноты (0 = выкл)", 0, 255, int(self._get().get("dark_threshold", 0)),
                    "%d", lambda v: self._set("dark_threshold", v), is_int=True)
        hcol = QHBoxLayout()
        hcol.addWidget(QLabel("Статичный цвет (режим Музыка):"))
        self.btn_static = QPushButton("Выбрать")
        self.btn_static.setObjectName("Ghost")
        self.btn_static.clicked.connect(self._pick_static)
        hcol.addWidget(self.btn_static)
        self.static_prev = QLabel("   ")
        hcol.addWidget(self.static_prev)
        gv.addLayout(hcol)
        self.chk_color_all = QCheckBox("Один цвет на все устройства")
        self.chk_color_all.setChecked(bool(self._get().get("color_all", True)))
        self.chk_color_all.toggled.connect(lambda v: self._set("color_all", bool(v)))
        gv.addWidget(self.chk_color_all)
        self.chk_rgb_en = QCheckBox("Подсветка ПК включена")
        self.chk_rgb_en.setChecked(bool(self._get().get("rgb_enabled", True)))
        self.chk_rgb_en.toggled.connect(lambda v: self._set("rgb_enabled", bool(v)))
        gv.addWidget(self.chk_rgb_en)
        g.setLayout(gv)
        v.addWidget(g)
        g2 = QGroupBox("Пульс (яркость от баса)")
        g2v = QVBoxLayout()
        C = self._get()
        _slider_row(g2v, "Порог импульса", 0, 50, int(C.get("pulse_threshold", 0.10) * 100),
                    "%.2f", lambda v: self._set("pulse_threshold", v))
        _slider_row(g2v, "Усиление импульса", 20, 300, int(C.get("pulse_gain", 1.2) * 100),
                    "%.2f", lambda v: self._set("pulse_gain", v))
        _slider_row(g2v, "Атака (с)", 1, 30, int(C.get("attack_s", 0.04) * 100),
                    "%.2f", lambda v: self._set("attack_s", v))
        _slider_row(g2v, "Затухание (с)", 5, 150, int(C.get("release_s", 0.35) * 100),
                    "%.2f", lambda v: self._set("release_s", v))
        _slider_row(g2v, "Мин. яркость", 0, 50, int(C.get("min_brightness", 0.02) * 100),
                    "%.2f", lambda v: self._set("min_brightness", v))
        _slider_row(g2v, "Макс. яркость", 20, 100, int(C.get("max_brightness", 1.0) * 100),
                    "%.2f", lambda v: self._set("max_brightness", v))
        _slider_row(g2v, "Усиление баса", 30, 300, int(C.get("bass_boost", 1.6) * 100),
                    "%.2f", lambda v: self._set("bass_boost", v))
        g2.setLayout(g2v)
        v.addWidget(g2)
        v.addStretch(1)
        return w

    def _page_amb(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        info = QLabel("ПК берёт ТОТ ЖЕ сочный цвет, что и волна оверлея.\nСкрин 96×54 каждые 0.5с → самый насыщенный цвет → плавно на железо.")
        info.setObjectName("Hint")
        info.setWordWrap(True)
        v.addWidget(info)
        rmon = QHBoxLayout()
        rmon.addWidget(QLabel("Монитор замера:"))
        self.cmb_colormon = QComboBox()
        self.cmb_colormon.currentIndexChanged.connect(self._on_colormon)
        rmon.addWidget(self.cmb_colormon, 1)
        self.btn_monrefresh = QPushButton("↻")
        self.btn_monrefresh.setObjectName("Ghost")
        self.btn_monrefresh.setFixedWidth(40)
        self.btn_monrefresh.clicked.connect(self.refresh_colormon)
        rmon.addWidget(self.btn_monrefresh)
        v.addLayout(rmon)
        rreg = QHBoxLayout()
        rreg.addWidget(QLabel("Область замера:"))
        self.cmb_region = QComboBox()
        self.cmb_region.addItems(["Весь экран", "Центр", "Верх", "Низ", "Своя..."])
        self._reg_keys = ["full", "center", "top", "bottom", "custom"]
        curreg = self._get().get("color_region", "full")
        self.cmb_region.setCurrentIndex(self._reg_keys.index(curreg) if curreg in self._reg_keys else 0)
        self.cmb_region.currentIndexChanged.connect(self._on_region)
        rreg.addWidget(self.cmb_region, 1)
        v.addLayout(rreg)
        self.sl_reg = {}
        reg_def = {"color_rx": 0.0, "color_ry": 0.0, "color_rw": 1.0, "color_rh": 1.0}
        for rkey, rtitle in [("color_rx", "X"), ("color_ry", "Y"),
                             ("color_rw", "Ширина"), ("color_rh", "Высота")]:
            s = _slider_row(v, "Область " + rtitle + ", %", 0, 100,
                            int(round(self._get().get(rkey, reg_def[rkey]) * 100)),
                            "%d", lambda vv, k=rkey: self._on_region_custom(k, vv), is_int=True)
            self.sl_reg[rkey] = s
        g = QGroupBox("Ambilight: экран → ПК")
        gv = QVBoxLayout()
        C = self._get()
        self.chk_follow = QCheckBox("ПК следует за экраном (как визуализатор)")
        self.chk_follow.setChecked(bool(C.get("follow_screen", True)))
        self.chk_follow.toggled.connect(self._on_follow)
        gv.addWidget(self.chk_follow)
        self.chk_excl = QCheckBox("Не учитывать волну и панель при замере")
        self.chk_excl.setChecked(bool(C.get("exclude_viz", True)))
        self.chk_excl.toggled.connect(lambda v: self._set("exclude_viz", bool(v)))
        gv.addWidget(self.chk_excl)
        _slider_row(gv, "Скорость подтяжки к экрану", 5, 100, int(C.get("auto_speed", 0.35) * 100),
                    "%.2f", lambda v: self._set("auto_speed", v))
        self.sl_sat_amb =         _slider_row(gv, "Насыщенность ПК (красный, а не розовый)", 0, 150,
                    int(C.get("rgb_saturation", 0.6) * 100), "%.2f",
                    lambda v: self._on_sat(v, getattr(self, "sl_sat_amb", None)))
        _slider_row(gv, "Держать цвет, замеров (анти-мигание)", 1, 6, int(C.get("color_hold", 2)), "%d",
                    lambda v: self._set("color_hold", v), is_int=True)
        _slider_row(gv, "Скорость перехода цвета", 2, 50, int(C.get("color_speed", 0.12) * 100),
                    "%.2f", lambda v: self._set("color_speed", v))
        r = QHBoxLayout()
        r.addWidget(QLabel("Эффект смены:"))
        self.cmb_fx = QComboBox()
        self.cmb_fx.addItems(["Плавный", "Мгновенный", "Радуга", "Дыхание", "Пульс от баса"])
        fk = ["smooth", "instant", "rainbow", "breathe", "beat"]
        cur = C.get("color_fx", "smooth")
        self.cmb_fx.setCurrentIndex(fk.index(cur) if cur in fk else 0)
        self.cmb_fx.currentIndexChanged.connect(lambda i: self._set("color_fx", fk[i]))
        r.addWidget(self.cmb_fx, 1)
        gv.addLayout(r)
        ra = QHBoxLayout()
        ra.addWidget(QLabel("Алгоритм цвета:"))
        self.cmb_algo = QComboBox()
        self.cmb_algo.addItems(["Сочный (точный оттенок)", "Средний (плавный)", "K-means (3 кластера)"])
        ak = ["juicy", "average", "kmeans"]
        cura = C.get("color_algo", "juicy")
        self.cmb_algo.setCurrentIndex(ak.index(cura) if cura in ak else 0)
        self.cmb_algo.currentIndexChanged.connect(lambda i: self._set("color_algo", ak[i]))
        ra.addWidget(self.cmb_algo, 1)
        gv.addLayout(ra)
        g.setLayout(gv)
        v.addWidget(g)
        g2 = QGroupBox("Геометрия волны")
        g2v = QVBoxLayout()
        _slider_row(g2v, "X (центр)", 0, 100, int(C.get("viz_x", 0.5) * 100),
                    "%.2f", lambda v: self._set("viz_x", v))
        _slider_row(g2v, "Y (высота)", 2, 100, int(C.get("viz_y", 0.93) * 100),
                    "%.2f", lambda v: self._set("viz_y", v))
        _slider_row(g2v, "Ширина", 20, 100, int(C.get("viz_w", 0.92) * 100),
                    "%.2f", lambda v: self._set("viz_w", v))
        g2.setLayout(g2v)
        v.addWidget(g2)
        v.addStretch(1)
        return w

    def _page_snd(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        g = QGroupBox("Захват звука (общий, pyaudiowpatch)")
        gv = QVBoxLayout()
        r = QHBoxLayout()
        r.addWidget(QLabel("Источник:"))
        self.cmb_audio = QComboBox()
        self.cmb_audio.setMinimumWidth(260)
        self.cmb_audio.currentIndexChanged.connect(self._on_audio_pick)
        r.addWidget(self.cmb_audio, 1)
        self.btn_audio = QPushButton("↻")
        self.btn_audio.setObjectName("Ghost")
        self.btn_audio.setFixedWidth(40)
        self.btn_audio.clicked.connect(lambda: self.hooks.get("refresh_audio", lambda: None)())
        r.addWidget(self.btn_audio)
        gv.addLayout(r)
        self.btn_restart = QPushButton("⟳ Перезапустить захват звука")
        self.btn_restart.setObjectName("Ghost")
        self.btn_restart.clicked.connect(lambda: self.hooks.get("restart_audio", lambda: None)())
        gv.addWidget(self.btn_restart)
        self.lbl_in = QLabel("Вход: —")
        self.lbl_in.setObjectName("Hint")
        gv.addWidget(self.lbl_in)
        self.chk_demo = QCheckBox("Демо-сигнал (если нет музыки)")
        self.chk_demo.setChecked(bool(self._get().get("demo", False)))
        self.chk_demo.toggled.connect(lambda v: self._set("demo", bool(v)))
        gv.addWidget(self.chk_demo)
        g.setLayout(gv)
        v.addWidget(g)
        g2 = QGroupBox("Чувствительность")
        g2v = QVBoxLayout()
        C = self._get()
        _slider_row(g2v, "Чувствительность", 20, 300, int(C.get("sensitivity", 1.4) * 100),
                    "%.2f", lambda v: self._set("sensitivity", v))
        _slider_row(g2v, "Динамика", 100, 300, int(C.get("dynamics", 1.8) * 100),
                    "%.2f", lambda v: self._set("dynamics", v))
        _slider_row(g2v, "Бас", 0, 200, int(C.get("bass_gain", 1.0) * 100),
                    "%.2f", lambda v: self._set("bass_gain", v))
        _slider_row(g2v, "Середина", 0, 250, int(C.get("mid_gain", 1.25) * 100),
                    "%.2f", lambda v: self._set("mid_gain", v))
        _slider_row(g2v, "Верха", 0, 300, int(C.get("treble_gain", 1.7) * 100),
                    "%.2f", lambda v: self._set("treble_gain", v))
        _slider_row(g2v, "Полос", 16, 128, int(C.get("bars", 64)), "%d",
                    lambda v: self._set("bars", v), is_int=True)
        self.chk_bal = QCheckBox("Автобаланс (двигается вся волна)")
        self.chk_bal.setChecked(bool(C.get("auto_balance", True)))
        self.chk_bal.toggled.connect(lambda v: self._set("auto_balance", bool(v)))
        g2v.addWidget(self.chk_bal)
        g2.setLayout(g2v)
        v.addWidget(g2)
        v.addStretch(1)
        return w

    def _page_sys(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        g = QGroupBox("Оверлей и система")
        gv = QVBoxLayout()
        C = self._get()
        r = QHBoxLayout()
        r.addWidget(QLabel("FPS оверлея:"))
        self.cmb_fps = QComboBox()
        self.cmb_fps.addItems(["30 (эко)", "60 (баланс)", "120 (плавно)"])
        try:
            self.cmb_fps.setCurrentIndex([30, 60, 120].index(C.get("overlay_fps", 60)))
        except ValueError:
            self.cmb_fps.setCurrentIndex(1)
        self.cmb_fps.currentIndexChanged.connect(lambda i: (self._set("overlay_fps", [30, 60, 120][i]),
                                                             self.overlay.apply_fps()))
        r.addWidget(self.cmb_fps, 1)
        gv.addLayout(r)
        rmon = QHBoxLayout()
        rmon.addWidget(QLabel("Монитор оверлея:"))
        self.cmb_ovmon = QComboBox()
        self.cmb_ovmon.currentIndexChanged.connect(self._on_ovmon)
        rmon.addWidget(self.cmb_ovmon, 1)
        self.btn_ovmon = QPushButton("↻")
        self.btn_ovmon.setObjectName("Ghost")
        self.btn_ovmon.setFixedWidth(40)
        self.btn_ovmon.clicked.connect(self.refresh_overlaymon)
        rmon.addWidget(self.btn_ovmon)
        gv.addLayout(rmon)
        _slider_row(gv, "Прозрачность оверлея", 10, 100, int(C.get("overlay_opacity", 1.0) * 100),
                    "%.2f", lambda v: (self._set("overlay_opacity", v), self.overlay.apply_opacity()))
        _slider_row(gv, "FPS подсветки ПК", 1, 60, int(C.get("rgb_fps", 30)), "%d",
                    lambda v: self._set("rgb_fps", v), is_int=True)
        _slider_row(gv, "Пауза перед автостартом (с)", 0, 180, int(C.get("startup_delay_s", 20)), "%d",
                    lambda v: self._set("startup_delay_s", v), is_int=True)
        for key, title in [("click_through", "Клики сквозь (не мешает в играх)"),
                           ("always_on_top", "Оверлей всегда поверх"),
                           ("auto_hide", "Прятать волну в тишине"),
                           ("autostart", "Запуск с Windows"),
                           ("autosave", "Автосохранение")]:
            c = QCheckBox(title)
            c.setChecked(bool(C.get(key, True)))
            c.toggled.connect(lambda v, k=key: self._on_sys_check(k, v))
            gv.addWidget(c)
            setattr(self, f"chk_{key}", c)
        r2 = QHBoxLayout()
        self.btn_save = QPushButton("💾 Сохранить")
        self.btn_save.setObjectName("Ghost")
        self.btn_save.clicked.connect(lambda: self.hooks.get("save", lambda: None)())
        r2.addWidget(self.btn_save)
        self.btn_quit = QPushButton("Выход")
        self.btn_quit.setObjectName("Ghost")
        self.btn_quit.clicked.connect(lambda: self.hooks.get("quit", lambda: None)())
        r2.addWidget(self.btn_quit)
        gv.addLayout(r2)
        self.lbl_conf = QLabel("Настройки грузятся сами при старте.")
        self.lbl_conf.setObjectName("Hint")
        gv.addWidget(self.lbl_conf)
        g.setLayout(gv)
        v.addWidget(g)
        v.addStretch(1)
        return w

    def refresh_colormon(self):
        """Список экранов для замера цвета: 'Где оверлей' + все мониторы."""
        self.cmb_colormon.blockSignals(True)
        try:
            self.cmb_colormon.clear()
            self.cmb_colormon.addItem("Где оверлей", -1)
            for i, sc in enumerate(QApplication.screens()):
                g = sc.geometry()
                tag = " (основной)" if sc == QApplication.primaryScreen() else ""
                self.cmb_colormon.addItem(f"{i + 1}: {g.width()}×{g.height()}{tag}", i)
            try:
                cur = int(self._get().get("color_monitor", -1))
            except Exception:
                cur = -1
            ix = self.cmb_colormon.findData(cur)
            self.cmb_colormon.setCurrentIndex(ix if ix >= 0 else 0)
        finally:
            self.cmb_colormon.blockSignals(False)

    def _on_colormon(self, _i):
        try:
            self._set("color_monitor", int(self.cmb_colormon.currentData()))
        except Exception:
            pass

    def _on_region(self, i):
        """Пресет области: раскладывает в 4 значения конфига и двигает слайдеры."""
        try:
            key = self._reg_keys[i]
        except Exception:
            return
        self._set("color_region", key)
        if key == "custom":
            return
        try:
            from .overlay import REGION_PRESETS
            vals = dict(zip(("color_rx", "color_ry", "color_rw", "color_rh"),
                            REGION_PRESETS.get(key, REGION_PRESETS["full"])))
            for k, v in vals.items():
                self._set(k, v, save=False)
                s = self.sl_reg.get(k)
                if s is not None:
                    s.blockSignals(True)
                    try:
                        s.setValue(int(round(v * 100)))
                    finally:
                        s.blockSignals(False)
            self._set("color_region", key)
        except Exception:
            pass

    def _on_region_custom(self, key, val):
        """Ручной сдвиг слайдера области — переходим в режим 'Своя'."""
        try:
            self._set(key, max(0.0, min(1.0, float(val) / 100.0)))
        except Exception:
            pass
        try:
            if self._get().get("color_region") != "custom":
                self.cmb_region.blockSignals(True)
                try:
                    self.cmb_region.setCurrentIndex(self._reg_keys.index("custom"))
                finally:
                    self.cmb_region.blockSignals(False)
                self._set("color_region", "custom")
        except Exception:
            pass

    def refresh_overlaymon(self):
        self.cmb_ovmon.blockSignals(True)
        try:
            self.cmb_ovmon.clear()
            for i, sc in enumerate(QApplication.screens()):
                g = sc.geometry()
                tag = " (основной)" if sc == QApplication.primaryScreen() else ""
                self.cmb_ovmon.addItem(f"{i + 1}: {g.width()}×{g.height()}{tag}", i)
            try:
                cur = int(self._get().get("monitor", 0))
            except Exception:
                cur = 0
            ix = self.cmb_ovmon.findData(cur)
            self.cmb_ovmon.setCurrentIndex(ix if ix >= 0 else 0)
        finally:
            self.cmb_ovmon.blockSignals(False)

    def _on_ovmon(self, _i):
        try:
            idx = int(self.cmb_ovmon.currentData())
        except Exception:
            return
        self._set("monitor", idx)
        try:
            self.overlay.move_to_monitor(idx)
        except Exception:
            pass

    # ---------------- события ----------------
    def _on_follow(self, v):
        """Одна галочка на две вкладки: RGB и Экран→ПК всегда синхронны."""
        self._set("follow_screen", bool(v))
        for attr in ("chk_follow", "chk_follow_rgb"):
            w = getattr(self, attr, None)
            if w is not None and w.isChecked() != bool(v):
                w.blockSignals(True)
                try:
                    w.setChecked(bool(v))
                finally:
                    w.blockSignals(False)

    def _on_sat(self, v, origin):
        """Ползунок насыщенности один на две вкладки: RGB и Экран→ПК синхронны."""
        if getattr(self, "_sat_syncing", False):
            return
        self._sat_syncing = True
        try:
            v = max(0.0, min(1.5, float(v)))
            self._set("rgb_saturation", v)
            for attr in ("sl_sat_rgb", "sl_sat_amb"):
                s = getattr(self, attr, None)
                if s is not None and s is not origin:
                    s.setValue(int(round(v * 100)))
        finally:
            self._sat_syncing = False

    def _on_cmode(self, i):
        modes = ["neon", "green", "fire", "white", "custom"]
        if modes[i] == "custom":
            c = QColorDialog.getColor(QColor(*self._get().get("custom_color", [0, 220, 255])))
            if c.isValid():
                self._set("custom_color", [c.red(), c.green(), c.blue()])
                self._set("color_mode", "custom")
            else:
                self.cmb_cmode.setCurrentIndex(0)
        else:
            self._set("color_mode", modes[i])

    def _pick_fill(self):
        cur = self._get().get("fill_color", [0, 200, 255])
        c = QColorDialog.getColor(QColor(*cur))
        if c.isValid():
            self._set("fill_color", [c.red(), c.green(), c.blue()])

    def _pick_static(self):
        from PySide6.QtGui import QColor as QC
        h = self._get().get("static_color", "#C50DF1")
        c = QColorDialog.getColor(QC(h))
        if c.isValid():
            self._set("static_color", "#{:02X}{:02X}{:02X}".format(c.red(), c.green(), c.blue()))

    def _on_audio_pick(self, i):
        if i <= 0:
            self._set("audio_device", "")
        else:
            self._set("audio_device", self.cmb_audio.itemText(i))

    def _on_sys_check(self, key, val):
        self._set(key, bool(val))
        if key == "click_through":
            try:
                self.overlay.set_click_through(bool(val))
            except Exception:
                pass
        if key == "autostart":
            try:
                self.hooks.get("autostart", lambda v: None)(bool(val))
            except Exception:
                pass

    def _toggle_rgb(self):
        if self.state.get("running_rgb"):
            self.hooks.get("stop_rgb", lambda: None)()
        else:
            self.hooks.get("start_rgb", lambda d=0: None)(0)

    def _toggle_overlay(self):
        vis = not self.overlay.isVisible()
        self.overlay.setVisible(vis)
        self.btn_hide.setText("Скрыть оверлей" if vis else "Показать оверлей")

    # ---------------- живое обновление ----------------
    def set_audio_names(self, names, current):
        self.cmb_audio.blockSignals(True)
        try:
            self.cmb_audio.clear()
            self.cmb_audio.addItem("Системный вывод (авто)")
            for n in names:
                self.cmb_audio.addItem(n)
            if current and current in names:
                self.cmb_audio.setCurrentIndex(names.index(current) + 1)
        finally:
            self.cmb_audio.blockSignals(False)

    def set_devices(self, devices, cfg_devices):
        self.dev_list.clear()
        for name in devices:
            en = cfg_devices.get(name, {}).get("enabled", True)
            it = QListWidgetItem(("● " if en else "○ ") + name)
            self.dev_list.addItem(it)

    def refresh_live(self):
        try:
            _, _, level, _, has_sig = self.audio.snapshot()
        except Exception:
            level, has_sig = 0.0, False
        self.level_bar.setValue(int(max(0.0, min(1.0, level)) * 100))
        # превью экрана
        try:
            import numpy as _np
            col = tuple(int(v) for v in _np.clip(self.sc.shown, 0, 255))
            hx = "#{:02X}{:02X}{:02X}".format(*col)
            self.dot_screen.setStyleSheet(f"background:{hx};border-radius:22px;")
            self.lbl_screen_hex.setText(hx)
            self.fill_prev.setStyleSheet(f"background:{hx};border-radius:4px;")
        except Exception:
            pass
        try:
            r, g, b = self.state.get("rgb", (0, 0, 0))
            hx2 = "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))
            self.dot_rgb.setStyleSheet(f"background:{hx2};border-radius:22px;")
            self.lbl_rgb_hex.setText(hx2)
            self.static_prev.setStyleSheet(f"background:{self._get().get('static_color','#C50DF1')};border-radius:4px;")
        except Exception:
            pass
        st = self.state.get("status", "")
        err = self.state.get("error")
        aud = self.audio.status if hasattr(self.audio, "status") else ""
        mic = "🔊" if has_sig else "🔇"
        self.lbl_status.setText(f"{mic} {st} • {aud}")
        self.side_status.setText(f"{st}\n{aud}" + (f"\n⚠ {err}" if err else ""))
        self.btn_rgb.setText("⏸ RGB СТОП" if self.state.get("running_rgb") else "▶ RGB СТАРТ")
        try:
            dev = self._get().get("audio_device") or "авто"
            self.lbl_in.setText(f"Вход [{dev}]: {int(level*100)}% • {'звук' if has_sig else 'тихо'}")
        except Exception:
            pass
