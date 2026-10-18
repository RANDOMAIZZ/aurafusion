"""Проверка, что веб-панель не вешает приложение.

Раньше кнопки «Скрыть оверлей» и смена настроек вызывали Qt-виджет прямо из
потока HTTP-обработчика, а окном владеет главный поток Qt. Приложение
зависало целиком. Теперь вызовы идут через GuiBridge.

Проверяем: серия переключений оверлея, смена настроек с побочными
эффектами, и что сервер продолжает отвечать после каждого.

    python tools/test_web_actions.py [адрес]
"""
import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
TIMEOUT = 6.0
fails = []


def call(path, payload=None):
    """Запрос с замером. Возвращает (секунды, ответ) либо бросает."""
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = json.loads(r.read().decode("utf-8"))
    return time.time() - t0, body


def state():
    _, s = call("/api/state")
    return s


def step(label, fn):
    try:
        dt, res = fn()
        print("  %-46s %6.0f мс  %s" % (label, dt * 1000, "ok" if res.get("ok") else res))
        return True
    except Exception as e:
        print("  %-46s  ЗАВИС/ОШИБКА: %s" % (label, e))
        fails.append(label)
        return False


def main():
    print("адрес:", BASE)
    try:
        s = state()
    except Exception as e:
        print("сервер не отвечает:", e)
        return 1
    print("оверлей виден:", s.get("overlay_visible"), "| статус:", s.get("status"))
    print()

    print("переключение оверлея 10 раз:")
    vis = s.get("overlay_visible")
    for i in range(10):
        if not step("скрыть" if vis else "показать", lambda: call(
                "/api/action", {"name": "toggle_overlay"})):
            break
        vis = not vis
        time.sleep(0.2)

    print()
    print("настройки с побочными эффектами (бьют по Qt из HTTP-потока):")
    _, cfg = call("/api/config")
    c = cfg["config"]
    step("overlay_fps", lambda: call("/api/set", {"patch": {"overlay_fps": c.get("overlay_fps", 60)}}))
    step("overlay_opacity", lambda: call("/api/set", {"patch": {"overlay_opacity": c.get("overlay_opacity", 1.0)}}))
    step("monitor", lambda: call("/api/set", {"patch": {"monitor": c.get("monitor", 0)}}))
    step("click_through", lambda: call("/api/set", {"patch": {"click_through": c.get("click_through", True)}}))
    step("height_scale", lambda: call("/api/set", {"patch": {"height_scale": c.get("height_scale", 0.55)}}))

    print()
    print("после серии:")
    ok = step("состояние", lambda: (0.0, {"ok": True}) if state() else (0.0, {"ok": True}))
    if ok:
        s2 = state()
        print("  сервер жив, оверлей виден:", s2.get("overlay_visible"))

    print()
    if fails:
        print("ПРОВАЛЕНО шагов:", len(fails), fails)
        return 1
    print("все шаги прошли, зависания нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())