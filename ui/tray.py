"""Значок в трее: цвет дуг показывает состояние OpenRGB, ленты и звука."""
import logging
import webbrowser

from PySide6.QtCore import QBuffer, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.status import component_status

log = logging.getLogger("aurafusion.web")


# Цвета состояния компонента в значке трея.
LEVEL_COLORS = {
    "ok": (60, 220, 110),      # работает
    "warn": (255, 190, 50),    # подключается / частично
    "bad": (255, 70, 70),      # не работает
    "off": (95, 100, 112),     # выключено или не настроено
}


def tray_icon(app=None, levels=None):
    """Значок AuraFusion для трея: кольцо из трёх дуг.

    Без аргументов дуги переливаются цветами RGB (так значок рисуется для
    вкладки браузера). С `levels` — кортежем из трёх состояний
    ("ok" | "warn" | "bad" | "off") для OpenRGB, ленты и звука — каждая дуга
    красится в цвет своего компонента, и по трею видно, что работает.

    Рисуем кодом, а не грузим файл: рядом может не оказаться ни одного
    .ico, а значок в трее нужен всегда, когда панель живёт в браузере, —
    иначе процесс нечем остановить, кроме как через диспетчер задач."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor(16, 20, 26, 235))
    p.setPen(QColor(0, 220, 255, 255) if levels is None else QColor(70, 80, 95, 255))
    p.drawEllipse(2, 2, 60, 60)
    # Углы drawArc берутся в 1/16 градуса, поэтому множитель 16 на обоих.
    p.setBrush(QColor(0, 0, 0, 0))
    default = (QColor(255, 40, 90), QColor(40, 255, 120), QColor(60, 140, 255))
    for i in range(3):
        if levels is None:
            col = default[i]
        else:
            col = QColor(*LEVEL_COLORS.get(levels[i], LEVEL_COLORS["off"]))
        pen = QPen(col)
        pen.setWidth(7)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(12, 12, 40, 40, -(112 + 120 * i) * 16, 88 * 16)
    p.end()
    return QIcon(pm)


_ICON_PNG = {}   # размер -> байты PNG


def icon_png(size=64):
    """PNG того же значка, что и в трее.

    Отдельный файл с картинкой разъехался бы со значком при первой же правке
    одного из двух, поэтому браузер получает ровно то, что рисует
    tray_icon().

    Рисуем в главном потоке и складываем в кэш: QPixmap из потока обработчика
    HTTP трогать нельзя, а картинка всё равно не меняется — цвета в значке
    заданы константами, не берутся от ленты.
    """
    key = int(size)
    if key in _ICON_PNG:
        return _ICON_PNG[key]
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    try:
        pix = tray_icon().pixmap(key, key)
        ok = pix.save(buf, "PNG")
        data = bytes(buf.data()) if ok else b""
    finally:
        buf.close()
    _ICON_PNG[key] = data
    return data


class Tray(QSystemTrayIcon):
    """Значок в трее: открыть панель, показать/спрятать оверлей, остановить всё.

    Без него процесс с панелью в браузере нечем было закрыть, кроме диспетчера
    задач, а закрывать его надо аккуратно: конфиг сохраняется, лента BLE
    отпускается, поток RGB останавливается. Выход из трея идёт через
    app.quit(), а тот вызывает aboutToQuit — то есть тот же путь, что у
    Ctrl+C в консоли, со всеми очистками."""
    def __init__(self, app, url, core, parent=None):
        super().__init__(parent)
        self._app = app
        self._url = url
        self._core = core
        self.setIcon(tray_icon())
        self.setToolTip("AuraFusion")
        self.activated.connect(self._on_activated)

        m = QMenu()
        # три строки состояния сверху меню: то же, что и цвет дуг значка
        self._st_actions = []
        for _ in range(3):
            a_st = QAction("…", m)
            a_st.setEnabled(False)
            m.addAction(a_st)
            self._st_actions.append(a_st)
        m.addSeparator()
        a_open = QAction("Открыть панель", m)
        a_open.triggered.connect(self.open_panel)
        m.addAction(a_open)
        self.a_ovl = QAction("Скрыть оверлей", m)
        self.a_ovl.triggered.connect(self.toggle_overlay)
        m.addAction(self.a_ovl)
        m.addSeparator()
        a_quit = QAction("Остановить всё", m)
        a_quit.triggered.connect(self.quit_all)
        m.addAction(a_quit)
        self._menu = m
        self.setContextMenu(m)
        self._levels = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_status)
        self._timer.start(2000)
        self.refresh_status()

    def refresh_status(self):
        """Перекрасить значок и обновить подсказку по состоянию компонентов."""
        try:
            st = component_status(self._core)
        except Exception:
            log.exception("tray status")
            return
        levels = (st["openrgb"][0], st["strip"][0], st["audio"][0])
        if levels != self._levels:
            self._levels = levels
            self.setIcon(tray_icon(levels=levels))
        names = (("OpenRGB", st["openrgb"]), ("Лента", st["strip"]), ("Звук", st["audio"]))
        mark = {"ok": "●", "warn": "◐", "bad": "○", "off": "–"}
        lines = ["%s %s: %s" % (mark[v[0]], n, v[1]) for n, v in names]
        for act, line in zip(self._st_actions, lines):
            act.setText(line)
        self.setToolTip("AuraFusion" + chr(10) + chr(10).join(lines))

    def open_panel(self):
        try:
            webbrowser.open(self._url)
        except Exception:
            pass

    def toggle_overlay(self):
        # core.toggle_overlay сам ходит через GuiBridge — из трея мы в главном
        # потоке, но единый путь всё равно надёжнее
        try:
            vis = self._core.toggle_overlay()
            self.a_ovl.setText("Скрыть оверлей" if vis else "Показать оверлей")
        except Exception:
            pass

    def quit_all(self):
        self._app.quit()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_panel()
