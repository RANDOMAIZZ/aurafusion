"""RGB-движок ПК: цвет с экрана + пульс яркости от музыки.

Режимы (rgb_mode):
  mix    — цвет с экрана (ScreenColor), яркость качает бас (выбор пользователя)
  screen — цвет с экрана, яркость фиксированная (max_brightness)
  music  — статичный цвет static_color/device color, яркость качает бас (как старый MusicRGB)
Анти-лаг: heartbeat + send_threshold + Direct один раз + startup_delay.
"""
import math
import threading
import time

from . import openrgb_raw


def hex_to_rgb(h):
    h = h.strip().lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def scale_color(c, b):
    return (int(round(c[0] * b)), int(round(c[1] * b)), int(round(c[2] * b)))


def apply_gains(rgb, cfg):
    """Баланс белого автотракта: подкрутить каналы под свои диоды/глаза
    (на пониженной яркости зелёный часто холодит в бирюзу)."""
    try:
        gr = max(0.3, min(1.5, float(cfg.get("rgb_gain_r", 1.0))))
        gg = max(0.3, min(1.5, float(cfg.get("rgb_gain_g", 1.0))))
        gb = max(0.3, min(1.5, float(cfg.get("rgb_gain_b", 1.0))))
    except Exception:
        return rgb
    if gr == 1.0 and gg == 1.0 and gb == 1.0:
        return rgb
    return (min(255, int(rgb[0] * gr)),
            min(255, int(rgb[1] * gg)),
            min(255, int(rgb[2] * gb)))


def apply_led_floor(rgb, cfg):
    """Пол яркости на выходе: итоговый цвет никогда не гаснет ниже
    min_brightness (иначе тёмная сцена = чёрный цвет × минимум = ноль
    и подсветка 'вырубается'). Оттенок сохраняется, чёрный даёт
    едва видимый серый. Потолок 24/255 — не слепим в темноте."""
    try:
        need = int(round(255 * max(0.0, min(1.0, float(cfg.get("min_brightness", 0.02))))))
    except Exception:
        return rgb
    need = min(need, 24)
    if need <= 0:
        return rgb
    m = max(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    if m >= need:
        return rgb
    if m <= 0:
        g = max(4, need // 4)
        return (g, g, g)
    f = need / m
    return (min(255, int(rgb[0] * f)),
            min(255, int(rgb[1] * f)),
            min(255, int(rgb[2] * f)))


def apply_dark_threshold(rgb, cfg):
    """Порог темноты 0..255: цвета темнее порога мягко гаснут к чёрному.
    0 = выключено. Влияет на все цвета подсветки."""
    try:
        thr = max(0, min(255, int(cfg.get("dark_threshold", 0))))
    except Exception:
        return rgb
    if thr <= 0:
        return rgb
    v = max(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    if v >= thr:
        return rgb
    f = (v / thr) ** 1.5
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def force_direct(d):
    try:
        cur = getattr(d, "active_mode", None)
        if cur is not None and str(getattr(cur, "name", "")).lower() == "direct":
            return
    except Exception:
        pass
    try:
        d.set_mode("Direct")
        return
    except Exception:
        pass
    try:
        other = next((md.name for md in d.modes if md.name != "Direct"), None)
        if other:
            try:
                d.set_mode(other)
            except Exception:
                pass
            d.set_mode("Direct")
    except Exception:
        try:
            d.set_custom_mode()
        except Exception:
            pass


def _rgb_to_hsv(r, g, b):
    r1, g1, b1 = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r1, g1, b1), min(r1, g1, b1)
    d = mx - mn
    if d <= 1e-9:
        return (0.0, 0.0, mx)
    if mx == r1:
        h = ((g1 - b1) / d) % 6.0
    elif mx == g1:
        h = (b1 - r1) / d + 2.0
    else:
        h = (r1 - g1) / d + 4.0
    return (h / 6.0, d / mx if mx > 0 else 0.0, mx)


def _hsv_to_rgb(h, s, v):
    if s <= 0:
        g = int(round(v * 255))
        return (g, g, g)
    hp = (h % 1.0) * 6.0
    c = v * s
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    m = v - c
    seg = int(hp) % 6
    table = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x),
             (0.0, x, c), (x, 0.0, c), (c, 0.0, x)]
    rr, gg, bb = table[seg]
    conv = lambda q: int(max(0, min(255, round((q + m) * 255))))
    return (conv(rr), conv(gg), conv(bb))


