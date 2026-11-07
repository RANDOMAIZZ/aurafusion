"""Состояние компонентов (OpenRGB, лента, звук) — для значка в трее и панели."""


def _ble_state():
    """Что сейчас с каждой BLE-лентой: адрес -> подключена ли и что за ошибка."""
    out = {}
    try:
        from core import ble_strip
        for addr, st in list(getattr(ble_strip, "_registry", {}).items()):
            out[addr] = {"connected": bool(getattr(st, "connected", False)),
                         "error": str(getattr(st, "error", "") or "")}
    except Exception:
        pass
    return out


def _ble_for(cfg_devs, name, ble_state):
    """Состояние ленты для конкретного устройства из списка."""
    info = cfg_devs.get(name) or {}
    addr = str(info.get("addr", "") or info.get("address", "")).upper()
    st = ble_state.get(addr) or {}
    return {"addr": addr,
            "connected": bool(st.get("connected")),
            "error": st.get("error", ""),
            # лента не трогает GATT, пока нечего посылать, поэтому «не
            # подключена» при пустом цене — это норма, а не поломка
            "idle": not addr}


def component_status(core):
    """Состояние трёх компонентов: OpenRGB, лента, звук.

    Возвращает {"openrgb": (уровень, текст), "strip": ..., "audio": ...},
    уровень — "ok" / "warn" / "bad" / "off". Читаются только простые
    атрибуты, поэтому безопасно звать из потока Qt по таймеру."""
    cfg = core.cfg
    devs = cfg.get("devices", {}) or {}
    en = {n: d for n, d in devs.items() if isinstance(d, dict) and d.get("enabled", True)}

    # --- OpenRGB ---
    orgb = [n for n, d in en.items() if d.get("transport") not in ("ble", "skydimo")]
    eng = core.rgb_engine
    if not orgb:
        o = ("off", "нет устройств в настройках")
    elif eng is None or not core.state.get("running_rgb"):
        o = ("off", "подсветка остановлена")
    elif eng.conn is None:
        o = ("bad", "сервер OpenRGB недоступен")
    elif core.state.get("error"):
        o = ("warn", "часть устройств недоступна")
    else:
        o = ("ok", "подключён, устройств: %d" % len(orgb))

    # --- лента (BLE и Skydimo) ---
    strips = [(n, d) for n, d in en.items() if d.get("transport") in ("ble", "skydimo")]
    if not strips:
        t = ("off", "ленты не настроены")
    else:
        ble = _ble_state()
        up, total = 0, len(strips)
        for n, d in strips:
            if d.get("transport") == "skydimo":
                svc = core.svcd
                if svc is not None and getattr(svc, "client_connected", False):
                    up += 1
            else:
                addr = str(d.get("addr", "") or "").upper()
                if (ble.get(addr) or {}).get("connected"):
                    up += 1
        if not core.state.get("running_rgb"):
            t = ("off", "подсветка остановлена")
        elif up == total:
            t = ("ok", "подключено: %d из %d" % (up, total))
        elif up:
            t = ("warn", "подключено: %d из %d" % (up, total))
        else:
            t = ("bad", "не подключена")

    # --- звук ---
    ast = str(getattr(core.audio, "status", "") or "")
    low = ast.lower()
    if low.startswith("loopback") or low == "demo":
        a = ("ok", ("демо" if low == "demo" else "захват идёт")
             + (", играет" if getattr(core.audio, "has_signal", False) else ", тихо"))
    elif low in ("init", "") or "переподключ" in low:
        a = ("warn", ast or "запуск")
    else:
        a = ("bad", ast[:60])
    return {"openrgb": o, "strip": t, "audio": a}
