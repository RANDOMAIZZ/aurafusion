"""BLE RGB-лента как устройство подсветки AuraFusion.

Контроллеры семейства ELK-BLEDOM / ELK-BLEDDM / LED LIGHT STRIP / XSL-
(приложение wl.smartled "宝莲灯", LotusLantern). Транспорт — GATT, а не
OpenRGB, поэтому лента живёт в общем конвейере RGBEngine через BleTarget:
тот же цвет с экрана, тот же пульс яркости от музыки, те же настройки.

Кадр ровно 9 байт: 7E LEN CMD P1 P2 P3 P4 P5 EF
Сервис FFF0, запись в FFF3 (с подтверждением), notify FFF4.
"""
import asyncio
import threading
import time

SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_CH = "0000fff3-0000-1000-8000-00805f9b34fb"
NOTIFY_CH = "0000fff4-0000-1000-8000-00805f9b34fb"

NAME_PREFIXES = ("ELK-", "ELK~", "LED LIGHT STRIP", "XSL-")


def frame(len_marker, cmd, p1=0xFF, p2=0xFF, p3=0xFF, p4=0xFF, p5=0xFF):
    return bytes([0x7E, len_marker, cmd, p1, p2, p3, p4, p5, 0xEF])


def cmd_power(on):
    v = 1 if on else 0
    return frame(0x04, 0x04, v, 0x00, v, 0xFF, 0x00)


def cmd_brightness(level, light_mode=0):
    return frame(0x04, 0x01, max(0, min(255, int(level))), light_mode, 0xFF, 0xFF, 0x00)


def cmd_color(r, g, b):
    return frame(0x07, 0x05, 0x03,
                 max(0, min(255, int(r))), max(0, min(255, int(g))),
                 max(0, min(255, int(b))), 0x10)


def cmd_mode(m):
    """Встроенный динамический режим; контроллер ждёт старший бит (0x80..0xFF)."""
    return frame(0x05, 0x03, (int(m) + 0x80) & 0xFF, 0x03, 0xFF, 0xFF, 0x00)


def cmd_mode_speed(speed):
    return frame(0x04, 0x02, max(0, min(255, int(speed))), 0xFF, 0xFF, 0xFF, 0x00)


