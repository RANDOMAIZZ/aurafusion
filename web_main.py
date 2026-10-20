#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AuraFusion — единственная точка входа: панель управления в браузере.

Ядро (звук, цвет с экрана, RGB-движок, оверлей) живёт в core/ и ui/overlay.py.
Отдельной десктопной панели на PySide6 больше нет — она не умела того, что
умеет веб (например, прятать настройки, неуместные для выбранной фигуры),
и держать две копии одного интерфейса было дороже, чем пользоваться браузером.

Запуск:
    python web_main.py [--port 8765] [--host 127.0.0.1] [--no-browser]
"""
import faulthandler
import json
import gc
import logging
import os
import queue
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtCore import Qt, QBuffer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QPen
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def tray_icon(app=None):
    """Значок AuraFusion для трея: кольцо, переливающееся цветами RGB.

    Рисуем кодом, а не грузим файл: рядом может не оказаться ни одного
    .ico, а значок в трее нужен всегда, когда панель живёт в браузере, —
    иначе процесс нечем остановить, кроме как через диспетчер задач."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor(16, 20, 26, 235))
    p.setPen(QColor(0, 220, 255, 255))
    p.drawEllipse(2, 2, 60, 60)
    # Три дуги разных оттенков — намёк на кольца RGB. Углы drawArc берутся
    # в 1/16 градуса, а не в градусах: из-за этого дуги вылезали за круг
    # мелким огрызком у края.
    p.setBrush(QColor(0, 0, 0, 0))
    for i, col in enumerate((QColor(255, 40, 90), QColor(40, 255, 120), QColor(60, 140, 255))):
        pen = QPen(col)
        pen.setWidth(7)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        # Углы drawArc — в 1/16 градуса, поэтому множитель 16 нужен на обоих.
        # Со стартовым углом без него все три дуги ложились в одно и то же место
        # и кольцо выглядело одноцветным: зелёный перекрывал красный, а синий
        # — оба.
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


