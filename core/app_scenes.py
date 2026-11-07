"""Автопереключение сцен по активному приложению.

Настройка `app_scenes` — строки вида «программа = сцена»:

    cs2.exe = game
    vlc.exe = movie
    * = work            # всё остальное (необязательно)

Имя программы сравнивается без учёта регистра, «.exe» можно не писать.
Сцена — id готовой («game», «movie», «work», «party», «calm», «off») или
своей. Переключение происходит только при смене активного приложения, поэтому
ручной выбор сцены в панели не перебивается каждые две секунды.
"""
import os


def parse_rules(text):
    """Текст правил -> (словарь программа->сцена, сцена по умолчанию или None)."""
    rules, default = {}, None
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        name, scene = (p.strip() for p in line.split("=", 1))
        if not name or not scene:
            continue
        if name == "*":
            default = scene
            continue
        name = name.lower()
        if not name.endswith(".exe"):
            name += ".exe"
        rules[name] = scene
    return rules, default


class AppSceneSwitcher:
    """Решает, какую сцену применить, когда сменилось активное приложение."""

    def __init__(self):
        self.last_exe = None
        self.last_scene = None

    def step(self, cfg, exe):
        """Вернуть id сцены, если её нужно применить сейчас, иначе None."""
        if not cfg.get("app_scenes_enabled"):
            self.last_exe = None
            return None
        exe = (exe or "").lower()
        if not exe or exe == self.last_exe:
            return None
        self.last_exe = exe
        rules, default = parse_rules(cfg.get("app_scenes", ""))
        scene = rules.get(exe, default)
        if not scene or scene == self.last_scene:
            return None
        self.last_scene = scene
        return scene


def foreground_exe():
    """Имя исполняемого файла активного окна (в нижнем регистре) или ''."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return ""
            return os.path.basename(buf.value).lower()
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""
