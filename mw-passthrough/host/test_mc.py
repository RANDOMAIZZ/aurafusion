"""test_mc.py - притворяется Minecraft-стороной.
Подключается к fakehost и печатает cam/ground.
Проверка: запусти в 2 окнах (пример путей):
  окно1: fakehost_mw.py 20
  окно2: test_mc.py 15
"""
import sys, json, time
try:
    from websockets.sync.client import connect
except ImportError:
    print("нужен pip install websockets")
    raise SystemExit(1)

secs = int(sys.argv[1]) if len(sys.argv) > 1 else 15
print(f"MC-тест цепляется к ws://127.0.0.1:25599 ({secs}c)...")
t0 = time.time()
n_cam = n_gnd = 0
try:
    with connect("ws://127.0.0.1:25599") as ws:
        print("подключено, жду cam+ground...")
        ws.send("{}")  # будим серверный _loop
        while time.time() - t0 < secs:
            try:
                msg = ws.recv(timeout=2)
            except Exception as e:
                print("recv timeout/обрыв:", e)
                break
            d = json.loads(msg)
            if d.get("t") == "cam":
                n_cam += 1
                if n_cam % 60 == 1:
                    print(f"cam #{n_cam}: pos={d['pos']} yaw={d['yaw']:.1f}")
            elif d.get("t") == "ground":
                n_gnd += 1
                if n_gnd == 1:
                    print(f"ground: cols={len(d['cols'])} yOffset={d.get('yOffset')}")
except Exception as e:
    print("не подключился. Сначала запусти fakehost_mw.py в другом окне. Ошибка:", e)
    raise SystemExit(1)
print(f"итог: cam={n_cam} ground={n_gnd}")
print("OK - линк двусторонний" if n_cam > 10 else "мало cam - что-то не так")
