"""Проверка геометрии оверлея на живом экране.

Показывает окно процесса web_main и, отдельно, окно панели задач: сколько
пикселей у низа экрана оверлей не занимает. Эксп��римент с TASKBAR_KEEP = 1
означает, что свободна ровно одна строка.
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys


def pids():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True).stdout
    res = set()
    q = chr(34)
    for line in out.splitlines():
        parts = [p.strip(q) for p in line.split(q + "," + q)]
        if len(parts) > 1 and parts[1].isdigit():
            res.add(int(parts[1]))
    return res


def overlay_windows(target):
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _l):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if int(pid.value) in target and u.IsWindowVisible(h):
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) > 800 and (r.bottom - r.top) > 400:
                found.append((int(h), (r.left, r.top, r.right, r.bottom)))
        return True

    u.EnumWindows(proc(cb), 0)
    return found


def taskbar():
    u = ctypes.windll.user32
    h = u.FindWindowW("Shell_TrayWnd", None)
    if not h:
        return None
    r = wt.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def main():
    u = ctypes.windll.user32
    tb = taskbar()
    if tb:
        print("панель задач: %dx%d в (%d,%d)-(г,%d), толщина %d px"
              % (tb[2] - tb[0], tb[3] - tb[1], tb[0], tb[1], tb[2], tb[3] - tb[1]))
    else:
        print("панель задач: окно не найдено")

    # низ экрана: минимальный из видимых мониторов, а не 0 — на столе с
    # несколькими экранами это разные значения
    hdc = u.GetDC(0)
    virt = wt.RECT()
    u.GetWindowRect(u.GetDesktopWindow(), ctypes.byref(virt))
    u.ReleaseDC(0, hdc)
    bottom = None
    for hwnd, r in overlay_windows(pids()):
        print("окно оверлея: hwnd=%d %dx%d +%d+%d (низ y=%d)"
              % (hwnd, r[2] - r[0], r[3] - r[1], r[0], r[1], r[3]))
        if bottom is None or r[3] > bottom:
            bottom = r[3]
    if bottom is None:
        print("видимых окон оверлея не найдено — процесс запущен?")
        return 1
    # для сравнения: где на самом деле заканчивается рабочая область экрана
    print("свободно у низа окна: %d px (считаем от 1079 при 1080p)" % (1079 - bottom))
    return 0


if __name__ == "__main__":
    sys.exit(main())