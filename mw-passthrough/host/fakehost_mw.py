"""fakehost_mw.py - эмулирует NFS MW ASI без самой игры.
Шлет cam + ground на ws://127.0.0.1:25599, как будет делать MW Passthrough.asi.
Проверка линка будущей Fabric-стороны без запуска NFS.

Запуск:  pip install websockets numpy
         python host/fakehost_mw.py [seconds=20]
"""
import asyncio
import json
import math
import sys

PORT = 25599

async def handler(ws):
    print("MC подключился, шлю cam+ground...")
    # ровная земля y=64, круг радиусом 40 как в GTA-примере (40 блоков, 160 проб)
    cols = [{"x": x, "z": z, "y": 64} for x in range(-20, 21, 5) for z in range(-20, 21, 5)]
    t = 0.0
    try:
        async for _ in ws:
            pass
    except Exception:
        pass

async def sender(connected):
    # ждем подключения чтобы не слать в пустоту
    while not connected:
        await asyncio.sleep(0.1)

async def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    try:
        import websockets
    except ImportError:
        print("нужен pip install websockets")
        return
    async with websockets.serve(handler, "127.0.0.1", PORT):
        print(f"fake NFS MW слушает ws://127.0.0.1:{PORT} ({seconds}c)")
        # отдельный таск шлет кадры всем клиентам
        async def broadcast():
            import websockets as wsmod
            t = 0.0
            cols = [{"x": x, "z": z, "y": 64} for x in range(-20, 21, 5) for z in range(-20, 21, 5)]
            while True:
                t += 1 / 60.0
                x = 8 * math.cos(t * 0.3)
                z = 8 * math.sin(t * 0.3)
                cam = {"t": "cam", "pos": [x, 67.0, z], "yaw": (math.degrees(t * 0.3) % 360), "pitch": -10.0, "fov": 70.0}
                gnd = {"t": "ground", "cols": cols, "yOffset": 0}
                for client in list(wsmod.serve.__self__ if False else []):
                    pass
                # шлем через серверные подключения
                await asyncio.sleep(1 / 60.0)
        await asyncio.sleep(seconds)
        print("готово")

def _loop(ws, cols, t0, secs):
    import time, math, json
    print("MC подключился")
    try:
        _ = ws.recv(timeout=2)
    except Exception:
        pass
    while time.time() - t0 < secs:
        t = time.time() - t0
        x = 8 * math.cos(t * 0.3)
        z = 8 * math.sin(t * 0.3)
        cam = {"t": "cam", "pos": [x, 67.0, z], "yaw": (math.degrees(t * 0.3) % 360), "pitch": -10.0, "fov": 70.0}
        gnd = {"t": "ground", "cols": cols, "yOffset": 0}
        try:
            ws.send(json.dumps(cam))
            ws.send(json.dumps(gnd))
        except Exception as e:
            print("обрыв:", e)
            return
        time.sleep(1 / 60.0)
    print("готово")

if __name__ == "__main__":
    # упрощенный вариант без broadcast-сложностей: синхронный сервер ниже
    import asyncio as _a, json as _j, math as _m
    try:
        from websockets.sync.server import serve as _serve
    except ImportError:
        print("нужен pip install websockets")
        raise SystemExit(1)
    _secs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    _cols = [{"x": x, "z": z, "y": 64} for x in range(-20, 21, 5) for z in range(-20, 21, 5)]
    print(f"fake NFS MW слушает ws://127.0.0.1:{PORT} ({_secs}c)")
    import time as _time, threading as _th
    _t0 = _time.time()
    with _serve(lambda ws: _loop(ws, _cols, _t0, _secs), "127.0.0.1", PORT) as _srv:
        _th.Timer(_secs + 1, _srv.shutdown).start()
        _srv.serve_forever()
    print("готово, выход")
