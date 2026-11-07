"""Файлы журнала: пути, простая ротация и чтение для страницы «Логи»."""
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "aurafusion.log")
CRASH_PATH = os.path.join(BASE_DIR, "crash.log")
OPENRGB_LOG = os.path.join(BASE_DIR, "openrgb_stdout.log")


def rotate_file(path, limit, keep=1):
    """Простая ротация для файлов, в которые пишет не logging (crash.log,
    вывод OpenRGB): если файл больше limit — отодвинуть в path.1."""
    try:
        if os.path.isfile(path) and os.path.getsize(path) > limit:
            for i in range(keep, 0, -1):
                src = path if i == 1 else "%s.%d" % (path, i - 1)
                dst = "%s.%d" % (path, i)
                if os.path.isfile(src):
                    os.replace(src, dst)
    except Exception:
        pass


_LOG_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
_LOG_LEVELS = {
    "errors": ("ERROR", "CRITICAL"),
    "problems": ("WARNING", "ERROR", "CRITICAL"),
    "all": None,
}


def read_logs(level="problems", limit=200, source="app"):
    """Последние записи лога для страницы «Логи» в панели.

    Читается хвост файла (до ~600 КБ), строки без метки времени (трейсбеки)
    приклеиваются к предыдущей записи, подряд идущие одинаковые сообщения
    схлопываются в одну со счётчиком — иначе одно и то же «лента не отвечает»
    занимает весь экран. Новые записи идут первыми."""
    path = {"app": LOG_PATH, "crash": CRASH_PATH,
            "openrgb": OPENRGB_LOG}.get(source, LOG_PATH)
    try:
        limit = max(10, min(1000, int(limit)))
    except (TypeError, ValueError):
        limit = 200
    out = {"source": source, "level": level, "path": os.path.basename(path),
           "size": 0, "entries": []}
    try:
        size = os.path.getsize(path)
        out["size"] = size
        with open(path, "rb") as f:
            if size > 600_000:
                f.seek(size - 600_000)
                f.readline()            # первая строка могла обрезаться
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return out
    if source != "app":
        # у crash.log и вывода OpenRGB нет формата logging — отдаём как есть
        lines = text.splitlines()[-limit:]
        out["entries"] = [{"t": "", "level": "INFO", "msg": ln, "count": 1}
                          for ln in reversed(lines) if ln.strip()]
        return out
    allowed = _LOG_LEVELS.get(level, _LOG_LEVELS["problems"])
    recs = []
    for ln in text.splitlines():
        m = _LOG_RE.match(ln)
        if m:
            recs.append({"t": m.group(1), "level": m.group(2), "msg": m.group(3), "count": 1})
        elif recs and ln.strip():
            recs[-1]["msg"] += chr(10) + ln
    entries = []
    for r in recs:
        if allowed is not None and r["level"] not in allowed:
            continue
        if entries and entries[-1]["level"] == r["level"] and entries[-1]["msg"] == r["msg"]:
            entries[-1]["count"] += 1
            entries[-1]["t_last"] = r["t"]
            continue
        entries.append(r)
    entries.reverse()
    out["entries"] = entries[:limit]
    out["total"] = len(entries)
    return out