def saturate_color(r, g, b, amount):
    """Дожим насыщенности для канала ПК через HSV: оттенок (H) и яркость (V)
    сохраняются строго, растёт только S. Почти ахроматические цвета
    (белый/серый, S < 0.12) не трогаем — иначе белый экран красит ПК
    в оранжевый, а серый — в цветной. Зелёный остаётся зелёным."""
    try:
        a = max(0.0, min(1.5, float(amount)))
    except Exception:
        return (int(r), int(g), int(b))
    if a <= 0:
        return (int(r), int(g), int(b))
    h, s, v = _rgb_to_hsv(r, g, b)
    if s <= 0.02:
        return (int(r), int(g), int(b))
    k = min(1.0, max(0.0, (s - 0.12) / 0.18))  # 0 до S=0.12, полный буст от S=0.30
    if k <= 0:
        return (int(r), int(g), int(b))
    s = min(1.0, s * (1.0 + a * 1.2 * k))
    return _hsv_to_rgb(h, s, v)


def is_gpu_like(name):
    """Эвристика: видеокарты (у них чаще всего залипает Direct, напр. GALAX)."""
    n = (name or "").lower()
    return any(k in n for k in ("geforce", "radeon", "nvidia", "galax",
                                "rtx", "gtx", " gpu"))


def kick_device_mode(host, port, device_name):
    """Дёрнуть режим устройства: другой -> Direct.
    Лечит залипание, когда OpenRGB показывает Direct, но пакеты цвета
    игнорируются (ручной трюк 'выбрать другое и вернуть Direct').
    Не падает, если конкретный режим устройство не принимает — важно,
    чтобы в итоге встал Direct, а не чтобы панель сыпала ошибками."""
    import openrgb
    c = openrgb.OpenRGBClient(address=host, port=port)
    try:
        d = next((x for x in c.devices if x.name == device_name), None)
        if d is None:
            raise RuntimeError("устройство не найдено: %s" % device_name)
        other = next((md.name for md in d.modes if md.name != "Direct"), None)
        if other:
            try:
                d.set_mode(other)
            except Exception:
                pass
            time.sleep(0.2)
        try:
            d.set_mode("Direct")
        except Exception:
            force_direct(d)
    finally:
        try:
            c.disconnect()
        except Exception:
            pass


def resolve_zone(cfg_target, conn):
    import openrgb
    c = openrgb.OpenRGBClient(address=cfg_target["_host"], port=cfg_target["_port"])
    try:
        for d in c.devices:
            if d.name != cfg_target["device_name"]:
                continue
            for z in d.zones:
                if z.name != cfg_target["zone_name"]:
                    continue
                native = len(z.leds)
                if native == 0:
                    conn.resize_zone(d.device_id, z.id, int(cfg_target["leds"]))
                    time.sleep(0.3)
                n = min(int(cfg_target["leds"]), native) if native > 0 else int(cfg_target["leds"])
                force_direct(d)
                return d.device_id, z.id, n
        raise RuntimeError("device/zone not found")
    finally:
        try:
            c.disconnect()
        except Exception:
            pass


def is_ble_target(t):
    """BLE-лента вместо OpenRGB-устройства (transport='ble' в конфиге)."""
    return str(t.get("transport", "")).lower() == "ble"


class OrgbSink:
    """Приёмник цвета для устройства под управлением OpenRGB."""

    def __init__(self, engine, t, dev_id, zone, n):
        self.engine = engine
        self.dev_id = dev_id
        self.zone = zone
        self.n = n

    def paint(self, rgb):
        # conn читается у движка в момент отправки — после переподключения
        # сокет свежий, а замыкание на старый был бы источник «мёртвых» целей
        self.engine.conn.update_zone_leds(self.dev_id, self.zone, [rgb] * self.n)

    def close(self):
        pass


class BleSink:
    """Приёмник цвета для BLE-ленты: та же логика цвета/яркости, свой транспорт."""

    def __init__(self, addr, max_fps=20, log=None):
        from . import ble_strip
        self.strip = ble_strip.get_strip(addr, max_fps=max_fps, log=log)
        self.addr = addr

    def paint(self, rgb, brightness=None):
        # brightness игнорируется: как и у зон OpenRGB, яркость уже вшита
        # в цвет движком (apply_led_floor(scale_color(base, prev))). Отдельный
        # кадр яркости на клоне стоит ~130 мс и убивает частоту обновления.
        self.strip.paint(rgb)

    def close(self):
        pass


