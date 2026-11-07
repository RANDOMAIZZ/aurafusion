from types import SimpleNamespace

from core import rgb_engine as E


def _mode(**kw):
    base = dict(name="Direct", speed=130, speed_min=0, speed_max=0,
                brightness=129, brightness_min=1, brightness_max=3)
    base.update(kw)
    return SimpleNamespace(**base)


def test_fix_mode_clamps_garbage_from_galax():
    # Видеокарта GALAX после загрузки отдаёт speed 130 при диапазоне 0..0 и
    # brightness 129 при 1..3 — без исправления режим не выставлялся вовсе.
    m = E.fix_mode(_mode())
    assert m.speed == 0
    assert m.brightness == 3


def test_fix_mode_ignores_modes_without_ranges():
    m = E.fix_mode(SimpleNamespace(name="Off", speed=None, brightness=None))
    assert m.speed is None and m.brightness is None


def test_skydimo_level_has_floor_in_silence():
    cfg = {"skydimo_min_brightness": 0.35, "min_brightness": 0.06, "max_brightness": 1.0}
    assert abs(E.RGBEngine._skydimo_level(0.06, cfg) - 0.35) < 1e-6
    assert abs(E.RGBEngine._skydimo_level(1.0, cfg) - 1.0) < 1e-6
    mid = E.RGBEngine._skydimo_level(0.53, cfg)
    assert 0.35 < mid < 1.0


def test_led_floor_keeps_hue():
    out = E.apply_led_floor((2, 1, 0), {"min_brightness": 0.05})
    assert out[0] >= out[1] >= out[2]
    assert max(out) >= 12


def test_saturate_leaves_grey_alone():
    assert E.saturate_color(120, 121, 120, 1.0) == (120, 121, 120)


def test_transport_helpers():
    assert E.is_ble_target({"transport": "BLE"})
    assert E.is_skydimo_target({"transport": "skydimo"})
    assert not E.is_skydimo_target({})
