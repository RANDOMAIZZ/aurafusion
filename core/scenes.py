"""Готовые сцены — именованные наборы настроек.

Сцена отвечает на вопрос «я сейчас играю / смотрю фильм / работаю», а не
«какой там формат линии». Переключение одной кнопкой переносит сразу всё:
режим цвета, силу эффектов, прозрачность оверлея, что включено.

Каждая сцена — словарь с ключом `patch`: те же ключи, что принимает
`/api/set`, поэтому применение сцены не требует отдельного кода на сервере.

Пользовательские сцены хранятся в `fusion_config.json` рядом с готовыми и
показываются после них. Если список в конфиге пуст, подставляются готовые —
чтобы сцены были сразу, без настройки.
"""

# Ключи, которые сцене полезно менять. Ограничение намеренное: сцена не
# должна трогать железо (устройства, порты, автозапуск) — иначе её
# применение может оборвать подсветку или включить то, чего не просил.
SAFE_KEYS = {
    "style", "shape_color", "color_mode", "line_style", "line_width",
    "height_scale", "fill_alpha", "alpha", "mirror", "beat_pulse",
    "beat_strength", "shake", "zoom", "flash", "vignette", "bg_dim",
    "dim_pulse", "sys_fx", "sys_mode", "overlay_opacity", "overlay_fps",
    "rgb_enabled", "rgb_mode", "rgb_fps", "min_brightness",
    "max_brightness", "follow_screen", "color_algo", "dark_threshold",
    "auto_balance", "dynamics", "sensitivity", "exclude_viz",
    "auto_hide", "click_through", "avoid_taskbar", "beat_mode",
    "spark_speed", "spark_boost", "spark_density", "spark_size", "viz_w",
    "spark_burst_count", "spark_burst_height",
}


BUILTIN = [
    {
        "id": "game",
        "name": "Игра",
        "hint": "Оверлей яркий и на всю ширину, подсветка реагирует на экран",
        "patch": {
            "style": "bars", "rgb_mode": "mix", "rgb_enabled": True,
            "follow_screen": True, "height_scale": 0.85, "alpha": 245,
            "line_style": "neon", "shape_color": "screen",
            "shake": 0.25, "zoom": 0.15, "flash": 0.30,
            "beat_pulse": True, "beat_strength": 0.55,
            "overlay_opacity": 1.0, "sys_fx": False,
            "viz_w": 0.94, "dark_threshold": 0, "auto_hide": False,
        },
    },
    {
        "id": "movie",
        "name": "Фильм",
        "hint": "Мягкая заливка по краям экрана, эффекты почти выключены",
        "patch": {
            "style": "smooth", "rgb_mode": "screen", "rgb_enabled": True,
            "follow_screen": True, "height_scale": 0.30, "alpha": 255,
            "line_style": "gradient", "shape_color": "tint",
            "shake": 0.0, "zoom": 0.0, "flash": 0.0, "vignette": 0.35,
            "beat_pulse": False, "beat_strength": 0.10,
            "overlay_opacity": 0.75, "sys_fx": False,
            "fill_alpha": 55, "dark_threshold": 0, "auto_hide": True,
            "max_brightness": 0.55, "min_brightness": 0.04,
        },
    },
    {
        "id": "work",
        "name": "Работа",
        "hint": "Оверлей убран в угол, подсветка приглушённая, эффекты мягкие",
        "patch": {
            "style": "ribbon", "rgb_mode": "mix", "rgb_enabled": True,
            "follow_screen": True, "height_scale": 0.22, "alpha": 210,
            "line_style": "thin", "shape_color": "screen",
            "shake": 0.0, "zoom": 0.0, "flash": 0.0, "vignette": 0.0,
            "beat_pulse": False, "beat_strength": 0.12,
            "overlay_opacity": 0.65, "sys_fx": False,
            "viz_w": 0.55, "auto_hide": True, "dark_threshold": 0,
            "max_brightness": 0.45, "min_brightness": 0.05,
        },
    },
    {
        "id": "party",
        "name": "Вечеринка",
        "hint": "Всё на максимум: плотные искры, разгон на удары, яркая подсветка",
        "patch": {
            "style": "sparks", "rgb_mode": "music", "rgb_enabled": True,
            "follow_screen": False, "height_scale": 1.20, "alpha": 255,
            "line_style": "megaglow", "shape_color": "palette",
            "shake": 0.45, "zoom": 0.35, "flash": 0.55,
            "beat_pulse": True, "beat_strength": 0.85,
            "overlay_opacity": 1.0, "sys_fx": True, "sys_zoom": 0.25,
            "spark_speed": 1.6, "spark_boost": 2.5, "spark_density": 2.0,
            "spark_burst_count": 1.4, "spark_burst_height": 0.90,
            "dark_threshold": 0, "max_brightness": 1.0, "min_brightness": 0.06,
        },
    },
    {
        "id": "calm",
        "name": "Тишина",
        "hint": "Только мягкий оверлей у нижнего края, подсветка почти не трогает",
        "patch": {
            "style": "smooth", "rgb_mode": "screen", "rgb_enabled": True,
            "follow_screen": True, "height_scale": 0.45, "alpha": 200,
            "line_style": "gradient", "shape_color": "tint",
            "shake": 0.0, "zoom": 0.0, "flash": 0.0, "vignette": 0.20,
            "beat_pulse": False, "beat_strength": 0.15,
            "overlay_opacity": 0.85, "sys_fx": False,
            "dark_threshold": 0.02, "max_brightness": 0.30,
            "min_brightness": 0.02, "auto_hide": True,
        },
    },
    {
        "id": "off",
        "name": "Выключить всё",
        "hint": "Оверлей и подсветка выключены — тёмная комната, ничего не светится",
        "patch": {
            "rgb_enabled": False, "overlay_opacity": 0.0,
            "sys_fx": False, "auto_hide": True,
        },
    },
]


def builtin():
    """Копия готовых сцен — чтобы вызывающий не мог испортить исходники."""
    return [dict(s, patch=dict(s["patch"])) for s in BUILTIN]


def sanitize(scene):
    """Привести сцену к безопасному виду: только известные ключи.

    В сцене не должно быть возможности выключить подсветку не там или испортить
    конфиг: ключи устройств и автозапуска отсекаются. Пользовательские сцены
    приходят из файла, им доверять нельзя.
    """
    if not isinstance(scene, dict):
        return None
    name = str(scene.get("name") or "").strip()
    if not name:
        return None
    patch = scene.get("patch")
    if not isinstance(patch, dict) or not patch:
        return None
    clean = {k: v for k, v in patch.items() if k in SAFE_KEYS}
    if not clean:
        return None
    return {
        "id": str(scene.get("id") or name)[:40],
        "name": name[:40],
        "hint": str(scene.get("hint") or "")[:120],
        "patch": clean,
    }


def all_scenes(cfg):
    """Готовые + пользовательские. Если пользовательских нет — готовые."""
    out = builtin()
    for s in (cfg.get("scenes") or []):
        c = sanitize(s)
        if c:
            out.append(c)
    return out