"""Автозапуск AuraFusion вместе с Windows.

Живёт в реестре, в ветке Run пользователя: HKCU, не HKLM. Права администратора
не нужны, и запись видна пользователю — в «Диспетчере задач → Автозагрузка».

Почему реестр, а не ярлык в «Пуске»:
ярлык там запускается через оболочку, и у него нельзя задать рабочую папку.
Программа читает и пишет конфиг рядом с собой, поэтому запуститься из
C:\\Windows\\System32 она не сможет. С реестром запускаем pythonw с абсолютным
путём к web_main.py — куда угодно, рабочая папка не нужна.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AuraFusion"


def _pythonw_path():
    """Путь к pythonw — всегда абсолютный.

    Раньше тут было splitext(exe)[0] + "w.exe". Программа запускается как раз
    через pythonw, и из "pythonw.exe" получалось "pythonww.exe" — файла нет,
    срабатывал запасной вариант, и в реестр ложилось голое "pythonw.exe" без
    пути. При входе в Windows система искала бы его в C:\\Windows\\System32,
    не находила и молча ничего не запускала — ровно та же беда, что была с
    удалённым main.py.
    """
    exe = sys.executable or ""
    base = os.path.splitext(exe)[0]
    # уже pythonw — приписывать w ещё раз нельзя
    if base.lower().endswith("w") and os.path.isfile(exe):
        return exe
    cand = base + "w.exe"
    if base and os.path.isfile(cand):
        return cand
    # pythonw лежит рядом с тем интерпретатором, которым нас запустили
    guess = os.path.join(os.path.dirname(exe), "pythonw.exe") if exe else ""
    if guess and os.path.isfile(guess):
        return guess
    return "pythonw.exe"


def _entry_path():
    """Команда запуска: pythonw + web_main.py, браузер не открываем.

    pythonw, а не python: иначе при входе в систему на секунду мелькнёт
    чёрное консольное окно.
    """
    script = os.path.join(BASE_DIR, "web_main.py")
    return '"%s" "%s" --no-browser' % (_pythonw_path(), script)


def _entry_is_usable(val):
    """Запись в Run действительно что-то запустит.

    Проверяем не только наличие строки, но и что оба файла на месте и путь
    к интерпретатору абсолютный. Запись вида «pythonw.exe web_main.py» без
    пути Windows при входе ищет в C:\\Windows\\System32 и молча ничего не
    делает — а галочка в панели показывает «включено».
    """
    val = str(val or "")
    low = val.lower()
    if "web_main.py" not in low:
        return False
    script = os.path.join(BASE_DIR, "web_main.py")
    if not os.path.isfile(script):
        return False
    parts = [p.strip(' "') for p in val.split('"') if p.strip(' "')]
    for p in parts:
        if p.lower().endswith("pythonw.exe"):
            return os.path.isabs(p) and os.path.isfile(p)
    # интерпретатор не нашли — запись битая, запускать нечем
    return False


def is_enabled():
    """Запись есть и действительно сработает.

    Раньше проверка была «есть строка и в ней web_main.py». Этого мало: в Run
    лежала запись на удалённый main.py, и Windows при входе молча пыталась
    запустить то, чего уже нет, а галочка в панели стояла «включено».
    """
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
    except OSError:
        return False
    return _entry_is_usable(val)


def enable():
    try:
        import winreg
    except ImportError:
        return False, "только Windows"
    cmd = _entry_path()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    except OSError as e:
        return False, str(e)
    # Заодно убираем возможный мусор от прежней версии — запись на удалённый
    # main.py. Windows не показывает её в «Автозагрузке», но она остаётся
    # висеть в реестре и каждый вход в систему пытается её выполнить.
    _drop_stale()
    return True, cmd


def _drop_stale():
    """Снести записи Run, ссылающиеся на несуществующие файлы проекта."""
    try:
        import winreg
    except ImportError:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumValue(k, i)[0])
                except OSError:
                    break
                i += 1
    except OSError:
        return
    for name in names:
        if name != VALUE_NAME and not str(name).lower().startswith("aurafusion"):
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                val, _ = winreg.QueryValueEx(k, name)
        except OSError:
            continue
        if _entry_is_usable(val):
            continue
        # указывает на файл, которого нет, — смысла держать нет
        parts = [p.strip(' "') for p in str(val).split('"') if p.strip(' "')]
        target = None
        for p in parts:
            low = p.lower()
            if low.endswith(".py") or low.endswith(".exe"):
                target = p
                break
        if target and not os.path.isfile(target):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                     winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, name)
            except OSError:
                pass


def disable():
    try:
        import winreg
    except ImportError:
        return False, "только Windows"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
    except FileNotFoundError:
        return True, "уже выключено"
    except OSError as e:
        return False, str(e)
    return True, "выключено"


def status():
    """Что показывать панели: включено ли по-настоящему и какой командой."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
    except Exception:
        val = ""
    return {"enabled": is_enabled(), "command": str(val), "supported": sys.platform == "win32"}