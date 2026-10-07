#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AuraFusion — единая точка входа: оверлей + ПК-подсветка + экран→ПК."""
import os
import subprocess
import sys
import threading
import time
import logging
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap, QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from core import config as C
from core.audio_engine import SharedAudio
from core.screen_color import ScreenColor
from core.rgb_engine import RGBEngine, hex_to_rgb, scale_color, kick_device_mode, is_gpu_like
from core import openrgb_raw
from ui.overlay import Overlay
from ui.panel import Panel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "aurafusion.log")
logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
log = logging.getLogger("aurafusion")


def _exchook(t, v, tb):
    log.error("UNCAUGHT: %s", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _exchook
try:
    threading.excepthook = lambda a: log.error(
        "THREAD %s: %s", getattr(a, "threadName", "?"),
        "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
except Exception:
    pass
log.info("=== AuraFusion start ===")
OPENRGB_CANDIDATES = [
    os.path.join(BASE_DIR, "OpenRGB", "OpenRGB.exe"),
    r"E:\MusicRGB\OpenRGB\OpenRGB.exe",
]
STARTUP_NAME = "AuraFusion"


def find_openrgb():
    for p in OPENRGB_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def launch_openrgb(host, port):
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
        print(f"[openrgb] launch: {e}")
        return False


def is_startup():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, STARTUP_NAME)
            return True
    except Exception:
        return False


def set_startup(on: bool):
    try:
        import winreg
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            w = exe[:-10] + "pythonw.exe"
            if os.path.exists(w):
                exe = w
        cmd = f'"{exe}" "{os.path.abspath(__file__)}" --minimized'
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


def make_icon(color=(0, 229, 255)):
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    from PySide6.QtGui import QPainter, QBrush
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(*color)))
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QBrush(QColor(10, 20, 30)))
    for i, h in enumerate([14, 26, 38, 30, 20]):
        p.drawRect(16 + i * 7, 50 - h, 4, h)
    p.end()
    return QIcon(pm)


