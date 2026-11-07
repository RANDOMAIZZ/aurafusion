"""Поиск и запуск сервера OpenRGB."""
import logging
import os
import subprocess

from core.logs import BASE_DIR, OPENRGB_LOG, rotate_file

log = logging.getLogger("aurafusion.web")

# Любой дочерний процесс без этого флага под pythonw на секунду открывает
# чёрное консольное окно. Раньше константа жила в web_main и при одной из
# правок пропала — запуск OpenRGB молча падал с NameError.
NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW


def openrgb_candidates(extra=""):
    """Где искать OpenRGB.exe: путь из настроек (openrgb_path), переменная
    окружения AURAFUSION_OPENRGB, папка OpenRGB рядом с программой и обычные
    места установки. Личных путей в коде нет."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    return [c for c in (
        extra,
        os.environ.get("AURAFUSION_OPENRGB", ""),
        os.path.join(BASE_DIR, "OpenRGB", "OpenRGB.exe"),
        os.path.join(pf, "OpenRGB", "OpenRGB.exe"),
        os.path.join(local, "Programs", "OpenRGB", "OpenRGB.exe") if local else "",
    ) if c]


def find_openrgb(extra=""):
    for p in openrgb_candidates(extra):
        if os.path.isfile(p):
            return p
    return None


def launch_openrgb(host, port, path=""):
    """Поднять сервер OpenRGB, если его ещё нет.

    Раньше эта функция жила в десктопном main.py, а веб импортировал её
    оттуда. Стоило удалить main.py — и веб перестал запускаться совсем."""
    exe = find_openrgb(path)
    if not exe:
        return False
    try:
        out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq OpenRGB.exe"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout
        if "OpenRGB.exe" in out:
            return True
    except Exception:
        pass
    try:
        orgb_log = OPENRGB_LOG
        rotate_file(orgb_log, 1_000_000)
        out_f = open(orgb_log, "ab", buffering=0)
        subprocess.Popen([exe, "--server", "--server-host", host,
                          "--server-port", str(port), "--startminimized"],
                         stdout=out_f, stderr=subprocess.STDOUT,
                         creationflags=subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00004000
                         | NO_WINDOW)
        return True
    except Exception as e:
        log.error("openrgb launch: %s", e)
        return False
