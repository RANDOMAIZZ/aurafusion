#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AuraFusion — web-интерфейс.

То же ядро, что и в десктопном main.py (звук, цвет с экрана, RGB-движок,
оверлей), но панель управления отдаётся в браузер. Десктопный вариант
лежит рядом и не тронут — запускать можно что-то одно: они оба берут
системный звук и пишут в подсветку, вдвоём работать не будут.

Запуск:
    python web_main.py [--port 8765] [--host 127.0.0.1] [--no-browser]
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import config as C
from core import ble_strip
from core import openrgb_raw
from core.audio_engine import SharedAudio
from core.screen_color import ScreenColor
from core.rgb_engine import (RGBEngine, hex_to_rgb, scale_color, kick_device_mode,
                             is_gpu_like, is_ble_target)
from ui.overlay import Overlay

# OpenRGB поднимаем тем же кодом, что и десктопная версия
from main import launch_openrgb

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
        try:
            if "overlay_fps" in keys:
                self.overlay.apply_fps()
            if "overlay_opacity" in keys:
                self.overlay.apply_opacity()
            if "monitor" in keys:
                self.overlay.move_to_monitor(int(cfg.get("monitor", 0) or 0))
            if "click_through" in keys:
                self.overlay.set_click_through(bool(cfg.get("click_through", True)))
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
        try:
            self.overlay_visible = not self.overlay.isVisible()
            self.overlay.setVisible(self.overlay_visible)
        except Exception:
            pass

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
            "saved_at": self.saved_flash,
            "devices_total": len(self.devices),
            # для фоновой анимации: бас + компактный спектр (считаем один раз)
            "bass": _spec_bass(_cspec),
            "spec": _cspec,
        }
        if extra_devices:
            cfg_devs = self.cfg.get("devices", {})
            out["devices"] = [
                {"name": n, "zones": z, "enabled": cfg_devs.get(n, {}).get("enabled", True),
                 "ble": cfg_devs.get(n, {}).get("transport") == "ble"}
                for n, z in self.devices.items()
            ]
        return out


ACTIONS = {}


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "AuraFusionWeb"
    protocol_version = "HTTP/1.1"
    core = None

    def log_message(self, *a):  # тише в консоль
        pass

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

    # ---------- маршруты ----------
    def do_GET(self):
        core = Handler.core
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("index.html")
        if path.startswith("/static/"):
            return self._file(path[len("/static/"):])
        if path == "/api/config":
            with core._lock:
                cfg = dict(core.cfg)
            return self._json({"config": cfg})
        if path == "/api/state":
            return self._json(core.snapshot(extra_devices=True))
        if path == "/api/audio":
            return self._json({"names": core.audio_names})
        if path == "/api/monitors":
            return self._json({"screens": core.screens})
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
            if path == "/api/action":
                name = data.get("name") or ""
                fn = ACTIONS.get(name)
                if fn is None:
                    return self._json({"ok": False, "error": "неизвестное действие"}, 400)
                res = fn(core, **data.get("args") or {})
                return self._json({"ok": True, "result": res})
        except Exception as e:
            log.error("action %s failed: %s", path, e)
            return self._json({"ok": False, "error": str(e)}, 500)
        self.send_error(404)


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
    """Тик в Qt-потоке: рассылка состояния и кэш мониторов."""
    t = QTimer()

    def tick():
        try:
            core.hub.publish(core.snapshot())
        except Exception:
            pass

    t.timeout.connect(tick)
    t.start(60)          # ~16 Гц: фон должен отзываться на музыку без задержки

    m = QTimer()

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

    s = QTimer()
    s.timeout.connect(core.save)
    s.start(5000)
    return t, m, s


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

    core = Core()
    Handler.core = core

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

    timers = _pump(core, app)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    th = threading.Thread(target=httpd.serve_forever, daemon=True, name="aura-web")
    th.start()
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

    app.aboutToQuit.connect(_bye)
    try:
        rc = app.exec()
    finally:
        _bye()
    sys.exit(rc)


if __name__ == "__main__":
    main()