from types import SimpleNamespace

from core import logs as L
from core import scenes
from core import status as ST

LOG = """2026-11-07 14:00:00,001 INFO start
2026-11-07 14:00:01,002 WARNING лента не отвечает
2026-11-07 14:00:02,003 WARNING лента не отвечает
2026-11-07 14:00:03,004 ERROR UNCAUGHT: Traceback (most recent call last):
  File "x.py", line 1, in <module>
ValueError: boom
2026-11-07 14:00:04,005 INFO done
"""


def test_read_logs_groups_and_filters(tmp_path, monkeypatch):
    p = tmp_path / "aurafusion.log"
    p.write_text(LOG, encoding="utf-8")
    monkeypatch.setattr(L, "LOG_PATH", str(p))
    r = L.read_logs("problems", 50, "app")
    assert [e["level"] for e in r["entries"]] == ["ERROR", "WARNING"]  # новые первыми
    assert r["entries"][1]["count"] == 2                               # дубли схлопнуты
    assert "ValueError: boom" in r["entries"][0]["msg"]                # трейсбек приклеен
    assert len(L.read_logs("all", 50, "app")["entries"]) == 4
    assert len(L.read_logs("errors", 50, "app")["entries"]) == 1


def test_rotate_file(tmp_path):
    p = tmp_path / "big.log"
    p.write_bytes(b"x" * 2000)
    L.rotate_file(str(p), 1000)
    assert not p.exists()
    assert (tmp_path / "big.log.1").exists()


def _core(running=True, conn=True, error=None, audio="loopback: x", devices=None):
    eng = SimpleNamespace(conn=object() if conn else None)
    return SimpleNamespace(
        cfg={"devices": devices if devices is not None else
             {"GPU": {"enabled": True}, "Sky": {"enabled": True, "transport": "skydimo"}}},
        rgb_engine=eng, state={"running_rgb": running, "error": error},
        svcd=SimpleNamespace(client_connected=True),
        audio=SimpleNamespace(status=audio, has_signal=True))


def test_component_status_levels():
    s = ST.component_status(_core())
    assert s["openrgb"][0] == "ok"
    assert s["strip"][0] == "ok"
    assert s["audio"][0] == "ok"
    assert ST.component_status(_core(conn=False))["openrgb"][0] == "bad"
    assert ST.component_status(_core(running=False))["openrgb"][0] == "off"
    assert ST.component_status(_core(audio="нет звука: x"))["audio"][0] == "bad"


def test_scenes_never_touch_hardware_keys():
    bad = {"name": "x", "patch": {"devices": {}, "autostart": True, "style": "bars"}}
    clean = scenes.sanitize(bad)
    assert clean["patch"] == {"style": "bars"}