class RGBEngine(threading.Thread):
    def __init__(self, cfg_getter, audio, screencolor, state, targets_fn,
                 initial_delay=0.0, log=None):
        super().__init__(daemon=True)
        self._cfg = cfg_getter
        self.audio = audio
        self.sc = screencolor
        self.state = state
        self._targets_fn = targets_fn
        self._initial_delay = max(0.0, float(initial_delay or 0.0))
        self.log = log or (lambda *a: None)
        self.stop_flag = threading.Event()
        self.conn = None
        self._targets = None
        self._sig = None

    def _make_ble_sink(self, t, cfg):
        try:
            fps = float(cfg.get("ble_max_fps", 20))
        except Exception:
            fps = 20.0
        sink = BleSink(t.get("addr", ""), max_fps=fps, log=self.log)
        if sink.strip is None:
            raise RuntimeError("BLE-адрес не задан")
        return sink

    def _current_base(self, t, cfg):
        mode = cfg.get("rgb_mode", "mix")
        if mode in ("mix", "screen"):
            with self.sc.lock:
                shown = self.sc.shown.copy()
            rgb = (int(shown[0]), int(shown[1]), int(shown[2]))
            rgb = saturate_color(*rgb, cfg.get("rgb_saturation", 0.6))
            rgb = apply_gains(rgb, cfg)
            return apply_dark_threshold(rgb, cfg)
        # music — статичный
        try:
            if cfg.get("color_all"):
                res = hex_to_rgb(cfg.get("static_color", "#C50DF1"))
            else:
                res = hex_to_rgb(t.get("color_hex", cfg.get("static_color", "#C50DF1")))
        except Exception:
            res = (197, 13, 241)
        return apply_dark_threshold(res, cfg)

    def run(self):
        state = self.state
        self._kicked = False  # авто-кик GPU делается один раз за запуск движка
        if self._initial_delay > 0:
            state["status"] = f"пауза {int(self._initial_delay)}с (ПК загружается)..."
            if self.stop_flag.wait(self._initial_delay):
                state["running_rgb"] = False
                return
        prev = 0.02
        avg = 0.0005
        next_t = time.perf_counter()
        last_sig = None
        last_send_t = 0.0
        conn_fails = 0
        names = ""
        try:
            while not self.stop_flag.is_set():
                cfg = self._cfg()
                if not cfg.get("rgb_enabled", True):
                    state["status"] = "подсветка ПК выключена"
                    self.stop_flag.wait(0.5)
                    continue
                host, port = cfg.get("openrgb_host", "127.0.0.1"), int(cfg.get("openrgb_port", 6742))
                dt = 1.0 / max(1, int(cfg.get("rgb_fps", 30)))
                live = list(self._targets_fn())
                need_orgb = any(not is_ble_target(t) for t in live)
                # OpenRGB нужен только если есть хоть одно его устройство:
                # BLE-лента обязана работать и когда сервер OpenRGB не поднят
                if need_orgb and self.conn is None:
                    try:
                        self.conn = openrgb_raw.OpenRGBRaw(host, port)
                        state["error"] = None
                        conn_fails = 0
                    except Exception as e:
                        conn_fails += 1
                        state["error"] = str(e)
                        state["status"] = f"ожидание OpenRGB ({e})..."
                        self.stop_flag.wait(min(3.0 + conn_fails, 10.0))
                        continue
                sig = tuple(sorted((t["device_name"], str(t["zone_name"]), int(t["leds"])) for t in live))
                if sig != self._sig:
                    self._sig = sig
                    self._targets = None
                    names = ", ".join(t["device_name"][:18] for t in live) or "нет устройств"
                    if not state.get("error"):
                        state["status"] = f"RGB работает | {names}"
                if self._targets is None:
                    resolved, bad = [], []
                    for t in live:
                        try:
                            if is_ble_target(t):
                                resolved.append((t, self._make_ble_sink(t, cfg)))
                            else:
                                tt = dict(t)
                                tt["_host"], tt["_port"] = host, port
                                dev_id, zone, n = resolve_zone(tt, self.conn)
                                resolved.append((t, OrgbSink(self, t, dev_id, zone, n)))
                        except Exception as e:
                            bad.append(f"{t['device_name'][:18]}: {e}")
                    if not resolved and bad:
                        state["error"] = "; ".join(bad)[:200]
                        state["status"] = f"{names} | недоступно: {bad[0][:70]}"
                        self.stop_flag.wait(2.0)
                        continue
                    if bad:
                        self.log("rgb: часть устройств недоступна: %s", "; ".join(bad))
                        state["status"] = f"RGB работает | {names}"
                        state["error"] = "; ".join(bad)[:200]
                    self._targets = resolved
                ts = self._targets
                # авто-кик GPU: дёрнуть режим туда-обратно один раз,
                # иначе GALAX-подобные висят в 'Direct', но цвет не берут
                if ts and not self._kicked and cfg.get("mode_kick", True):
                    self._kicked = True
                    for t, _ in ts:
                        if is_gpu_like(t["device_name"]):
                            try:
                                kick_device_mode(host, port, t["device_name"])
                                state["status"] = f"режим GPU дёрнут: {t['device_name'][:24]}"
                            except Exception as e:
                                state["status"] = f"кик GPU не удался: {e}"
                # аудио-уровень из общего движка
                try:
                    _, _, level, bass, has_sig = self.audio.snapshot()
                except Exception:
                    level, _, has_sig = 0.0, 0.0, False
                # сглаживание среднего
                avg += (level - avg) * (1.0 - math.exp(-dt / float(cfg.get("avg_s", 1.5))))
                mode = cfg.get("rgb_mode", "mix")
                if mode == "screen" or level < float(cfg.get("abs_floor", 0.004)):
                    target = float(cfg["max_brightness"]) if mode == "screen" else float(cfg["min_brightness"])
                else:
                    ratio = level / max(avg, 1e-4)
                    pulse = (ratio - 1.0 - float(cfg["pulse_threshold"])) * float(cfg["pulse_gain"])
                    pulse = max(0.0, min(1.0, pulse))
                    target = float(cfg["min_brightness"]) + (float(cfg["max_brightness"]) - float(cfg["min_brightness"])) * (pulse ** float(cfg["gamma"]))
                alpha = 1.0 - math.exp(-dt / float(cfg["attack_s"] if target >= prev else cfg["release_s"]))
                prev += (target - prev) * alpha
                prev = max(float(cfg["min_brightness"]), min(1.0, prev))
                # heartbeat
                cols = [self._current_base(t, cfg) for t, _ in ts]
                try:
                    idle_fps = max(1, int(cfg.get("idle_fps", 5)))
                except Exception:
                    idle_fps = 5
                try:
                    send_thr = max(0.0, float(cfg.get("send_threshold", 0.004)))
                except Exception:
                    send_thr = 0.004
                now_m = time.monotonic()
                heartbeat_due = (now_m - last_send_t) >= 1.0 / idle_fps
                if last_sig is None:
                    changed = True
                else:
                    lprev, lcols = last_sig
                    changed = (tuple(cols) != lcols or abs(prev - lprev) >= max(send_thr, 1.0 / 255.0))
                paint_color = cols[0] if cols else (197, 13, 241)
                if changed or heartbeat_due:
                    try:
                        for i, (t, sink) in enumerate(ts):
                            final = apply_led_floor(scale_color(cols[i], prev), cfg)
                            sink.paint(final)
                        last_sig = (prev, tuple(cols))
                        last_send_t = now_m
                    except Exception as e:
                        if any(is_ble_target(t) for t, _ in ts) and not need_orgb:
                            self.log("rgb: сбой BLE-цели: %s", e)
                            state["status"] = "BLE-лента: переподключение..."
                            self._targets = None
                            self._sig = None
                            last_sig = None
                            self.stop_flag.wait(1.0)
                            continue
                        try:
                            self.conn.close()
                        except Exception:
                            pass
                        self.conn = None
                        self._targets = None
                        self._sig = None
                        last_sig = None
                        state["status"] = "переподключение к OpenRGB..."
                        self.stop_flag.wait(1.0)
                        continue
                state["rgb_level"] = level
                state["rgb_brightness"] = prev
                state["rgb"] = apply_led_floor(scale_color(paint_color, prev), cfg)
                state["rgb_has_signal"] = has_sig
                next_t += dt
                delay = next_t - time.perf_counter()
                if delay > 0:
                    self.stop_flag.wait(delay)
                else:
                    next_t = time.perf_counter()
        except Exception as e:
            state["error"] = str(e)
            state["status"] = f"ошибка RGB: {e}"
        finally:
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:
                pass
            for _t, sink in (self._targets or []):
                try:
                    sink.close()
                except Exception:
                    pass
            self._targets = None
            self.stop_flag.set()
            state["running_rgb"] = False
            state["status"] = "RGB остановлено"
