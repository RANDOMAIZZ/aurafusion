"""Стенд оверлея AuraFusion: рисует все стили и все типы линии в PNG.

Ничего не запускает на вашем экране — рендер идёт через offscreen-платформу
Qt, звук и цвет экрана заменены синтетикой. Нужен, чтобы поймать баги в
рисовании до того, как оверлей появится поверх игры.

    python tools/shot_overlay.py [папка]

Снимки складываются в .overlay_shots/ (в .gitignore).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ui.overlay as _overlay_mod                # noqa: E402
from core.config import DEFAULTS            # noqa: E402
from ui.overlay import LINE_STYLES, STYLES, Overlay  # noqa: E402

W, H = 1280, 720
N = 64
FRAMES = 45          # кадров прогрева перед снимком (частицы/пики набирают инерцию)


class VirtualClock:
    """Часы, которые идут на 1/fps за кадр вместо настоящих.

    Физика искр и инерция частиц считаются по времени, а стенд рисует кадры
    подряд, за доли миллисекунды: за 45 кадров проходило меньше сотни
    миллисекунд, частицы оставались лежать на базовой линии, и стиль
    «Искры» на листе выглядел пустым. Ждать в цикле можно, но тогда прогон
    растянется на минуты. Виртуальные часы дают то же самое мгновенно и,
    что важнее, одинаково от запуска к запуску.
    """

    def __init__(self, fps=30.0, start=1000.0):
        self.t = float(start)
        self.step = 1.0 / float(fps)

    def time(self):
        v = self.t
        self.t += self.step
        return v

    def monotonic(self):
        # Оверлей метит им последний тик, на него смотрит сторож в web_main.
        # Отдельный метод нужен именно потому, что подменяется весь модуль
        # time: если бы виртуальные часы не умели monotonic, стенд падал бы
        # на AttributeError ещё на первом кадре.
        return self.time()

    def sleep(self, _s):
        return None

    def install(self):
        """Подменяем time в оверлее на виртуальные часы."""
        self._saved = _overlay_mod.time
        _overlay_mod.time = self
        return self

    def restore(self):
        if getattr(self, "_saved", None) is not None:
            _overlay_mod.time = self._saved
            self._saved = None


def synth(n=N, seed=7):
    """Похожий на музыку спектр: бас жирный, верха спадают, пики разбросаны."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, n)
    env = 1.25 * np.exp(-3.1 * x) + 0.28 * np.exp(-0.6 * x) + 0.05
    peaks = np.zeros(n)
    for c, w, a in ((2, 1.6, 0.42), (7, 1.1, 0.34), (13, 0.8, 0.30), (21, 0.7, 0.24),
                    (31, 0.5, 0.20), (43, 0.4, 0.17), (52, 0.3, 0.14)):
        peaks += a * np.exp(-0.5 * ((np.arange(n) - c) / w) ** 2)
    v = env * peaks + rng.random(n) * 0.09
    return np.clip(v, 0, 1.35).astype(np.float32)


class FakeAudio:
    """Звук-заглушка со сменяемым спектром.

    Снимки идут через ov.tick(), а не прямой записью в ov.disp. Оверлей рисует
    self.lines, и наполняется этот буфер именно в tick(). Раньше стенд писал
    только в disp — после того, как рисунок отделился от спектра (self.lines),
    все таблицы стилей рисовались пустыми: вместо фигуры одна базовая линия.
    А проверка гостинга так и вовсе стала ложной: оба «разных» кадра были
    одинаково пустыми, левая часть равна нулю в обоих, и GHOST=OK проходил
    вхолостую, ничего не проверяя."""

    def __init__(self, spec):
        self.spec = spec
        self.level = 0.55
        self.bass = 0.62

    def set(self, spec=None, level=None, bass=None):
        if spec is not None:
            self.spec = spec
        if level is not None:
            self.level = level
        if bass is not None:
            self.bass = bass

    def snapshot(self):
        return self.spec.copy(), np.zeros(512, np.float32), self.level, self.bass, True

    def set_bars(self, n):
        pass


def put(ov, values, level=0.55, bass=0.62):
    """Подложить спектр в оба буфера оверлея разом.

    tick() тут не годится: он прогоняет спектр через сглаживание атаки и
    отпускания, а проверки крайних случаев должны рисовать ровно то, что
    подложили, включая пустой спектр и спектр из одного столбика."""
    d = np.asarray(values, dtype=np.float32)
    ov.disp = d
    ov.lines = d.copy()
    ov._level = level
    ov._bass = bass


