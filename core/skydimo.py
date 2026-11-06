"""Klient WebSocket API oficial'noy programma Skydimo SK0L27.

Podklyuchaetsya k WebSocket serveru oficial'noy provody (port 55489 / 61393).
Soedinenie ustanavlivaetsya po odnoj na komandu set-led (server zaykryvaet
soedinenie posle obrabotki komandy), po etomu kazdyj vyзов set_colors
otkryvaet novoe soedinenie.
"""

import base64
import json
import os
import socket
import threading
import time


def _ws_frame(payload: bytes) -> bytes:
    n = len(payload)
    if n <= 125:
        return bytes([0x81, n]) + payload
    elif n <= 65535:
        return bytes([0x81, 0x7E]) + n.to_bytes(2, 'big') + payload
    else:
        return bytes([0x81, 0x7F]) + n.to_bytes(8, 'big') + payload


def _parse_ws_events(data: bytes):
    """Izvlekat' cvetа iz WebSocket tekstovykh freamer."""
    colors = []
    buf = data
    while len(buf) >= 2:
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        if opcode != 0x01:
            break
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        idx = 2
        if length == 126:
            if len(buf) < 4:
                break
            length = int.from_bytes(buf[2:4], 'big')
            idx = 4
        elif length == 127:
            if len(buf) < 10:
                break
            length = int.from_bytes(buf[2:10], 'big')
            idx = 10
        if masked:
            if len(buf) < idx + 4:
                break
            mask = buf[idx:idx + 4]
            idx += 4
        if len(buf) < idx + length:
            break
        payload = buf[idx:idx + length]
        if masked:
            payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
        buf = buf[idx + length:]
        try:
            msg = json.loads(payload.decode('utf-8'))
            if msg.get("method") == "event":
                d = msg.get("params", {}).get("data", {})
                rgb_b64 = d.get("rgb", "")
                if rgb_b64:
                    raw = base64.b64decode(rgb_b64)
                    for i in range(0, len(raw) - 2, 3):
                        colors.append((raw[i], raw[i + 1], raw[i + 2]))
        except Exception:
            pass
    return colors