class App:
    def __init__(self, qapp, minimized=False):
        self.qapp = qapp
        self.cfg = C.load()
        self.state = {"running_rgb": False, "status": "старт...",
                      "rgb": (0, 0, 0), "error": None,
                      "rgb_level": 0.0, "rgb_brightness": 0.0}
        self.devices = {}  # name -> [(zone, leds)]
        self.audio = SharedAudio(lambda: self.cfg, n_bars=self.cfg.get("bars", 64))
        self.sc = ScreenColor(lambda: self.cfg)
        self.rgb_engine = None
        self.overlay = Overlay(lambda: self.cfg, self.audio, self.sc)
        hooks = {
            "start_rgb": self.start_rgb,
            "stop_rgb": self.stop_rgb,
            "test_color": self.test_color,
            "kick_mode": self.kick_mode,
            "refresh_devices": self.refresh_devices_async,
            "refresh_audio": self.refresh_audio_async,
            "restart_audio": self.restart_audio,
            "save": self.save,
            "quit": self.quit,
            "autostart": lambda v: set_startup(v),
            "geom_sync": self._sync_panel_geom,
        }
        self.panel = Panel(lambda: self.cfg, self.set_key,
                           self.audio, self.overlay, self.sc, self.state, hooks)
        self._setup_tray()
        # старт потоков
        self.audio.start()
        launch_openrgb(self.cfg.get("openrgb_host", "127.0.0.1"),
                       int(self.cfg.get("openrgb_port", 6742)))
        self.refresh_devices_async()
        self.refresh_audio_async()
        self.overlay.show()
        self.overlay.apply_opacity()
        self.overlay.move_to_monitor(int(self.cfg.get("monitor", 0) or 0))
        QTimer.singleShot(400, lambda: self.overlay.set_click_through(
            bool(self.cfg.get("click_through", True))))
        # панель не должна участвовать в замере цвета — отдаём её геометрию оверлею
        self._geom_timer = QTimer(self.panel)
        self._geom_timer.timeout.connect(self._sync_panel_geom)
        self._geom_timer.start(2000)
        self._sync_panel_geom()
        # сторож потока звука: если упал — поднять заново
        self._audio_watch = QTimer(self.panel)
        self._audio_watch.timeout.connect(self._watch_audio)
        self._audio_watch.start(5000)
        if minimized:
            self.panel.hide()
        else:
            self.panel.show()
        # автостарт RGB
        if self.cfg.get("rgb_enabled", True):
            try:
                delay = float(self.cfg.get("startup_delay_s", 20))
            except Exception:
                delay = 20.0
            QTimer.singleShot(1500, lambda: self.start_rgb(delay))

    def _sync_panel_geom(self):
        try:
            if self.panel.isVisible():
                g = self.panel.geometry()
                self.overlay._panel_geom = (g.x(), g.y(), g.width(), g.height())
            else:
                self.overlay._panel_geom = None
        except Exception:
            pass

    def _watch_audio(self):
        try:
            alive = self.audio.is_alive()
        except Exception:
            alive = False
        if not alive:
            log.warning("audio thread dead (status was: %s), restarting",
                        getattr(self.audio, "status", "?"))
            try:
                new = SharedAudio(lambda: self.cfg, n_bars=int(self.cfg.get("bars", 64)))
                new.start()
                self.audio = new
                self.overlay.audio = new
                log.info("audio thread restarted")
            except Exception as e:
                log.error("audio restart failed: %s", e)

    # ---------- конфиг ----------
    def set_key(self, key, value, save=True):
        self.cfg[key] = value
        if save and self.cfg.get("autosave", True):
            # тихий сейв без спама диска — пишем сразу, файл маленький
            C.save(self.cfg)

    def save(self):
        C.save(self.cfg)
        try:
            self.panel.lbl_conf.setText("Сохранено ✓")
        except Exception:
            pass

    # ---------- RGB ----------
    def _targets(self):
        out = []
        devs = self.cfg.get("devices", {})
        shared = self.cfg.get("static_color", "#C50DF1") if self.cfg.get("color_all") else None
        for name, d in devs.items():
            if not d.get("enabled", True):
                continue
            if self.devices and name not in self.devices:
                continue
            out.append({"device_name": name,
                        "zone_name": d.get("zone_name", "ARGB_V2_2"),
                        "leds": int(d.get("leds", 60)),
                        "color_hex": shared or d.get("color_hex", self.cfg.get("static_color", "#C50DF1"))})
        return out

    def start_rgb(self, delay=0.0):
        if self.state.get("running_rgb"):
            return
        if not self._targets():
            # устройств ещё нет — подождём сканирование, но движок запустим
            pass
        self.state["running_rgb"] = True
        self.rgb_engine = RGBEngine(lambda: self.cfg, self.audio, self.sc,
                                    self.state, self._targets, initial_delay=delay)
        self.rgb_engine.start()

    def stop_rgb(self):
        try:
            if self.rgb_engine is not None:
                self.rgb_engine.stop_flag.set()
        except Exception:
            pass
        self.state["running_rgb"] = False

    def kick_mode(self):
        """Ручной кик режима (кнопка 'Дёрнуть режим'): GPU, иначе все цели."""
        def work():
            cfg = self.cfg
            tgts = self._targets()
            names = [t["device_name"] for t in tgts if is_gpu_like(t["device_name"])]
            if not names:
                names = [t["device_name"] for t in tgts]
            for n in names:
                try:
                    kick_device_mode(cfg.get("openrgb_host", "127.0.0.1"),
                                     int(cfg.get("openrgb_port", 6742)), n)
                    self.state["status"] = f"режим дёрнут: {n[:24]}"
                except Exception as e:
                    self.state["error"] = str(e)
                    self.state["status"] = f"кик не удался: {e}"
        threading.Thread(target=work, daemon=True).start()

    def test_color(self):
        cfg = dict(self.cfg)
        def work():
            try:
                conn = openrgb_raw.OpenRGBRaw(cfg.get("openrgb_host", "127.0.0.1"),
                                              int(cfg.get("openrgb_port", 6742)))
                try:
                    import openrgb
                    c = openrgb.OpenRGBClient(address=cfg.get("openrgb_host", "127.0.0.1"),
                                              port=int(cfg.get("openrgb_port", 6742)))
                    try:
                        devs = {d.name: d for d in c.devices}
                    finally:
                        try:
                            c.disconnect()
                        except Exception:
                            pass
                    tgts = self._targets() or [{
                        "device_name": next(iter(devs)) if devs else "",
                        "zone_name": "ARGB_V2_2", "leds": 60,
                        "color_hex": cfg.get("static_color", "#C50DF1")}]
                    import time as _t
                    col = scale_color(hex_to_rgb(tgts[0]["color_hex"]), 1.0)
                    for t in tgts:
                        d = devs.get(t["device_name"])
                        if d is None:
                            continue
                        z = next((zz for zz in d.zones if zz.name == t["zone_name"]), d.zones[0] if d.zones else None)
                        if z is None:
                            continue
                        conn.update_zone_leds(d.device_id, z.id, [col] * int(t["leds"]))
                    self.state["status"] = "тест: 4с"
                    _t.sleep(4)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except Exception as e:
                self.state["error"] = str(e)
        threading.Thread(target=work, daemon=True).start()

    # ---------- устройства ----------
    def restart_audio(self):
        """Полный рестарт потока захвата (кнопка на вкладке Звук)."""
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
                log.info("audio restarted manually")
                self.state["status"] = "захват звука перезапущен"
            except Exception as e:
                log.error("manual audio restart failed: %s", e)
        threading.Thread(target=work, daemon=True).start()

    def refresh_devices_async(self):
        def work():
            devs = {}
            if not openrgb_raw.wait_for_server(self.cfg.get("openrgb_host", "127.0.0.1"),
                                               int(self.cfg.get("openrgb_port", 6742)),
                                               timeout=30.0, step=2.0):
                return
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
            # новые устройства — добавить в конфиг
            changed = False
            for name, zones in devs.items():
                if name not in self.cfg.get("devices", {}):
                    z0 = zones[0][0] if zones else "ARGB_V2_2"
                    n0 = zones[0][1] if zones and zones[0][1] > 0 else 60
                    self.cfg.setdefault("devices", {})[name] = {
                        "zone_name": z0, "leds": n0, "enabled": True,
                        "color_hex": self.cfg.get("static_color", "#C50DF1")}
                    changed = True
            if changed:
                C.save(self.cfg)
            try:
                self.panel.set_devices(devs, self.cfg.get("devices", {}))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def refresh_audio_async(self):
        def work():
            names = SharedAudio.list_loopbacks()
            try:
                self.panel.set_audio_names(names, self.cfg.get("audio_device", ""))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    # ---------- трей ----------
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self.panel)
        self.tray.setToolTip("AuraFusion — музыка + экран + ПК")
        menu = QMenu()
        a_show = QAction("Показать панель", self.panel)
        a_show.triggered.connect(lambda: (self.panel.show(), self.panel.raise_(), self.panel.activateWindow()))
        menu.addAction(a_show)
        a_over = QAction("Скрыть/показать оверлей", self.panel)
        a_over.triggered.connect(self.panel._toggle_overlay)
        menu.addAction(a_over)
        a_rgb = QAction("Старт/стоп RGB", self.panel)
        a_rgb.triggered.connect(self.panel._toggle_rgb)
        menu.addAction(a_rgb)
        menu.addSeparator()
        a_quit = QAction("Выход", self.panel)
        a_quit.triggered.connect(self.quit)
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.panel.isVisible():
                self.panel.hide()
            else:
                self.panel.show()
                self.panel.raise_()
                self.panel.activateWindow()

    def quit(self):
        try:  # вернуть экран из лупы, иначе останется приближённым
            self.overlay.sysfx.close()
        except Exception:
            pass
        try:
            C.save(self.cfg)
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        try:
            self.stop_rgb()
        except Exception:
            pass
        QApplication.quit()


def main():
    minimized = "--minimized" in sys.argv
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ap = App(app, minimized=minimized)
    def _on_quit():
        try:
            ap.overlay.sysfx.close()
        except Exception:
            pass
        try:
            C.save(ap.cfg)
        except Exception:
            pass
    app.aboutToQuit.connect(_on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