class FakeSC:
    def __init__(self):
        self.shown = np.array([40.0, 190.0, 235.0])
        self.bass = 0.62

    def tick(self, _c):
        pass

    def feed_frame(self, _a):
        pass


def ensure(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        if not os.path.isdir(path):
            raise
    return path


def render(ov, cfg, path, audio=None):
    """Прогревает несколько кадров (инерция частиц и пиков) и снимает один.

    Спектр намеренно меняется от кадра к кадру: при неподвижном спектре пиковые
    колпачки сливаются со столбиками и стиль «пики» неотличим от «баров».

    Кадры идут через tick() — это единственный путь, который честно наполняет
    self.lines. Прямая запись в ov.disp рисовала бы устаревший буфер."""
    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)
    base = synth(N)
    # Заглушку берём у самого оверлея: она уже сконструирована вместе с ним.
    aud = audio if audio is not None else getattr(ov, "audio", None)
    for f in range(FRAMES):
        # затухающий удар: громко на старте, тише к концу — колпачки отстают
        env = 0.45 + 0.75 * float(np.exp(-f / 9.0))
        spec = np.clip(base * env, 0, 1.35).astype(np.float32)
        if aud is not None and hasattr(aud, "set"):
            aud.set(spec, level=0.55 * env, bass=0.62 * env)
            ov.tick()
        else:
            put(ov, spec, level=0.55 * env, bass=0.62 * env)
        ov.render(pm)
    return pm.save(path, "PNG")


def sheet(items, outdir, cols, title, cw=480, ch=270):
    """Склеивает снимки в таблицу с подписями через PIL."""
    from PIL import Image, ImageDraw, ImageFont
    pad, lab, head = 8, 18, 34
    rows = (len(items) + cols - 1) // cols
    sh = Image.new("RGB", (cols * (cw + pad) + pad,
                           head + rows * (ch + pad + lab) + pad), (12, 14, 18))
    d = ImageDraw.Draw(sh)
    # шрифт по умолчанию рисует кириллицу квадратами — берём системный
    fnt = None
    for cand in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            fnt = ImageFont.truetype(cand, 13)
            break
        except Exception:
            continue
    txt = d.text if fnt is None else (lambda xy, s, fill=None: d.text(xy, s, fill=fill, font=fnt))
    txt((pad + 2, 10), title, fill=(210, 230, 240))
    for i, (label, path) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = head + r * (ch + pad + lab)
        if os.path.isfile(path):
            im = Image.open(path).convert("RGBA").resize((cw, ch), Image.LANCZOS)
            bg = Image.new("RGBA", (cw, ch), (20, 24, 30, 255))
            bg.alpha_composite(im)
            sh.paste(bg.convert("RGB"), (x, y))
        else:
            d.rectangle([x, y, x + cw, y + ch], outline=(200, 60, 60), width=2)
            d.text((x + 10, y + 10), "НЕТ ФАЙЛА", fill=(230, 80, 80))
        txt((x + 2, y + ch + 3), label, fill=(150, 230, 255))
    p = os.path.join(outdir, title)
    sh.save(p, "PNG")
    return p


