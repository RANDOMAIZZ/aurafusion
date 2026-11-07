from core.app_scenes import AppSceneSwitcher, parse_rules


def test_parse_rules():
    rules, default = parse_rules("""
        CS2.exe = game
        vlc = movie   # плеер
        * = work
        мусор без знака равенства
    """)
    assert rules == {"cs2.exe": "game", "vlc.exe": "movie"}
    assert default == "work"


def test_switches_only_on_app_change():
    sw = AppSceneSwitcher()
    cfg = {"app_scenes_enabled": True, "app_scenes": "cs2 = game\nvlc = movie"}
    assert sw.step(cfg, "cs2.exe") == "game"
    assert sw.step(cfg, "cs2.exe") is None        # то же приложение — не трогаем
    assert sw.step(cfg, "explorer.exe") is None   # нет правила и нет «*»
    assert sw.step(cfg, "vlc.exe") == "movie"
    assert sw.step(cfg, "VLC.EXE") is None


def test_disabled_does_nothing():
    sw = AppSceneSwitcher()
    assert sw.step({"app_scenes_enabled": False, "app_scenes": "cs2 = game"}, "cs2.exe") is None
