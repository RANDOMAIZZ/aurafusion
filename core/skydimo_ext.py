"""Пуши цветов на ленту Skydimo через Lua-расширение extension.aurafusion.

Расширение (в %APPDATA%\\com.skydimo.desktop\\plugins\\dev\\extension.aurafusion)
подключается TCP к 127.0.0.1:8766, читает строки-кадры
"r,g,b | ... по 96 светодиодов ...", и красит ленту через ext.set_leds.
"""

import threading
import socket
import time


class ExtensionServer:
    """TCP-сервер 127.0.0.1:8766: отдаёт кадры цветов Lua-расширению Skydimo."""

    def __init__(self, leds=96, port=8766, log=None):
        self.leds = leds
        self.port = port
        self._log = log or (lambda *a: None)
        self._client = None      # сокет текущего расширения
        self._client_lock = threading.Lock()
        self._last_colors = None
        self._last_msg = None        # последнее сообщение целиком (для нового клиента)
        self._client_leds = leds     # сколько светодиодов просит расширение
        self._colors_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="skydimo-ext")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._client_lock:
            c = self._client
        if c is not None:
            try:
                c.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)

    @property
    def client_connected(self):
        with self._client_lock:
            return self._client is not None

    def _dbg(self, what, n, c):
        # отладка: раз в 5 секунд показываем, что отправка жива
        now = time.monotonic()
        if now - getattr(self, "_dbg_t", 0.0) > 60.0:
            self._dbg_t = now
            self._log("skydimo-ext: %s %s, client=%s"
                      % (what, n, "да" if c is not None else "нет"))

    def _emit(self, msg):
        with self._colors_lock:
            self._last_msg = msg
        with self._client_lock:
            c = self._client
        if c is not None:
            self._send(c, msg)
        return c

    def push(self, rgb_list):
        """Режим «красим сами»: кадр 'F' + leds*3 байт."""
        if not rgb_list:
            return
        with self._colors_lock:
            self._last_colors = list(rgb_list)
        c = self._emit(b"F" + self._make_frame(rgb_list))
        self._dbg("push leds", len(rgb_list), c)

    def push_brightness(self, level01):
        """Режим «цвет считает сам Skydimo»: 'B' + яркость 0..100.

        Цвет по экрану (адресный, по краям) берёт ScreenMirror самой
        программы, а AuraFusion управляет только яркостью — тёмной или
        яркой в такт музыке."""
        try:
            v = int(round(max(0.0, min(1.0, float(level01))) * 100))
        except (TypeError, ValueError):
            return
        c = self._emit(b"B" + bytes([v]))
        self._dbg("brightness", v, c)

    def _make_frame(self, rgb_list):
        """Двоичный кадр: ровно client_leds*3 байт, недостающее — чёрным."""
        n = int(self._client_leds)
        out = bytearray(n * 3)
        for i, (r, g, b) in enumerate(rgb_list[:n]):
            out[i * 3] = max(0, min(255, int(r)))
            out[i * 3 + 1] = max(0, min(255, int(g)))
            out[i * 3 + 2] = max(0, min(255, int(b)))
        return bytes(out)

    def _send(self, sock, frame):
        try:
            sock.sendall(frame)
        except OSError:
            # клиент оборвался
            with self._client_lock:
                if self._client is sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    self._client = None
            self._log("skydimo-ext: руку включил равно клиент оборван")

    def _run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        host, port = "127.0.0.1", self.port
        bound = False
        for _ in range(10):
            try:
                srv.bind((host, port))
                srv.listen(2)
                bound = True
                break
            except OSError as e:
                self._log("skydimo-ext: %s (port %d занят, повтор)" % (e, port))
                if self._stop.wait(2.0):
                    return
        if not bound:
            self._log("skydimo-ext: не смог занять порт %d, отдаюсь" % port)
            return
        self._log("skydimo-ext: слушаю tcp://127.0.0.1:%d" % port)

        srv.settimeout(1.0)
        while not self._stop.is_set():
            try:
                c, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # рукопожатие: расширение сообщает число светодиодов (2 байта LE)
            try:
                c.settimeout(3.0)
                hs = b""
                while len(hs) < 2:
                    chunk = c.recv(2 - len(hs))
                    if not chunk:
                        raise OSError("closed")
                    hs += chunk
                n = int.from_bytes(hs, "little")
                if 0 < n <= 4096:
                    self._client_leds = n
                c.settimeout(None)
            except OSError as e:
                self._log("skydimo-ext: нет рукопожатия от %s: %s" % (addr, e))
                try:
                    c.close()
                except OSError:
                    pass
                continue
            self._log("skydimo-ext: клиент подключен %s, светодиодов: %d"
                      % (addr, self._client_leds))
            # Зд.getServer приветствие — Lua-сторона сразу в цикле чтения
            with self._client_lock:
                old = self._client
                self._client = c
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            # если уже есть накопленный кадр — отдаём сразу
            with self._colors_lock:
                last = self._last_msg
            if last:
                self._send(c, last)

            # сторожевой поток клиента: при разрыве закроем
            def watchdog(sock=c):
                try:
                    sock.settimeout(None)
                    # read блокируется до разрыва — сигнал того, что клиент умер
                    while True:
                        chunk = sock.recv(256)
                        if not chunk:
                            break
                except OSError:
                    pass
                finally:
                    with self._client_lock:
                        if self._client is sock:
                            self._client = None
                    self._log("skydimo-ext: клиент отвалился")
                    try:
                        sock.close()
                    except OSError:
                        pass

            threading.Thread(target=watchdog, daemon=True).start()

        try:
            srv.close()
        except OSError:
            pass