class GuiBridge(QObject):
    """Переносит вызовы Qt-виджетов в главный поток.

    HTTP-обработчики крутятся в своих потоках, а окном оверлея владеет
    главный поток Qt. Прямой setVisible() из потока — гонка: виджет
    перерисовывается там же, где крутится event loop, и всё приложение
    встаёт. Сигнал между потоками Qt доставляет в очередь главного потока
    сам, поэтому вызов выполняется там, где безопасно."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run)

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            log.exception("gui call failed")

    def post(self, fn):
        """Поставить вызов в очередь главного потока."""
        self.call.emit(fn)

from core import config as C
from core import autostart as autostart_mod
from core import ble_strip
from core import openrgb_raw
from core import scenes
from core.audio_engine import SharedAudio
from core.screen_color import ScreenColor
from core.rgb_engine import (RGBEngine, hex_to_rgb, scale_color, kick_device_mode,
                             is_gpu_like, is_ble_target)
from ui.overlay import Overlay

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPENRGB_CANDIDATES = [
    os.path.join(BASE_DIR, "OpenRGB", "OpenRGB.exe"),
    r"E:\MusicRGB\OpenRGB\OpenRGB.exe",
]


def find_openrgb():
    for p in OPENRGB_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def launch_openrgb(host, port):
    """Поднять сервер OpenRGB, если его ещё нет.

    Раньше эта функция жила в десктопном main.py, а веб импортировал её
    оттуда. Стоило удалить main.py — и веб перестал запускаться совсем."""
    import subprocess
    exe = find_openrgb()
    if not exe:
        return False
    try:
        out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq OpenRGB.exe"],
                             capture_output=True, text=True, timeout=10).stdout
        if "OpenRGB.exe" in out:
            return True
    except Exception:
        pass
    try:
        log = open(os.path.join(BASE_DIR, "openrgb_stdout.log"), "ab", buffering=0)
        subprocess.Popen([exe, "--server", "--server-host", host,
                          "--server-port", str(port), "--startminimized"],
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00004000)
        return True
    except Exception as e:
        log.error("openrgb launch: %s", e)
        return False


STARTUP_NAME = "AuraFusionWeb"


def set_startup(on):
    """Автозапуск именно web-варианта: путь считаем от web_main.py."""
    try:
        import winreg
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            w = exe[:-10] + "pythonw.exe"
            if os.path.exists(w):
                exe = w
        cmd = f'"{exe}" "{os.path.abspath(__file__)}" --no-browser'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, STARTUP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, STARTUP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"[startup] {e}")
        return False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "webui")
LOG_PATH = os.path.join(BASE_DIR, "aurafusion.log")
logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
log = logging.getLogger("aurafusion.web")

# Дамп при падении — в отдельный файл, потому что обычный лог при 0xc0000005
# обрывается на полуслове: процесс умирает, не дописав ни строчки.
# Происходило это с pythonw в python314.dll без внятной причины, и без этого
# файла каждый раз оставалось только «процесс умер, в логе пусто».
CRASH_PATH = os.path.join(BASE_DIR, "crash.log")
try:
    _crash = open(CRASH_PATH, "a", buffering=1, encoding="utf-8")
    faulthandler.enable(file=_crash, all_threads=True)
except Exception:      # не фатально: без дампа просто по-старому
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        pass


def _exchook(t, v, tb):
    log.error("UNCAUGHT: %s", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _exchook
try:
    threading.excepthook = lambda a: log.error(
        "THREAD %s: %s", getattr(a, "threadName", "?"),
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
except Exception:
    pass
log.info("=== AuraFusion web start ===")


class Hub:
    """Рассылка состояния подписчикам SSE. Медленному клиенту рвём хвост,
    чтобы он не копил очередь и не тормозил."""

    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()

    def add(self):
        q = queue.Queue(maxsize=3)
        with self._lock:
            self._clients.append(q)
        return q

    def remove(self, q):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def has_clients(self):
        """Есть ли хоть один подписчик. Проверяется без снимка и без
        сериализации — иначе пустая рассылка стоила бы работы главному
        потоку каждые 60 мс."""
        with self._lock:
            return bool(self._clients)

    def publish(self, obj):
        try:
            payload = json.dumps(obj, ensure_ascii=False)
        except Exception:
            return
        with self._lock:
            targets = list(self._clients)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()      # рвём самый старый кадр
                    q.put_nowait(payload)
                except Exception:
                    pass


def _compact_spec(spec, bins=32):
    """Спектр -> n бакетов 0..1 для фоновой анимации.

    audio_engine отдаёт спектр, обрезанный в 0..3 (log1p + усиление полос),
    поэтому нормируем на 3. Низкая треть бакетов — это бас: engine сам же
    домножает первые n/3 полос bass_gain, так что граница честная.
    Bass из audio.snapshot() не годится: он в относительной шкале (~1e-5),
    для анимации нужен именно средний уровень низких полос."""
    try:
        n = len(spec)
        if n <= 0:
            return []
        out = []
        for i in range(bins):
            a = int(n * i / bins)
            b = max(a + 1, int(n * (i + 1) / bins))
            seg = spec[a:b]
            m = max(float(v) for v in seg)
            out.append(round(max(0.0, min(1.0, m / 3.0)), 3))
        return out
    except Exception:
        return []


def _spec_bass(comp):
    """Средний уровень низкой трети спектра 0..1 — «бас» для фона.

    comp — уже нормализованный список (см. _compact_spec). Проверка длины
    вместо truthiness: на numpy-массиве `not arr` бросает исключение."""
    try:
        if comp is None or len(comp) == 0:
            return 0.0
        low = comp[:max(1, len(comp) // 3)]
        return round(max(0.0, min(1.0, sum(low) / len(low) * 2.2)), 3)
    except Exception:
        return 0.0


class Core:
    """Ядро приложения без Qt-панели: всё, что нужно и панели, и браузеру."""

    def __init__(self):
        self.cfg = C.load()
        self.state = {"running_rgb": False, "status": "старт...",
                      "rgb": (0, 0, 0), "error": None,
                      "rgb_level": 0.0, "rgb_brightness": 0.0}
        self.devices = {}
        self.audio_names = []
        self.screens = []
        self.hub = Hub()
        self.rgb_engine = None
        self.overlay_visible = True
        self.saved_flash = 0.0
        self._lock = threading.RLock()

        self.audio = SharedAudio(lambda: self.cfg, n_bars=self.cfg.get("bars", 64))
        self.sc = ScreenColor(lambda: self.cfg)
        # мост создаётся в главном потоке Qt — там же, где живёт event loop,
        # поэтому его сигналы доставляются без блокировки
        self.gui = GuiBridge()
        self.overlay = Overlay(lambda: self.cfg, self.audio, self.sc)

    # ---------- конфиг ----------
    def get_cfg(self):
        return self.cfg

    def set_keys(self, patch, save=True):
        patch = {k: v for k, v in (patch or {}).items()
                 if k in C.DEFAULTS or k == "devices"}
        with self._lock:
            for k, v in patch.items():
                self.cfg[k] = v
            autosave = bool(self.cfg.get("autosave", True))
        self._apply_side_effects(patch)
        if save and autosave:
            self.save()
        return True

    def save(self):
        with self._lock:
            ok = C.save(self.cfg)
        self.saved_flash = time.time()
        return ok

    def _apply_side_effects(self, keys):
        """Побочные эффекты настроек — в Qt-панели они вызывались инлайн."""
        cfg = self.cfg
        gui = self.gui
        try:
            if "overlay_fps" in keys:
                gui.post(self.overlay.apply_fps)
            if "color_sample_ms" in keys:
                # Интервал замера читался только при запуске, поэтому ползунок
                # менял цифру в конфиге и ничего больше. Теперь применяется
                # сразу: таймер просто перезапускается с новым шагом.
                gui.post(self.overlay.apply_sample_ms)
            if "overlay_opacity" in keys:
                gui.post(self.overlay.apply_opacity)
            if "monitor" in keys:
                idx = int(cfg.get("monitor", 0) or 0)
                gui.post(lambda: self.overlay.move_to_monitor(idx))
            if "click_through" in keys:
                on = bool(cfg.get("click_through", True))
                gui.post(lambda: self.overlay.set_click_through(on))
            if "avoid_taskbar" in keys:
                # геометрия окна считается при создании, поэтому снять или
                # вернуть галку без перезапуска можно только переставив окно
                idx = int(cfg.get("monitor", 0) or 0)
                gui.post(lambda: self.overlay.move_to_monitor(idx))
        except Exception as e:
            log.warning("side effects %s: %s", list(keys), e)

    # ---------- RGB ----------
    def targets(self):
        out = []
        devs = self.cfg.get("devices", {})
        shared = self.cfg.get("static_color", "#C50DF1") if self.cfg.get("color_all") else None
        for name, d in devs.items():
            if not d.get("enabled", True):
                continue
            color_hex = shared or d.get("color_hex", self.cfg.get("static_color", "#C50DF1"))
            if d.get("transport") == "ble":
                out.append({"device_name": name, "transport": "ble",
                            "addr": d.get("addr", ""),
                            "zone_name": d.get("zone_name", "STRIP"),
                            "leds": int(d.get("leds", 1)), "color_hex": color_hex})
                continue
            if self.devices and name not in self.devices:
                continue
            out.append({"device_name": name,
                        "zone_name": d.get("zone_name", "ARGB_V2_2"),
                        "leds": int(d.get("leds", 60)), "color_hex": color_hex})
        return out

    def start_rgb(self, delay=0.0):
        if self.state.get("running_rgb"):
            return False
        self.state["running_rgb"] = True
        self.rgb_engine = RGBEngine(lambda: self.cfg, self.audio, self.sc,
                                    self.state, self.targets, initial_delay=delay,
                                    log=log.info)
        self.rgb_engine.start()
        return True

    def stop_rgb(self):
        try:
            if self.rgb_engine is not None:
                self.rgb_engine.stop_flag.set()
        except Exception:
            pass
        self.state["running_rgb"] = False

    def toggle_rgb(self):
        if self.state.get("running_rgb"):
            self.stop_rgb()
        else:
            self.start_rgb(0.0)

    def kick_mode(self):
        def work():
            tgts = [t for t in self.targets() if not is_ble_target(t)]
            names = [t["device_name"] for t in tgts if is_gpu_like(t["device_name"])]
            if not names:
                names = [t["device_name"] for t in tgts]
            for n in names:
                try:
                    kick_device_mode(self.cfg.get("openrgb_host", "127.0.0.1"),
                                     int(self.cfg.get("openrgb_port", 6742)), n)
                    self.state["status"] = f"режим дёрнут: {n[:24]}"
                except Exception as e:
                    self.state["error"] = str(e)
                    self.state["status"] = f"кик не удался: {e}"
        threading.Thread(target=work, daemon=True).start()

    def test_color(self):
        def work():
            try:
                tgts = self.targets()
                ble = [t for t in tgts if is_ble_target(t)]
                orgb = [t for t in tgts if not is_ble_target(t)]
                col = scale_color(hex_to_rgb(tgts[0]["color_hex"]), 1.0) if tgts else (197, 13, 241)
                for t in ble:
                    try:
                        s = ble_strip.get_strip(t["addr"], max_fps=8, log=log.info)
                        s.set_hardware_brightness(1.0)
                        s.paint(col)
                    except Exception as e:
                        self.state["error"] = f"BLE: {e}"
                if orgb:
                    conn = openrgb_raw.OpenRGBRaw(self.cfg.get("openrgb_host", "127.0.0.1"),
                                                  int(self.cfg.get("openrgb_port", 6742)))
                    try:
                        import openrgb
                        c = openrgb.OpenRGBClient(address=self.cfg.get("openrgb_host", "127.0.0.1"),
                                                  port=int(self.cfg.get("openrgb_port", 6742)))
                        try:
                            devs = {d.name: d for d in c.devices}
                        finally:
                            try:
                                c.disconnect()
                            except Exception:
                                pass
                        for t in orgb:
                            d = devs.get(t["device_name"])
                            if d is None:
                                continue
                            z = next((zz for zz in d.zones if zz.name == t["zone_name"]),
                                     d.zones[0] if d.zones else None)
                            if z is None:
                                continue
                            conn.update_zone_leds(d.device_id, z.id, [col] * int(t["leds"]))
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                self.state["status"] = "тест: 4с"
                time.sleep(4)
            except Exception as e:
                self.state["error"] = str(e)
        threading.Thread(target=work, daemon=True).start()

    # ---------- устройства / звук ----------
    def refresh_devices_async(self):
        def work():
            devs = {}
            ble = {}
            try:
                if self.cfg.get("ble_enabled", True):
                    ble = ble_strip.scan(timeout=float(self.cfg.get("ble_scan_s", 8)))
                    for addr, nm in ble.items():
                        devs[nm] = [("STRIP", 1)]
                        log.info("BLE strip found: %s %s", nm, addr)
            except Exception as e:
                log.warning("BLE scan failed: %s", e)
            if openrgb_raw.wait_for_server(self.cfg.get("openrgb_host", "127.0.0.1"),
                                           int(self.cfg.get("openrgb_port", 6742)),
                                           timeout=30.0, step=2.0):
                for _ in range(5):
                    try:
                        import openrgb
                        c = openrgb.OpenRGBClient(address=self.cfg.get("openrgb_host", "127.0.0.1"),
                                                  port=int(self.cfg.get("openrgb_port", 6742)))
                        try:
                            for d in c.devices:
                                devs[d.name] = [(z.name, len(z.leds)) for z in d.zones]
                            break
                        finally:
                            try:
                                c.disconnect()
                            except Exception:
                                pass
                    except Exception:
                        time.sleep(2)
            self.devices = devs
            changed = False
            cfg_devs = self.cfg.setdefault("devices", {})
            for name, zones in devs.items():
                if name not in cfg_devs:
                    z0 = zones[0][0] if zones else "ARGB_V2_2"
                    n0 = zones[0][1] if zones and zones[0][1] > 0 else 60
                    cfg_devs[name] = {"zone_name": z0, "leds": n0, "enabled": True,
                                      "color_hex": self.cfg.get("static_color", "#C50DF1")}
                    changed = True
            for nm, addr in ble.items():
                d = cfg_devs.get(nm)
                if isinstance(d, dict) and d.get("transport") == "ble" and not d.get("addr"):
                    d["addr"] = addr
                    changed = True
            if changed:
                C.save(self.cfg)
            self.hub.publish(self.snapshot(extra_devices=True))
            if ble:
                self.state["status"] = f"найдено BLE-лент: {len(ble)}"
        threading.Thread(target=work, daemon=True).start()

    def refresh_audio_async(self):
        def work():
            try:
                self.audio_names = SharedAudio.list_loopbacks()
            except Exception as e:
                log.warning("audio list failed: %s", e)
        threading.Thread(target=work, daemon=True).start()

    def restart_audio(self):
        def work():
            try:
                try:
                    self.audio.stop()
                except Exception:
                    pass
                time.sleep(0.3)
                new = SharedAudio(lambda: self.cfg, n_bars=int(self.cfg.get("bars", 64)))
                new.start()
                self.audio = new
                self.overlay.audio = new
                self.state["status"] = "захват звука перезапущен"
                log.info("audio restarted (web)")
            except Exception as e:
                log.error("audio restart failed: %s", e)
                self.state["error"] = str(e)
        threading.Thread(target=work, daemon=True).start()

    def toggle_overlay(self):
        # Прямой вызов виджета из потока HTTP-обработчика вешал всё
        # приложение: окном владеет главный поток Qt. Отдаём через мост.
        # Состояние держим сами — читать isVisible() из чужого потока тоже
        # нельзя, он не синхронизирован с главным.
        want = not self.overlay_visible
        self.overlay_visible = want
        self.gui.post(lambda: self.overlay.setVisible(want))
        return want

    # ---------- снимок для браузера ----------
    def snapshot(self, extra_devices=False):
        st = self.state
        spec = None
        _cspec = []
        try:
            spec, _wave, level, _bass, has_sig = self.audio.snapshot()
            _cspec = _compact_spec(spec, 32)
        except Exception:
            level, has_sig = 0.0, False
        try:
            with self.sc.lock:
                scr = [int(v) for v in self.sc.shown]
        except Exception:
            scr = [0, 0, 0]
        pc = st.get("rgb", (0, 0, 0))
        out = {
            "screen_hex": "#{:02X}{:02X}{:02X}".format(*[max(0, min(255, v)) for v in scr]),
            "pc_hex": "#{:02X}{:02X}{:02X}".format(*[max(0, min(255, int(v))) for v in pc]),
            "level": float(max(0.0, min(1.0, level))),
            "has_sig": bool(has_sig),
            "status": st.get("status", ""),
            "error": st.get("error"),
            "running_rgb": bool(st.get("running_rgb")),
            "overlay_visible": bool(self.overlay_visible),
            "audio_status": getattr(self.audio, "status", ""),
            "audio_device": self.cfg.get("audio_device", "") or "авто",
            "audio_active": getattr(self.audio, "active", "") or "",
            "brightness": float(st.get("rgb_brightness", 0.0)),
            # Почему может не работать цвет с экрана: интерфейс закрывает его
            # целиком. Молча это выглядит как «цвет сломался», хотя настройки
            # верные, — потому показываем прямо.
            "screen_color_blocked": bool(getattr(self.overlay, "_panel_blocked", False)),
            "panel_cover": round(float(getattr(self.overlay, "_panel_cover", 0.0)), 2),
            # Сама найденная геометрия окна панели. Без неё в состоянии видно
            # только «закрывает 100%», и непонятно откуда взялось: окно не
            # найдено, найдено не то или найдено свёрнутым. Три разные причины
            # лечатся по-разному, а выглядят одинаково.
            "panel_geom": list(getattr(self.overlay, "_panel_geom", None) or []),
            "panel_geom_src": getattr(self.overlay, "_panel_geom_src", ""),
            "panel_on_top": bool(getattr(self.overlay, "_panel_on_top", False)),
            # Откуда берутся отказы замера. Цвет держится на прошлом значении
            # не из-за «лени», а когда кадр не дошёл; без этих счётчиков
            # «то реагирует, то зависает» нечем было лечить.
            "sample_stat": dict(getattr(self.overlay, "_stat", {}) or {}),
            # Реальный fps оверлея, а не заданный в настройках. Разница видна
            # сразу: настроено 60, выдаёт 22 — значит кадр не укладывается.
            "overlay_fps_actual": round(float(getattr(self.overlay, "_fps_real", 0.0)), 1),
            "grab_busy": bool(getattr(self.overlay, "_grab_busy", False)),
            "grab_core": getattr(self.overlay, "_grab_core", None),
            # Провал между тиками главного потока. Большой разрыв = приложение
            # подвисало (долгий проход сборщика мусора, зависший Qt).
            "tick_gap": round(float(getattr(self.overlay, "_tick_gap", 0.0)), 3),
            "tick_gaps": int(getattr(self.overlay, "_tick_gaps", 0)),
            "saved_at": self.saved_flash,
            "devices_total": len(self.devices),
            # для фоновой анимации: бас + компактный спектр (считаем один раз)
            "bass": _spec_bass(_cspec),
            "spec": _cspec,
        }
        if extra_devices:
            cfg_devs = self.cfg.get("devices", {})
            # Состояние подключения BLE отдаём наружу: connected/error живут
            # в core/ble_strip.py, но наверх не поднимались, и по панели
            # отличить «лента отвалилась» от «лента горит чёрным» было
            # невозможно — а это две разные поломки с разными лекарствами.
            ble_state = _ble_state()
            out["devices"] = [
                {"name": n, "zones": z, "enabled": cfg_devs.get(n, {}).get("enabled", True),
                 "ble": cfg_devs.get(n, {}).get("transport") == "ble",
                 "ble_state": _ble_for(cfg_devs, n, ble_state)}
                for n, z in self.devices.items()
            ]
        return out


def _ble_state():
    """Что сейчас с каждой BLE-лентой: адрес -> подключена ли и что за ошибка."""
    out = {}
    try:
        from core import ble_strip
        for addr, st in list(getattr(ble_strip, "_registry", {}).items()):
            out[addr] = {"connected": bool(getattr(st, "connected", False)),
                         "error": str(getattr(st, "error", "") or "")}
    except Exception:
        pass
    return out


def _ble_for(cfg_devs, name, ble_state):
    """Состояние ленты для конкретного устройства из списка."""
    info = cfg_devs.get(name) or {}
    addr = str(info.get("addr", "") or info.get("address", "")).upper()
    st = ble_state.get(addr) or {}
    return {"addr": addr,
            "connected": bool(st.get("connected")),
            "error": st.get("error", ""),
            # лента не трогает GATT, пока нечего посылать, поэтому «не
            # подключена» при пустом цене — это норма, а не поломка
            "idle": not addr}


ACTIONS = {}


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def apply_scene(core, sid):
    """Применить сцену по id. Возвращает применённый patch или None.

    Сцену ищем и среди готовых, и среди пользовательских, и пропускаем через
    scenes.sanitize: сцены лежат в конфиге на диске, а он тоже редактируется
    руками. Ключи вне SAFE_KEYS (устройства, порты, автозапуск) отсекаются —
    иначе сцена могла бы выключить подсветку не там или сломать запуск."""
    for s in scenes.all_scenes(core.cfg):
        if s.get("id") != sid:
            continue
        clean = scenes.sanitize(s)
        if not clean:
            return None
        core.set_keys(clean["patch"])
        return clean["patch"]
    return None


def style_profiles():
    """Список фигур и набор настроек для каждой.

    Считается один раз из ui.overlay — того же места, откуда берёт оверлей.
    Интерфейсу нужно, чтобы прятать неуместные ползунки: у круга нет смысла
    в «ширине по краям экрана», у волны — в «центре фигуры»."""
    global _STYLE_PROFILES
    if _STYLE_PROFILES is None:
        try:
            from ui.overlay import STYLES, per_style_keys, style_keys
            _STYLE_PROFILES = {
                "styles": [[k, n] for k, n in STYLES],
                # keys — что показывать; own — что писать в личный список стиля
                "keys": {k: sorted(style_keys(k)) for k, _ in STYLES},
                "own": {k: sorted(per_style_keys(k)) for k, _ in STYLES},
            }
        except Exception as e:
            log.warning("style profiles: %s", e)
            _STYLE_PROFILES = {"styles": [], "keys": {}}
    return _STYLE_PROFILES


_STYLE_PROFILES = None


class Handler(BaseHTTPRequestHandler):
    server_version = "AuraFusionWeb"
    protocol_version = "HTTP/1.1"
    core = None

    def log_message(self, fmt, *args):
        # Раньше здесь был pass: любой разбор запроса, в том числе ошибка,
        # молча уходил в никуда — под pythonw stderr всё равно не видно.
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---------- утилиты ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _file(self, name):
        path = os.path.normpath(os.path.join(WEB_DIR, name))
        if not path.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml"}.get(os.path.splitext(path)[1].lower(),
                                              "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Время ожидания на соединение. Без него keep-alive вкладка держит свой
    # поток часами: handle_one_request() висит в read() до следующего запроса.
    # Такие потоки копятся, и при обрыве сети или переходе с Wi-Fi на провод
    # им конца нет: соединение может висеть до бесконечности.
    timeout = 30

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (TimeoutError, socket.timeout):
            self.close_connection = True
        except OSError:
            # Клиент отвалился посреди ответа. Это его дело, не наша ошибка.
            self.close_connection = True

    def _icon(self, size):
        data = icon_png(size)
        if not data:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- маршруты ----------
    def do_GET(self):
        core = Handler.core
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("index.html")
        # /favicon.ico отдаём тем же PNG: браузер запрашивает его сам, когда
        # в странице нет тега link, а вкладывать .ico только ради этого
        # значит держать вторую копию картинки
        if path in ("/favicon.ico", "/favicon.png"):
            return self._icon(64)
        if path.startswith("/static/"):
            return self._file(path[len("/static/"):])
        if path == "/api/config":
            with core._lock:
                cfg = dict(core.cfg)
            # Показываем панели настоящее состояние автозапуска, а не значение
            # из файла. Разъехаться они могут, если запись в реестре удалили
            # руками или запись указывала на удалённый файл — галочка стояла
            # бы «включено», а автозагрузки не было.
            cfg["autostart"] = autostart_mod.is_enabled()
            cfg["autostart_info"] = autostart_mod.status()
            return self._json({"config": cfg})
        if path == "/api/state":
            return self._json(core.snapshot(extra_devices=True))
        if path == "/api/audio":
            return self._json({"names": core.audio_names})
        if path == "/api/monitors":
            return self._json({"screens": core.screens})
        if path == "/api/styles":
            return self._json(style_profiles())
        if path == "/api/scenes":
            return self._json({"scenes": scenes.all_scenes(core.cfg)})
        if path == "/api/stream":
            return self._stream(core)
        self.send_error(404)

    def _stream(self, core):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = core.hub.add()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=5.0)
                except queue.Empty:
                    payload = json.dumps(core.snapshot(), ensure_ascii=False)
                self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            core.hub.remove(q)

    def do_POST(self):
        core = Handler.core
        path = self.path.split("?")[0]
        data = self._body()
        try:
            if path == "/api/set":
                core.set_keys(data.get("patch") or {})
                return self._json({"ok": True})
            if path == "/api/scene":
                # применение сцены: подставляем её patch обычным set_keys,
                # поэтому побочные эффекты (монитор, клик сквозь, прозрачность)
                # срабатывают те же самые, что и при ручной правке
                res = apply_scene(core, data.get("id"))
                return self._json({"ok": res is not None, "result": res or {}})
            if path == "/api/action":
                name = data.get("name") or ""
                if name == "dump_sample":
                    return self._json({"ok": True, "result": _dump_sample(core)})
                if name == "panel_geom":
                    return self._json({"ok": True, "result": _panel_geom(core, data.get("args") or {})})
                if name == "autostart":
                    # on лежит внутри args, а не на верхнем уровне тела:
                    # панель шлёт {name, args:{on}}. Раньше читалось
                    # data.get("on") — всегда None, то есть всегда «выключить».
                    a = data.get("args") or {}
                    # on — основное имя; value принимаем тоже, потому что
                    # старая панель слала именно его и галочка работала
                    # только наполовину
                    want = a.get("on", a.get("value"))
                    return self._json({"ok": True, "result": _autostart(core, want)})
                fn = ACTIONS.get(name)
                if fn is None:
                    return self._json({"ok": False, "error": "неизвестное действие"}, 400)
                res = fn(core, **data.get("args") or {})
                return self._json({"ok": True, "result": res})
        except Exception as e:
            log.error("action %s failed: %s", path, e)
            return self._json({"ok": False, "error": str(e)}, 500)
        self.send_error(404)


def _dump_sample(core):
    """Выгрузить то, что оверлей реально видит на экране.

    Нужен для разбора «цвет определяется неверно»: агент в своём контексте
    снимает экран и всегда получает чёрный кадр, а у живого процесса
    grabWindow работает. Поэтому снимок и числа делает сам оверлей.
    """
    import numpy as np
    from core import screen_color as SC
    try:
        arr = core.overlay._last_sample
        if arr is None:
            return {"ok": False, "error": "сэмпл ещё не снят"}
        arr = np.asarray(arr, dtype=np.float32)
        h, w = arr.shape[0], arr.shape[1]
        lines = ["сэмпл %dx%d" % (w, h)]
        flat = (np.clip(arr, 0, 1) * 255).astype(int).reshape(-1, 3)
        q = (flat // 24).astype(np.int32)
        key = q[:, 0] * 121 + q[:, 1] * 11 + q[:, 2]
        uniq, cnt = np.unique(key, return_counts=True)
        order = np.argsort(-cnt)[:8]
        tot = float(cnt.sum())
        lines.append("крупнейшие пятна в кадре замера:")
        for i in order:
            if cnt[i] < tot * 0.005:
                continue
            m = key == uniq[i]
            avg = flat[m].mean(axis=0).astype(int)
            mx = flat[m].max(axis=1)
            mn = flat[m].min(axis=1)
            sat = float(((mx - mn) / np.maximum(mx, 1)).mean())
            lines.append("   %5.2f%%  rgb(%3d,%3d,%3d)  насыщ.%.2f"
                         % (100.0 * cnt[i] / tot, avg[0], avg[1], avg[2], sat))
        lines.append("алгоритмы на этом же кадре:")
        lines.append("   average  %02X%02X%02X" % tuple(SC.average_target(arr)))
        lines.append("   juicy    %02X%02X%02X" % tuple(SC.juicy_target(arr, {}, 1)))
        lines.append("   kmeans   %02X%02X%02X" % tuple(SC.kmeans_target(arr, lock={})))
        st = core.state
        lines.append("в приложении сейчас: screen=%s pc=%s raw=%s shown=%s"
                     % (st.get("screen_hex"), st.get("pc_hex"),
                        np.round(core.overlay.sc.raw).astype(int).tolist(),
                        np.round(core.overlay.sc.shown).astype(int).tolist()))
        lines.append("настройки: %s" % {k: core.cfg.get(k) for k in
                                        ("color_region", "color_algo", "color_hold",
                                         "auto_speed", "color_speed", "exclude_viz",
                                         "shape_color", "color_monitor")})
        pg = getattr(core.overlay, "_panel_geom", None)
        lines.append("геометрия панели от браузера: %s  <- None значит панель "
                     "НЕ вырезана из замера" % (pg,))
        # и сам кадр картинкой, чтобы посмотреть глазами что именно измеряется
        from PIL import Image
        p = os.path.join(BASE_DIR, ".overlay_shots", "sample.png")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Image.fromarray((np.clip(arr, 0, 1) * 255).astype("uint8")).resize(
            (w * 6, h * 6), Image.NEAREST).save(p)
        lines.append("кадр сохранён: %s" % p)
        txt = os.path.join(BASE_DIR, ".overlay_shots", "sample.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return {"ok": True, "path": p, "text": txt}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def detect_panel_rect():
    """Найти окно панели средствами Windows.

    Возвращает пару: (прямоугольник окна или None, на переднем ли оно плане).

    Браузер сообщает своё положение через window.screenX/screenY/
    outerWidth/outerHeight, и это ненадёжно: у развёрнутого окна Opera
    outerWidth равен ширине экрана даже когда вкладка показывает половину
    страницы. Поэтому ищем окно напрямую — заголовок панели начинается с
    «AuraFusion», и это видит сама Windows, а не догадка из браузера.

    Прямоугольник и «на переднем ли плане» — разные вещи, и нужны обе:

    - вырезать панель из кадра надо, даже когда она сзади: она всё равно
      занимает место в кадре, пока её видно;
    - объявлять, что «интерфейс закрывает экран», можно только когда
      панель на переднем плане. Раньше это не проверялось, и выходило
      так: окно панели развёрнуто на весь экран, лежит на заднем плане,
      пользователь смотрит видео в другом окне — а оверлей всё равно
      объявлял «100%» и замер вставал. Окно позади ничего не закрывает:
      видно то, что впереди.

    Свёрнутое окно (IsIconic) не берём: в панели задач оно висит, но экран
    не занимает. Свои окна (оверлей, трей) пропускаем по PID.
    """
    try:
        import win32gui
        import win32process
    except ImportError:
        return None, False
    me = os.getpid()
    try:
        fg = win32gui.GetForegroundWindow()
        handles = []
        win32gui.EnumWindows(lambda h, _a: handles.append(h), None)
    except Exception:
        return None, False
    best = None
    for h in handles:
        try:
            if not win32gui.IsWindowVisible(h):
                continue
            if win32gui.IsIconic(h):
                continue          # свёрнуто в панель задач — не занимает экран
            title = win32gui.GetWindowText(h)
            if not title or "aurafusion" not in title.lower():
                continue
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid == me:
                continue
            l, t, r, b = win32gui.GetWindowRect(h)
        except Exception:
            continue
        w, ht = r - l, b - t
        if w <= 120 or ht <= 120:
            continue
        area = w * ht
        if best is None or area > best[0]:
            best = (area, (int(l), int(t), int(w), int(ht)), h == fg)
    if best is None:
        return None, False
    return best[1], best[2]


def _panel_geom(core, args):
    """Запомнить, где на экране окно панели, чтобы вырезать его из замера.

    Браузер знает своё положение на экране (window.screenX/screenY), а оверлей
    про него не знал ничего: код вырезания в ui/overlay.py был написан, но
    переменная _panel_geom нигде не присваивалась, поэтому условие
    `pg is not None` никогда не выполнялось.

    Прямоугольник, который больше экрана, не принимаем: при нескольких
    открытых вкладках отчёт может прийти от скрытой, а у неё размеры окна
    бывают любыми. Иначе оверлей решил бы, что интерфейс закрывает всё, и
    замер встал бы совсем.
    """
    def num(k, d):
        try:
            return float(args.get(k, d))
        except (TypeError, ValueError):
            return float(d)
    x, y = num("x", -1), num("y", -1)
    w, h = num("w", 0), num("h", 0)
    ok = w > 0 and h > 0
    if ok:
        try:
            sc = core.overlay.screen()
            geo = sc.geometry() if sc is not None else None
            if geo is not None and geo.width() > 0:
                if w > geo.width() * 1.25 or h > geo.height() * 1.25:
                    ok = False
        except Exception:
            pass
    if not ok:
        with core._lock:
            core.overlay._panel_geom = None
            core.overlay._panel_geom_src = "браузер отклонил"
        return {"geom": None, "rejected": True}
    g = (int(x), int(y), int(w), int(h))
    with core._lock:
        core.overlay._panel_geom = g
        core.overlay._panel_geom_src = "браузер"
    # Передним планом браузер себя назвать не может, а рапорт он шлёт
    # раз в 4 секунды из вкладки, которая может быть и на заднем плане.
    # Поэтому «закрывает экран» от браузера не включаем — определитель
    # windows всё равно скажет точно через 3 секунды. Лишний замер хуже,
    # чем лишняя маска, а вот застывший цвет не разморозить.
    return {"geom": g, "rejected": False}


def _autostart(core, on):
    """Включить/выключить автозапуск и записать это в конфиг.

    Галочка в панели шла в конфиг, но ничего не делала: ключ autostart был
    заведён, а читать его было некому. Плюс в реестре лежала запись на
    main.py — файл удалён при переходе на веб-версию, и Windows при входе
    молча пытался запустить то, чего уже нет.
    """
    want = bool(on)
    if want:
        ok, info = autostart_mod.enable()
    else:
        ok, info = autostart_mod.disable()
    if ok:
        with core._lock:
            core.cfg["autostart"] = want
        try:
            C.save(core.cfg)
        except Exception:
            log.exception("не сохранил конфиг после смены автозапуска")
        log.info("автозапуск %s: %s", "включён" if want else "выключен", info)
    else:
        log.error("автозапуск не переключился: %s", info)
    st = autostart_mod.status()
    st["ok"] = ok
    st["info"] = info
    return st


@action("start_rgb")
def _a_start(core, delay=0.0):
    return core.start_rgb(float(delay or 0.0))


@action("stop_rgb")
def _a_stop(core):
    core.stop_rgb()
    return True


@action("toggle_rgb")
def _a_toggle_rgb(core):
    core.toggle_rgb()
    return True


@action("toggle_overlay")
def _a_toggle_overlay(core):
    core.toggle_overlay()
    return True


@action("refresh_devices")
def _a_refresh_devices(core):
    core.refresh_devices_async()
    return True


@action("refresh_audio")
def _a_refresh_audio(core):
    core.refresh_audio_async()
    return True


@action("restart_audio")
def _a_restart_audio(core):
    core.restart_audio()
    return True


@action("test_color")
def _a_test_color(core):
    core.test_color()
    return True


@action("kick_mode")
def _a_kick(core):
    core.kick_mode()
    return True


@action("save")
def _a_save(core):
    return core.save()


@action("autostart")
def _a_autostart(core, value=False):
    return bool(set_startup(bool(value)))


@action("noop")
def _a_noop(core):
    return True


@action("reload_config")
def _a_reload(core):
    with core._lock:
        core.cfg = C.load()
    return True


@action("quit")
def _a_quit(core):
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return True


def _pump(core, app):
    """Тик в Qt-потоке: рассылка состояния, кэш мониторов, автосохранение.

    Таймеры обязательно с родителем `app`. Раньше они создавались без него и
    вызывающий код держал их в локальной переменной `timers`, которая больше
    нигде не использовалась: пока жив фрейм main(), всё работало, а стоило
    переменную убрать — таймеры собрал бы сборщик мусора, и вместе с ними
    молча умерли бы рассылка в веб-панель и автосохранение конфига раз в 5 с.
    С `app` родителем их держит Qt, и голый вызов _pump(core, app) достаточен.
    """
    t = QTimer(app)

    def tick():
        try:
            # Снимок и его сериализация — работа на главном потоке, а
            # подписчиков часто нет (панель закрыта). Проверяем список
            # подписчиков ДО снимка: пусто — делать нечего.
            if core.hub.has_clients():
                core.hub.publish(core.snapshot())
        except Exception:
            pass

    t.timeout.connect(tick)
    t.start(60)          # ~16 Гц: фон должен отзываться на музыку без задержки

    m = QTimer(app)

    def mon():
        try:
            screens = QApplication.screens()
            prim = QApplication.primaryScreen()
            core.screens = [{"i": i, "w": s.geometry().width(), "h": s.geometry().height(),
                             "primary": s == prim} for i, s in enumerate(screens)]
        except Exception:
            pass

    m.timeout.connect(mon)
    m.start(2000)
    mon()

    s = QTimer(app)

    def autosave():
        # Флаг проверяем здесь, а не один раз при старте: иначе переключатель
        # «Автосохранение» выглядел бы работающим, но через 5 секунд таймер
        # всё равно писал бы конфиг на диск.
        try:
            if core.cfg.get("autosave", True):
                core.save()
        except Exception:
            pass

    s.timeout.connect(autosave)
    s.start(5000)        # автосохранение: без него настройки терялись бы при выходе


def _panel_front(core, on_top):
    """Переключить «панель на переднем плане», отфильтровав дребезг.

    Windows меняет активное окно часто — на клик, на смену вкладки, просто по
    своему таймеру, — и без фильтра «панель то впереди, то сзади» скакал
    6 раз за 15 секунд: замер то брался, то нет, и цвет дёргался.

    Фильтр сделан по ВРЕМЕНИ, а не по числу ответов. Вариант «два одинаковых
    ответа» при опросе раз в 3 секунды давал 6 секунд ожидания, а с включённым
    «Мгновенным» эффектом это читалось как «цвет меняется через 6 секунд» —
    ровно то, на что жаловался пользователь. Теперь опрос идёт часто
    (см. _panel_timer), и ждать нужно меньше секунды.
    """
    ov = core.overlay
    st = getattr(core, "_panel_front", None)
    if st is None:
        st = {"last": None, "since": 0.0}
        core._panel_front = st
    now = time.monotonic()
    on_top = bool(on_top)
    if on_top != st["last"]:
        st["last"] = on_top
        st["since"] = now
        return
    if now - st["since"] < 0.45:
        return
    ov._panel_on_top = on_top


def _panel_timer(core, app):
    """Быстрый опрос окна панели.

    Отдельный таймер, а не сторож: сторож ходит раз в 3 секунды и нужен для
    того, чтобы чинить, — а передний план надо знать быстро, от него
    напрямую зависит, берётся ли цвет с экрана. Опрос стоит меньше
    миллисекунды (EnumWindows по видимым окнам), так что 400 мс не жалко.
    """
    t = QTimer(app)

    def poll():
        try:
            g, on_top = detect_panel_rect()
            ov = core.overlay
            if g != getattr(ov, "_panel_geom", None):
                ov._panel_geom = g
                ov._panel_geom_src = "win32" if g else "не найдено"
            _panel_front(core, on_top)
        except Exception:
            pass

    t.timeout.connect(poll)
    t.start(400)
    return t


def _watchdog(core, app, httpd):
    """Сторож: поднимает то, что перестало работать, и пишет об этом в лог.

    Проверяет две вещи, обе ломались «молча»:

    - Поток http-сервера. Раньше его падение означало, что панель не отвечает
      навсегда: сокет слушал, принимать было некому, а значок в трее и оверлей
      продолжали работать, так что всё выглядело как «зависло».
    - Тики оверлея. Если оверлей виден, а метка последнего тика не
      обновлялась дольше нескольких секунд, значит он не рисует. Перезапускаем
      таймер: дешевле, чем искать, что именно встало.
    """
    w = QTimer(app)

    def check():
        try:
            if not any(t.name == "aura-web" and t.is_alive() for t in threading.enumerate()):
                log.error("сторож: поток http-сервера мёртв, поднимаю заново")
                threading.Thread(target=_run_server, args=(httpd,), daemon=True,
                                 name="aura-web").start()
                return
        except Exception:
            log.exception("сторож: не смог проверить сервер")

        # Окно панели ищем сами, средствами Windows: браузер сообщает
        # outerWidth неверно (у развёрнутого окна он равен ширине экрана).
        # Передний план проверяем тут же и тут же: окно могло уйти в фон,
        # а геометрия остаться прежней — тогда «закрывает экран» надо
        # снять, иначе замер встанет, хотя перед пользователем видео.
        try:
            g, on_top = detect_panel_rect()
            ov = core.overlay
            if g != getattr(ov, "_panel_geom", None):
                ov._panel_geom = g
                ov._panel_geom_src = "win32" if g else "не найдено"
            _panel_front(core, on_top)
        except Exception:
            pass

        try:
            ov = core.overlay
            if not ov.isVisible():
                return
            idle = time.monotonic() - getattr(ov, "_last_tick", 0.0)
            if idle > 6.0:
                log.error("сторож: оверлей не тикает %.1f с, перезапускаю таймер", idle)
                ov.apply_fps()
        except Exception:
            log.exception("сторож: не смог проверить оверлей")

    w.timeout.connect(check)
    w.start(3000)
    return w


def _run_server(httpd):
    """Сервер с переподъёмом после падения. См. пояснение в main()."""
    while True:
        try:
            httpd.serve_forever(poll_interval=0.4)
            return              # shutdown() — выходим штатно
        except Exception:
            log.exception("http-сервер упал, поднимаю заново через 2 с")
            time.sleep(2.0)


def main():
    args = sys.argv[1:]
    port = 8765
    host = "127.0.0.1"
    open_browser = "--no-browser" not in args
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a == "--host" and i + 1 < len(args):
            host = args[i + 1]

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # без значка в трее процесс с панелью в браузере закрывать нечем: ни одного
    # своего окна у него нет. Значок появляем только если система реально его
    # показывает — иначе меню «Остановить всё» просто некуда нажать.
    if QSystemTrayIcon.isSystemTrayAvailable():
        app.setQuitOnLastWindowClosed(False)
    else:
        log.warning("трея нет — закрывать процесс придётся через диспетчер задач")

    core = Core()
    Handler.core = core

    # Автозапуск. Если в конфиге он включён, а в реестре записи нет или она
    # указывает на удалённый main.py — чиним. Иначе пользователь видит
    # включённую галочку, а при входе в систему ничего не запускается.
    if core.cfg.get("autostart"):
        if not autostart_mod.is_enabled():
            ok, info = autostart_mod.enable()
            if ok:
                log.info("автозапуск починен при старте: %s", info)
            else:
                log.error("автозапуск включён в настройках, но не сработал: %s", info)

    core.audio.start()
    launch_openrgb(core.cfg.get("openrgb_host", "127.0.0.1"),
                   int(core.cfg.get("openrgb_port", 6742)))
    core.refresh_devices_async()
    core.refresh_audio_async()

    core.overlay.show()
    core.overlay.apply_opacity()
    core.overlay.move_to_monitor(int(core.cfg.get("monitor", 0) or 0))
    QTimer.singleShot(400, lambda: core.overlay.set_click_through(
        bool(core.cfg.get("click_through", True))))

    _pump(core, app)

    # Значок для вкладки браузера рисуем заранее, в главном потоке и до
    # старого сервера: обработчик HTTP отдаёт только готовые байты.
    try:
        icon_png(64)
    except Exception as e:
        log.warning("иконку вкладки отдать не выйдет: %s", e)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    # Поток сервера с переподъёмом после падения. Раньше был голый
    # serve_forever в демон-потоке: любое исключение из него убивало поток,
    # сокет оставался слушающим, а принимать было некому — панель не отвечала
    # навсегда, и выглядело это как «зависло». Исключение уходило в stderr, а
    # под pythonw stderr никуда не пишется, так что следов не оставалось.
    threading.Thread(target=_run_server, args=(httpd,), daemon=True,
                     name="aura-web").start()
    _watchdog(core, app, httpd)
    _panel_timer(core, app)
    url = f"http://{host}:{port}/"
    log.info("web ui: %s", url)
    print(f"[AuraFusion web] {url}  (Ctrl+C — выход)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if core.cfg.get("rgb_enabled", True):
        try:
            delay = float(core.cfg.get("startup_delay_s", 20))
        except Exception:
            delay = 20.0
        QTimer.singleShot(1500, lambda: core.start_rgb(delay))

    def _bye():
        try:
            core.overlay.sysfx.close()
        except Exception:
            pass
        try:
            core.save()
        except Exception:
            pass
        try:
            core.audio.stop()
        except Exception:
            pass
        try:
            core.stop_rgb()
        except Exception:
            pass
        try:
            ble_strip.close_all()
        except Exception:
            pass

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(app, url, core)
        tray.show()
        log.info("трея: значок AuraFusion добавлен")

    app.aboutToQuit.connect(_bye)
    # Сборщик мусора. Приложение живёт часами и рисует кадры, создавая мусор
    # каждый кадр. Циклический сборщик по умолчанию со временем обходит всё
    # больший кучу, и такой проход занимает СЕКУНДЫ. Проход — это нативный
    # код, он держит GIL, и всё приложение на это время замирает: поток
    # http-сервера не может проснуться, и панель перестаёт отвечать.
    #
    # Признаки были все характерные: сразу после запуска сервер отвечает за
    # 0-14 мс, через два часа — 2.4-3 с; py-spy показывал главный поток в
    # app.exec(), а треть замеров стека не читалась вовсе (сборщик в это
    # время в C-коде, питоновского кадра нет).
    #
    # Что делаем: после старта замораживаем всё, что уже живёт, — сборщику
    # больше не нужно это пересматривать; и делаем сборы реже. Пороги по
    # умолчанию (700, 10, 10) для процесса, который непрерывно создаёт
    # объекты, слишком шумные.
    try:
        gc.collect()
        gc.freeze()
        gc.set_threshold(50000, 50, 50)
        log.info("gc: постоянные объекты заморожены, сборы пореже")
    except Exception as e:
        log.warning("gc: не настроил (%s)", e)
    try:
        rc = app.exec()
    finally:
        _bye()
    sys.exit(rc)


if __name__ == "__main__":
    main()