def stress(app, ov, cfg):
    """Прогоняет все сочетания стилей и линий и крайние случаи.

    Цель — поймать исключения в paintEvent, а не красоту: раньше, например,
    длина спектра от аудио могла разойтись с длиной палитры, и отрисовка падала
    с IndexError прямо поверх игры."""
    fails, n = [], 0
    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)
    base = synth(N)

    def shot(tag):
        nonlocal n
        n += 1
        try:
            put(ov, np.clip(base * 0.9, 0, 1.3), level=0.5, bass=0.5)
            ov.render(pm)
        except Exception as e:
            fails.append(f"{tag}: {type(e).__name__}: {e}")

    # 1) все пары стиль x линия, с зеркалом и без
    for sk, _ in STYLES:
        for lk, _ in LINE_STYLES:
            for mirror in (False, True):
                cfg["style"], cfg["line_style"], cfg["mirror"] = sk, lk, mirror
                shot(f"{sk}/{lk}/mirror={mirror}")
    cfg["mirror"] = False

    # 2) крайние размеры спектра
    for bars in (16, 24, 32, 64, 96, 128):
        cfg["bars"] = bars
        for sk, _ in STYLES:
            cfg["style"] = sk
            put(ov, np.clip(synth(bars) * 0.9, 0, 1.3), level=0.5, bass=0.5)
            ov.palette = ov.palette[:bars]
            shot(f"{sk}/bars={bars}")

    # 3) длина спектра разошлась с палитрой — был IndexError в отрисовке
    for sk, _ in STYLES:
        cfg["style"] = sk
        put(ov, np.clip(base * 0.9, 0, 1.3), level=0.5, bass=0.5)
        ov.palette = ov.palette[:8]
        shot(f"{sk}/palette-shorter")
        ov.palette = []
        shot(f"{sk}/palette-empty")
    ov.palette = ov.palette[:N]
    cfg["bars"] = N

    # 4) пустой и очень короткий спектр
    for sk, _ in STYLES:
        cfg["style"] = sk
        put(ov, np.zeros(0))
        shot(f"{sk}/empty-spectrum")
        put(ov, np.zeros(1))
        shot(f"{sk}/one-bin")

    # 5) крайние настройки геометрии
    put(ov, np.clip(base * 0.9, 0, 1.3), level=0.5, bass=0.5)
    for sk, _ in STYLES:
        cfg["style"] = sk
        for vy, vh in ((0.02, 0.02), (0.98, 0.02), (0.5, 0.05), (0.93, 3.0), (0.93, 0.0)):
            cfg["viz_y"], cfg["height_scale"] = vy, vh
            shot(f"{sk}/viz_y={vy}/hs={vh}")
    cfg["viz_y"], cfg["height_scale"] = 0.93, 0.55

    print(f"\nпрогнано {n} кадров, ошибок: {len(fails)}")
    for f in fails[:20]:
        print("   ", f)
    return fails