class BleStrip:
    """Один контроллер: свой asyncio-цикл в фоновом потоке + писатель.

    paint() можно звать хоть каждый кадр движка (60 fps) — кадры
    склеиваются, наружу уходит не чаще max_fps и всегда самый свежий.
    """

    PROBE_S = 2.5   # как часто кадр цвета уходит с подтверждением (пинг связи)

    def __init__(self, addr, max_fps=20, log=None):
        self.addr = addr.upper()
        self.name = ""
        self.connected = False
        self.error = None
        self._log = log or (lambda *a: None)
        self.max_fps = max(1.0, float(max_fps))
        # последнее написанное в лог и когда. Лента, которую телефон занял или
        # которая выключена, сыпет одну и ту же ошибку каждые полторы секунды
        # часами: за вечер лог на 200 КБ, и в нём не видно ничего, кроме этой
        # мухи. Пишем первый раз, потом не чаще раза в минуту.
        self._last_log = ""
        self._last_log_at = 0.0

    def _log_once(self, msg, every=60.0):
        """Написать в лог, если такого сообщения ещё не было или оно старое."""
        now = time.monotonic()
        if msg == self._last_log and now - self._last_log_at < every:
            return
        self._last_log = msg
        self._last_log_at = now
        self._log(msg)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop_thread,
                                        name=f"ble-loop-{self.addr}", daemon=True)
        self._thread.start()

        self._client = None
        self._lock = threading.Lock()
        self._rgb = None          # последний желаемый цвет
        self._bright = 255        # последняя заданная яркость 0..255
        self._sent_bright = None
        self._last_probe = 0.0
        self._powered = False
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._writer_thread,
                                        name=f"ble-rgb-{self.addr}", daemon=True)
        self._writer.start()

    # ---------- жизненный цикл ----------
    def _loop_thread(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout=8.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self._dirty.set()
        try:
            self._run(self._teardown(), timeout=3.0)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        self._thread.join(timeout=2.0)

    async def _teardown(self):
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass
        self.connected = False

    # ---------- API для движка ----------
    def paint(self, rgb):
        """Залить сплошным цветом. rgb = (r, g, b), 0..255.

        Яркости здесь нет как отдельного кадра: как и у зон OpenRGB, её несёт
        сам цвет (движок домножает base-цвет на текущий 'prev'). Иначе каждый
        кадр пульса требовал бы команды яркости, а она на клонах медленная.
        Чёрный — такой же честный цвет, как и для остальных устройств."""
        if rgb is None:
            return
        self._powered = True
        with self._lock:
            self._rgb = tuple(int(v) for v in rgb)
        self._dirty.set()

    def set_hardware_brightness(self, level):
        """Железная яркость 0..1 — редкая настройка, шлётся с подтверждением.
        Обычно 1.0 (255): дальше яркостью управляет цвет."""
        lv = max(0, min(255, int(round(float(level) * 255))))
        with self._lock:
            if lv != self._bright:
                self._bright = lv
                self._dirty.set()

    def off(self):
        self._powered = False
        with self._lock:
            self._rgb = (0, 0, 0)
        self._dirty.set()

    # ---------- писатель ----------
    def _writer_thread(self):
        last = 0.0
        while not self._stop.is_set():
            if not self._dirty.wait(timeout=1.0):
                self._dirty.clear()
                continue
            self._dirty.clear()
            gap = 1.0 / self.max_fps
            dt = time.monotonic() - last
            if dt < gap and self._stop.wait(gap - dt):
                break
            if self._stop.is_set():
                break
            with self._lock:
                rgb, bright, powered = self._rgb, self._bright, self._powered
            if rgb is None:
                continue
            try:
                self._run(self._send(rgb, bright, powered))
                self.connected = True
                self.error = None
                last = time.monotonic()
            except Exception as e:  # noqa: BLE001 — клионы рвут GATT сами
                self.connected = False
                self.error = str(e)
                self._log_once(f"[ble] {self.addr} сбой: {e}")
                try:
                    self._run(self._teardown(), timeout=3.0)
                except Exception:
                    pass
                if self._stop.wait(1.5):
                    break

    # ---------- GATT ----------
    async def _ensure(self):
        if self._client is not None and self._client.is_connected:
            return
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        from bleak import BleakClient, BleakScanner
        dev = await BleakScanner.find_device_by_address(self.addr, timeout=6.0)
        if dev is None:
            raise RuntimeError(f"{self.addr} не отвечает (лента выключена или занята телефоном)")
        self.name = dev.name or ""
        self._client = BleakClient(dev)
        await asyncio.wait_for(self._client.connect(), timeout=8.0)
        self.connected = True
        self._sent_bright = None
        self._powered = False
        self._log(f"[ble] подключено {self.addr} ({self.name}) rssi")

    async def _send(self, rgb, bright, powered):
        await self._ensure()
        c = self._client
        if not powered:
            await c.write_gatt_char(WRITE_CH, cmd_power(False), response=True)
            self._powered = False
            self._sent_bright = None
            return
        if not self._powered:
            await c.write_gatt_char(WRITE_CH, cmd_power(True), response=True)
            self._powered = True
        if bright != self._sent_bright:
            await c.write_gatt_char(WRITE_CH, cmd_brightness(bright), response=True)
            self._sent_bright = bright
        # Кадр цвета уходит без подтверждения: на клонах это ~1 мс вместо
        # ~130 мс (замер), то есть 15+ fps вместо 7. Раз в PROBE_S шлём тот же
        # кадр с подтверждением — это дешёвый пинг, по нему ловим мёртвый GATT.
        now = time.monotonic()
        probe = (now - self._last_probe) >= self.PROBE_S
        await c.write_gatt_char(WRITE_CH, cmd_color(*rgb), response=probe)
        if probe:
            self._last_probe = now

    async def status(self):
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("не подключено")
        return await self._client.read_gatt_char(NOTIFY_CH)


# ---------- разделяемое состояние на один адрес ----------
_registry = {}
_reg_lock = threading.Lock()


def get_strip(addr, max_fps=20, log=None):
    """Один BleStrip на BLE-адрес на весь процесс (лента держит одно соединение)."""
    addr = (addr or "").upper()
    if not addr:
        return None
    with _reg_lock:
        s = _registry.get(addr)
        if s is None:
            s = BleStrip(addr, max_fps=max_fps, log=log)
            _registry[addr] = s
    s.max_fps = max(1.0, float(max_fps))
    return s


def close_all():
    with _reg_lock:
        items = list(_registry.values())
        _registry.clear()
    for s in items:
        try:
            s.close()
        except Exception:
            pass


def _scan_sync(timeout):
    from bleak import BleakScanner

    async def run():
        found = {}

        def cb(dev, adv):
            nm = adv.local_name or dev.name or ""
            if nm.startswith(NAME_PREFIXES):
                found[dev.address.upper()] = nm

        sc = BleakScanner(detection_callback=cb)
        await sc.start()
        await asyncio.sleep(timeout)
        await sc.stop()
        return found

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(run())
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def scan(timeout=10.0):
    """Список лент рядом: {адрес: имя}. Должен вызываться не из GUI-потока."""
    return _scan_sync(float(timeout))