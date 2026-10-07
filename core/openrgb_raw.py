import socket
import struct
import time

MAGIC = b"ORGB"
PKT_RESIZEZONE = 1000
PKT_UPDATELEDS = 1050
PKT_UPDATEZONELEDS = 1051
PKT_SETCUSTOMMODE = 1100


def server_reachable(host="127.0.0.1", port=6742, timeout=1.0):
    s = None
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def wait_for_server(host="127.0.0.1", port=6742, timeout=90.0, step=2.0):
    end = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < end:
        if server_reachable(host, port, timeout=1.0):
            return True
        time.sleep(step)
    return server_reachable(host, port, timeout=1.0)


class OpenRGBRaw:
    def __init__(self, host="127.0.0.1", port=6742):
        self.sock = socket.create_connection((host, port), timeout=5)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        self.sock.settimeout(5)

    def send(self, dev_id, pkt_id, payload=b"", expect_reply=True):
        header = struct.pack("<4sIII", MAGIC, dev_id, pkt_id, len(payload))
        self.sock.sendall(header + payload)
        if not expect_reply:
            return None
        hdr = self._recv_exact(16)
        magic, rdev, rpid, rsize = struct.unpack("<4sIII", hdr)
        assert magic == MAGIC
        data = self._recv_exact(rsize) if rsize else b""
        return rdev, rpid, data

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def resize_zone(self, dev_id, zone_idx, new_size):
        payload = struct.pack("<II", zone_idx, new_size)
        return self.send(dev_id, PKT_RESIZEZONE, payload, expect_reply=False)

    def update_zone_leds(self, dev_id, zone_idx, colors):
        body = struct.pack("<I", zone_idx) + struct.pack("<H", len(colors)) + b"".join(
            struct.pack("<BBBx", r, g, b) for r, g, b in colors
        )
        payload = struct.pack("<I", len(body) + 4) + body
        return self.send(dev_id, PKT_UPDATEZONELEDS, payload, expect_reply=False)


def rgb_to_hex(c):
    return "#{:02X}{:02X}{:02X}".format(c[0], c[1], c[2])