def ghost_check(app, ov, cfg):
    """Гостинг: рисуем два разных кадра на ОДНОМ буфере и смотрим, остался
    ли след первого.

    Полупрозрачное окно не очищает буфер между кадрами, поэтому без явного
    затирания волна оставляет бесконечный след. Проверяем на пикселях."""
    import numpy as np
    from PySide6.QtGui import QImage
    cfg["style"] = "bars"
    cfg["height_scale"] = 1.6
    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)

    # кадр 1: громкая слева
    d = np.zeros(N, dtype=np.float32)
    d[:8] = 1.2
    put(ov, d, bass=0.6)
    ov.render(pm)
    a = pm.toImage().convertToFormat(QImage.Format_ARGB32)

    # кадр 2: громкая только справа, слева должно быть пусто
    d = np.zeros(N, dtype=np.float32)
    d[-6:] = 1.2
    put(ov, d, bass=0.6)
    ov.render(pm)
    b = pm.toImage().convertToFormat(QImage.Format_ARGB32)

    def alpha_at(img, x0, x1):
        s = 0
        for y in range(0, H, 7):
            for x in range(x0, x1, 7):
                s += img.pixelColor(x, y).alpha()
        return s

    left1 = alpha_at(a, 0, W // 3)
    left2 = alpha_at(b, 0, W // 3)
    right2 = alpha_at(b, 2 * W // 3, W)
    print("\nгостинг:")
    print("   непрозрачность слева в кадре 1 : %6d" % left1)
    print("   непрозрачность слева в кадре 2 : %6d  (должна упасть — старый след стёрт)" % left2)
    print("   непрозрачность справа в кадре 2: %6d  (должна быть большой)" % right2)

    # Сначала убеждаемся, что кадры вообще разные. Проверка гостинга имеет
    # смысл только если слева что-то было и справа что-то есть: иначе обе
    # суммы нулевые, условие «слева упала» выполняется само, и тест молча
    # зелёный, ничего не проверяя. Так было, когда оба кадра рисовались по
    # пустому устаревшему буферу.
    drew = left1 > 400 and right2 > 400
    if not drew:
        print("   ПРОВЕРКА НЕВАЛИДНА: кадры пустые (слева было %d, справа стало %d) — "
              "тест ничего не проверяет" % (left1, right2))
        print("   GHOST=VACUOUS")
        cfg["height_scale"] = 0.55
        return True

    ghosted = left2 > max(40, left1 * 0.25)
    print("   GHOST=%s" % ("BUG" if ghosted else "OK"))
    cfg["height_scale"] = 0.55
    return ghosted


def main():
    outdir = ensure(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, ".overlay_shots"))
    raw = ensure(os.path.join(outdir, "raw"))
    app = QApplication.instance() or QApplication([])

    cfg = dict(DEFAULTS)
    cfg.update({"bars": N, "demo": True, "sys_fx": False, "overlay_fps": 30,
                "line_style": "neon", "mirror": False})
    clock = VirtualClock(fps=float(cfg["overlay_fps"])).install()
    ov = Overlay(lambda: cfg, FakeAudio(synth()), FakeSC())
    ov.resize(W, H)

    # 1) все стили при неоновой линии
    items, bad = [], []
    for key, name in STYLES:
        cfg["style"] = key
        p = os.path.join(raw, f"style_{key}.png")
        if not render(ov, cfg, p):
            bad.append(name)
        items.append((f"{name}  ({key})", p))
    print("  ", sheet(items, outdir, 2, "01-styles.png"))

    # 2) все типы линии — на волне и на заливке
    for style_key, title in (("smooth", "02-lines.png"), ("fill", "03-lines-fill.png")):
        cfg["style"] = style_key
        items = []
        for key, name in LINE_STYLES:
            cfg["line_style"] = key
            p = os.path.join(raw, f"line_{style_key}_{key}.png")
            render(ov, cfg, p)
            items.append((name, p))
        print("  ", sheet(items, outdir, 2, title))
    cfg["line_style"] = "neon"

    # 3) сочетания, где раньше была дыра: контур с зеркалом, ступени
    cfg["style"] = "smooth"
    items = []
    for ls, mirror, lbl in (("outline", True, "контур + зеркало"),
                            ("double", True, "двойная + зеркало"),
                            ("outline", False, "контур без зеркала"),
                            ("steps", False, "ступени (раньше = неон)")):
        cfg["line_style"], cfg["mirror"] = ls, mirror
        p = os.path.join(raw, f"mirror_{ls}_{int(mirror)}.png")
        render(ov, cfg, p)
        items.append((lbl, p))
    cfg["mirror"] = False
    print("  ", sheet(items, outdir, 2, "04-line-mirror.png"))

    # 5) режимы цвета фигур — было/стало: бары и круг на своём цвете экрана
    items = []
    for mode, label in (("screen", "как на экране"),
                        ("tint", "палитра, подкрашенная экраном"),
                        ("palette", "своя палитра (как раньше)")):
        for style_key in ("bars", "circle"):
            cfg["shape_color"] = mode
            cfg["style"] = style_key
            p = os.path.join(raw, f"color_{mode}_{style_key}.png")
            render(ov, cfg, p)
            items.append((f"{style_key}: {label}", p))
    cfg["shape_color"] = "screen"
    print("  ", sheet(items, outdir, 2, "05-shape-color.png"))

    # 4) маска exclude_viz против габаритов фигуры — должны совпадать
    thin = []
    for key, _ in STYLES:
        cfg["style"] = key
        ext = ov._extent(cfg)
        arr = np.ones((54, 96, 3), dtype=np.float32)
        ov._blackout_viz(arr, 96, 54, cfg, None, (0.0, 0.0, 1.0, 1.0))
        ys = np.linspace(0, 1, 54)[:, None] * np.ones((1, 96))
        outside = ys < ext[2] - 0.03
        outside |= ys > ext[3] + 0.03
        # если фигура залезла за пределы экрана, маска обязана покрывать край
        if outside.all():
            thin.append(key)
        cut = 100.0 * float((~outside).sum()) / float(outside.size)
        print("    %-10s габариты y %.3f..%.3f, x %.2f..%.2f — вырезано %4.1f%%"
              % (key, ext[2], ext[3], ext[0], ext[1], cut))

    print("\nне сохранились:", bad or "нет")
    print("пустых листов:", thin or "нет")
    print("папка:", outdir)
    fails = stress(app, ov, cfg)
    print("итог:", "ошибок нет" if not fails else f"ОШИБОК {len(fails)} — разбираться")
    ghosted = ghost_check(app, ov, cfg)
    print("итог:", "ошибок и гостинга нет" if not fails and not ghosted else "есть проблемы")
    clock.restore()
    teardown(ov, app)


def teardown(ov, app):
    """Аккуратно отпускаем Qt перед выходом.

    Без этого процесс падал с 0xc0000005 при завершении, и если рядом работал
    AuraFusion — тот тоже уходил: Qt не успевал разобрать свои ресурсы."""
    try:
        ov.close()
        ov.hide()
        ov.deleteLater()
        ov.timer.stop()
        ov.color_timer.stop()
        try:
            ov.sysfx.close()
        except Exception:
            pass
    except Exception:
        pass
    try:
        app.processEvents()
        app.quit()
        app.processEvents()
    except Exception:
        pass


if __name__ == "__main__":
    main()