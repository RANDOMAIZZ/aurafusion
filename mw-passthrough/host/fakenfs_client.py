"""fakenfs_client.py - притворяется NFS MW ASI (как будет MWPassthrough.asi).
Подключается К МОДУ (мод = сервер на 127.0.0.1:25599) и шлет cam+ground 60 FPS.
Мир в игре должен быть ОТКРЫТ (не меню), иначе мод игнорит пакеты.
"""
import sys, json, time, math
try:
    from websockets.sync.client import connect
except ImportError:
    print("нужен pip install websockets")
    raise SystemExit(1)

secs = int(sys.argv[1]) if len(sys.argv) > 1 else 12
cols = [{"x": x, "z": z, "y": 64} for x in range(-20, 21, 5) for z in range(-20, 21, 5)]
print(f"NFS-клиент шлет cam+ground {secs}c...")
t0 = time.time()
n = 0
try:
    with connect("ws://127.0.0.1:25599") as ws:
        print("подключено к моду!")
        while time.time() - t0 < secs:
            t = time.time() - t0
            x = 8 * math.cos(t * 0.3)
            z = 8 * math.sin(t * 0.3)
            ws.send(json.dumps({"t": "cam", "pos": [x, 67.0, z], "yaw": (math.degrees(t * 0.3) % 360), "pitch": -10.0, "fov": 70.0}))
            ws.send(json.dumps({"t": "ground", "cols": cols, "yOffset": 0}))
            n += 1
            time.sleep(1 / 60.0)
except Exception as e:
    print("не подключился (мод/игра запущены?):", e)
    raise SystemExit(1)
print(f"отправлено кадров: {n} - смотри barrier-пол в игре вокруг спавна")