class SkydimoClient:
    """Klient Skydimo SK0L27 cherez WebSocket oficial'noy provody.

    Kazdyj vyзов set_colors otkryvaet sobstvennoe WebSocket-soedinenie,
    posylaja set-led i chitaia otvet.
    """

    PORTS = [55489, 61393]

    def __init__(self, port=None, leds=96, log=None, com_port="COM4"):
        self.port = port
        self.leds = leds
        self.com_port = com_port or "COM4"
        # Отправка уходит в отдельный поток: set_colors() зовётся из цикла
        # RGBEngine, а сетевой обмен (recv до 3 с, поиск порта через
        # PowerShell до 15 с) подвешивал подсветку на ВСЕХ устройствах.
        self._pending = None
        self._pending_lock = threading.Lock()
        self._pending_evt = threading.Event()
        self._send_thread = None
        self._next_discover = 0.0
        self._log = log or (lambda *a: None)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._colors = [(0, 0, 0)] * leds
        self._colors_lock = threading.Lock()
        self._connected = False
        self._connected_lock = threading.Lock()
        self._actual_port = None

    @property
    def connected(self):
        with self._connected_lock:
            return self._connected

    @property
    def colors(self):
        with self._colors_lock:
            return list(self._colors)

    @property
    def actual_port(self):
        return self._actual_port

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="skydimo-ws")
        self._thread.start()
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True,
                                             name="skydimo-send")
        self._send_thread.start()

    def stop(self):
        self._stop.set()
        self._pending_evt.set()
        for t in (self._thread, self._send_thread):
            if t:
                t.join(timeout=5.0)

    def _send_loop(self):
        """Шлёт только самый свежий кадр: устаревшие пропускаются."""
        while not self._stop.is_set():
            self._pending_evt.wait()
            self._pending_evt.clear()
            if self._stop.is_set():
                break
            with self._pending_lock:
                job, self._pending = self._pending, None
            if job is None:
                continue
            try:
                self._send_once(*job)
            except Exception as e:
                self._log("skydimo: сбой отправки: %s" % e)

    def set_colors(self, rgb_list):
        """Ustanovit' cvet na vsekh svetodiodakh. Odno soedinenie na komandu."""
        if not rgb_list:
            return
        data = bytes()
        for r, g, b in rgb_list[: self.leds]:
            data += bytes([max(0, min(255, int(r))),
                           max(0, min(255, int(g))),
                           max(0, min(255, int(b)))])
        while len(data) < self.leds * 3:
            data += bytes([0, 0, 0])
        b64 = base64.b64encode(data).decode()
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "set-led",
            "params": {"count": self.leds, "port": self.com_port, "rgb": b64},
            "id": int(time.time() * 1000) % 1000000,
        })
        with self._pending_lock:
            self._pending = (msg, data)
        self._pending_evt.set()

    def _send_once(self, msg: str, raw_data: bytes):
        ports = [self.port] if self.port else list(self.PORTS)
        for port in ports:
            if self._stop.is_set():
                break
            try:
                self._try_port(port, msg, raw_data)
                return  # uspesno
            except Exception:
                # sterlyy port — kandidaty peryezhivayut, log ne nujен
                pass
        # izvestnye porty ne otlichilis' — port official'noy provody menyaetsya
        # pri kazdom zapuske, ishem ego po spisku portov processa skydimo-core
        # PowerShell тяжёлый — не чаще раза в минуту
        found = None
        if time.monotonic() >= self._next_discover:
            self._next_discover = time.monotonic() + 60.0
            found = self._discover_port()
        if found:
            self._log("skydimo: nayden port %d (avtoobnaryzhenie)" % found)
            try:
                self._try_port(found, msg, raw_data)
                return
            except Exception:
                pass
        self._set_connected(False)
        # все porty ne otlichilis' — odin log, ne spammим
        self._log("skydimo: set-led ne poslán (server zanyat)")

    def _discover_port(self):
        """Nayti WebSocket-port skydimo-core per Counterom eго портов подслушnения."""
        import subprocess
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue "
                 "| ForEach-Object { $pr = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
                 "if ($pr -and $pr.ProcessName -like 'skydimo*') { $_.LocalPort } }"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000)
            ports = []
            for l in r.stdout.split():
                l = l.strip()
                if l.isdigit():
                    p = int(l)
                    if p not in ports:
                        ports.append(p)
        except Exception:
            return None
        for p in ports:
            try:
                self._ws_probe(p)
                return p
            except Exception:
                continue
        return None

    def _ws_probe(self, port):
        """Proverit', chto port — WebSocket JSON-RPC server skydimo."""
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect(("127.0.0.1", port))
            req = (
                'GET / HTTP/1.1\r\n'
                'Host: 127.0.0.1:%d\r\n'
                'Upgrade: websocket\r\n'
                'Connection: Upgrade\r\n'
                'Sec-WebSocket-Key: %s\r\n'
                'Sec-WebSocket-Version: 13\r\n'
                '\r\n' % (port, key)
            ).encode('ascii')
            sock.sendall(req)
            resp = sock.recv(512)
            if b'101 Switching Protocols' not in resp:
                raise RuntimeError("ne WebSocket")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _try_port(self, port: int, msg: str, raw_data: bytes):
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(("127.0.0.1", port))
            req = (
                'GET / HTTP/1.1\r\n'
                'Host: 127.0.0.1:%d\r\n'
                'Upgrade: websocket\r\n'
                'Connection: Upgrade\r\n'
                'Sec-WebSocket-Key: %s\r\n'
                'Sec-WebSocket-Version: 13\r\n'
                '\r\n' % (port, key)
            ).encode('ascii')
            sock.sendall(req)
            resp = sock.recv(65536)
            if b'101 Switching Protocols' not in resp:
                raise RuntimeError("ne WebSocket: %r" % resp[:100])

            # privetstvennoe sobytie s tekushimi cvetami
            try:
                sock.settimeout(1.0)
                extra = sock.recv(65536)
                colors = _parse_ws_events(extra)
                if colors:
                    with self._colors_lock:
                        self._colors = colors[: self.leds]
            except socket.timeout:
                pass

            # otpravljaem set-led
            sock.settimeout(5.0)
            sock.sendall(_ws_frame(msg.encode('utf-8')))
            self._log("skydimo: set-led poslán (%d bajtov, port %d)" % (len(raw_data), port))

            # chitaem otvet s aktualnymi cvetami
            try:
                sock.settimeout(3.0)
                resp2 = sock.recv(65536)
                colors = _parse_ws_events(resp2)
                if colors:
                    with self._colors_lock:
                        self._colors = colors[: self.leds]
            except socket.timeout:
                pass

            self._set_connected(True)
            self._actual_port = port
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _run(self):
        # fondovyj potok: periodicheskoe obnovenie cvetov s lentы
        # obnovlenie kazhdyh 5 sekund; tipichnyy PCC portov (55489, 61393,
        # i td) — tycho, bez loga, chtoby ne spammirovat' faл log.
        last_fail_logged = False
        while not self._stop.is_set():
            # pervym probuyem port, naidennyy pri otpravke komand
            ports = []
            if self.port:
                ports.append(self.port)
            with self._connected_lock:
                act = self._actual_port
            if act and act not in ports:
                ports.append(act)
            ports.extend(p for p in self.PORTS if p not in ports)
            ok = False
            for p in ports:
                if self._stop.is_set():
                    break
                try:
                    self._refresh_colors(p)
                    ok = True
                    break  # port rabochiy — dostatochno
                except Exception:
                    # sterlye porty oficial'noy provody (pereskatka) —
                    # ne logiruyem, chtoby ne spammirovat'
                    pass
            if ok:
                self._set_connected(True)
                if last_fail_logged:
                    last_fail_logged = False  # vosstanovlenie — odin log
                    self._log("skydimo: soedinenie vosstanovleno (port %s)" % self._actual_port)
            else:
                self._set_connected(False)
                if not last_fail_logged:
                    last_fail_logged = True
                    self._log("skydimo: server ne nachalsya, poyhchyuty zhdem (kazhdyе 5 s)...")
            if not self._stop.is_set():
                self._stop.wait(5.0)

    def _refresh_colors(self, port: int):
        """Prochitat' tekushie cvetа s lentы cherez WebSocket."""
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(("127.0.0.1", port))
            req = (
                'GET / HTTP/1.1\r\n'
                'Host: 127.0.0.1:%d\r\n'
                'Upgrade: websocket\r\n'
                'Connection: Upgrade\r\n'
                'Sec-WebSocket-Key: %s\r\n'
                'Sec-WebSocket-Version: 13\r\n'
                '\r\n' % (port, key)
            ).encode('ascii')
            sock.sendall(req)
            resp = sock.recv(65536)
            if b'101 Switching Protocols' not in resp:
                raise RuntimeError("ne WebSocket: %r" % resp[:100])

            try:
                sock.settimeout(1.0)
                extra = sock.recv(65536)
                colors = _parse_ws_events(extra)
                if colors:
                    with self._colors_lock:
                        self._colors = colors[: self.leds]
                    self._set_connected(True)
                    self._actual_port = port
                    self._log("skydimo: cvetа s lentы polucheny (port %d)" % port)
            except socket.timeout:
                pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _set_connected(self, val):
        with self._connected_lock:
            self._connected = val
