from core import config as C


def test_coerce_rejects_garbage_for_ints():
    out = C.coerce_patch({"rgb_fps": "abc", "overlay_fps": "45"})
    assert "rgb_fps" not in out          # мусор отбрасывается, а не роняет движок
    assert out["overlay_fps"] == 45


def test_coerce_clamps_floats():
    out = C.coerce_patch({"gamma": "200", "min_brightness": -1})
    lo, hi = C.FLOAT_KEYS["gamma"]
    assert out["gamma"] == hi
    assert out["min_brightness"] == 0.0


def test_coerce_keeps_strings_and_drops_empty_required_strings():
    out = C.coerce_patch({"static_color": "", "skydimo_mode": "solid",
                          "openrgb_path": "E:/x/OpenRGB.exe"})
    assert "static_color" not in out
    assert out["skydimo_mode"] == "solid"
    assert out["openrgb_path"] == "E:/x/OpenRGB.exe"


def test_generic_numeric_defaults_get_numbers():
    out = C.coerce_patch({"viz_x": "0.3"})
    assert out["viz_x"] == 0.3


def test_defaults_have_new_keys():
    for k in ("skydimo_mode", "skydimo_min_brightness", "openrgb_path", "mode_kick"):
        assert k in C.DEFAULTS
    assert "reassert_min" not in C.DEFAULTS
