"""Единый конфиг AuraFusion: визуал + RGB + экран->ПК."""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "fusion_config.json")

DEFAULTS = {
    # --- визуал (оверлей) ---
    "style": "smooth",          # smooth | bars | circle | combo
    "bars": 64,
    "overlay_fps": 60,
    "sensitivity": 1.4,
    "dynamics": 1.8,
    "max_level": 1.0,
    "height_scale": 0.55,
    "color_mode": "neon",       # neon | green | fire | white | custom
    "shape_color": "screen",   # цвет фигур (бары/круг/…): screen|tint|palette
    "custom_color": [0, 220, 255],
    "alpha": 230,
    "fill_color": [0, 200, 255],
    "fill_alpha": 90,
    "line_width": 3,
    "line_style": "thin",       # neon|thin|fat|double|dash|gradient|dotted|outline|steps|megaglow
    "wave_ceiling": 1.0,
    "mirror": False,
    "viz_x": 0.5, "viz_y": 0.93, "viz_w": 0.92,
    "fig_x": 0.5, "fig_y": 0.5, "fig_size": 1.0,   # положение и размер центральных фигур
    "style_settings": {},      # свои значения настроек для каждой фигуры: {стиль: {ключ: значение}}
    "line_count": 64,           # сколько линий/столбиков/спиц рисует фигура
    "line_smooth": 0.45,        # плавность линий 0..1
    "spark_speed": 1.0,         # базовая скорость подъёма искр
    "spark_boost": 1.0,         # насколько скорость растёт на громкой частоте
    "spark_density": 1.0,       # плотность искр, 1.0 — умеренно
    "spark_glow": 0.5,         # свечение искр: ореол вокруг точки, 0 = выкл
    "beat_pulse": True, "beat_strength": 0.35,
    "overlay_opacity": 1.0,
    "bass_gain": 1.0, "mid_gain": 1.25, "treble_gain": 1.7,
    "auto_balance": True,
    "attack_wave": 0.45, "release_wave": 0.88,
        "shake": 0.0,
    "zoom": 0.0,
    "flash": 0.0,
    "bg_dim": 0.0,
    "vignette": 0.0,            # виньетка оверлея 0..1 (0 = выкл)
    "sys_fx": False,            # SYS: приближение/тряска ВСЕГО экрана (лупа Windows)
    "sys_mode": "center",       # center | corners
    "sys_zoom": 0.5, "sys_shake": 0.5, "dim_pulse": 0.0,
    "auto_hide": False, "auto_hide_sec": 3.0,
    "monitor": 0,
    "color_monitor": -1,      # -1 = тот же монитор, где оверлей; иначе индекс экрана
    "color_region": "full",     # full | center | top | bottom | custom
    "color_rx": 0.0, "color_ry": 0.0, "color_rw": 1.0, "color_rh": 1.0,  # своя область (доли экрана)
    "click_through": True, "always_on_top": True,
    # Оверлей не залезает на панель задач. Иначе на автоскрытии она выезжает
    # под оверлей и кажется, что не поднялась.
    "avoid_taskbar": True,
    # Сцены: именованные наборы настроек, переключаются одним кликом.
    # Пустой список — приложение подставит свои сцены при первом запуске.
    "scenes": [],
    # --- цвет с экрана (общий для оверлея и ПК) ---
    "follow_screen": True,      # главный тумблер Ambilight
    "exclude_viz": True,        # не учитывать волну и панель при замере цвета
    "color_hold": 1,            # держать оттенок N замеров (анти-мигание) 1..6
    "color_algo": "juicy",      # juicy | average | kmeans
    "rgb_saturation": 0.6,      # дожим насыщенности ПК (красный, а не розовый)
    "rgb_gain_r": 1.0, "rgb_gain_g": 1.0, "rgb_gain_b": 1.0,  # баланс белого автотракта 0.3..1.5
    "dark_threshold": 0,      # порог темноты ПК 0..255: темнее порога гаснет (0 = выкл)
    "auto_speed": 0.6,          # скорость подтяжки сырого цвета 0.05..1
    "color_fx": "smooth",       # smooth|instant|rainbow|breathe|beat
    "color_speed": 0.28,        # скорость перехода 0.02..0.5
    # --- звук (общий движок pyaudiowpatch) ---
    "audio_device": "",
    "chunk": 2048,
    "silence_thr": 0.002,
    "demo": False,
    # --- подсветка ПК ---
    "rgb_enabled": True,
    "rgb_mode": "mix",          # mix (цвет экран + пульс музыка) | screen | music
    "static_color": "#C50DF1",
    "color_all": True,
    "devices": {
        "B760 GAMING X DDR4 GEN5": {"zone_name": "ARGB_V2_2", "leds": 60, "enabled": True, "color_hex": "#C50DF1"},
    },
    "rgb_fps": 30,
    "bass_boost": 1.6,
    "attack_s": 0.05, "release_s": 0.4, "avg_s": 2.0,
    "pulse_threshold": 0.10, "pulse_gain": 1.3,
    "abs_floor": 0.004, "gamma": 1.0,
    "min_brightness": 0.02, "max_brightness": 1.0,
    "startup_delay_s": 20, "idle_fps": 5, "send_threshold": 0.004, "reassert_min": 0,
    "mode_kick": True,  # авто-кик режима GPU при старте (лечит залипший Direct)
    # --- BLE-лента (ELK-BLEDOM/BLEDDM) как обычное устройство подсветки ---
    "ble_enabled": True,    # сканировать BLE-ленты рядом
    "ble_scan_s": 8,        # длительность BLE-сканирования при поиске устройств
    "ble_max_fps": 20,      # потолок частоты BLE-кадров (клоны рвут GATT на 60 fps)
    # --- система ---
    "web_theme": "neon",          # тема web-интерфейса, 20 шт. (список — THEMES в webui/app.js)
    "autostart": False, "autosave": True,
    "openrgb_host": "127.0.0.1", "openrgb_port": 6742,
}


# Ключи, которые обязаны быть числами. Панель местами писала их строками
# («60» вместо 60), и код, делящий на них, падал с TypeError — приложение
# не стартовало. Приводим тип при загрузке, чтобы правка в конфиге или в
# веб-интерфейсе не могла сломать запуск.
INT_KEYS = {
    "overlay_fps", "rgb_fps", "bars", "line_width", "alpha", "fill_alpha", "line_count",
    "chunk", "monitor", "color_monitor", "idle_fps",
    "ble_max_fps", "color_hold", "dark_threshold", "reassert_min",
}

# Дробные ключи. Их нельзя класть в INT_KEYS: int() от 0.35 даёт 0, и
# значение, выставленное в панели, молча превращалось в ноль при каждой
# загрузке. Так пропадали обе границы яркости подсветки: «мин. яркость»
# крутится в диапазоне 0..0.50, а «макс. яркость» шлёт 0.20..1.00, но обе
# стояли в списке целых рядом с прочими числами — и всегда грузились нулём,
# то есть подсветка работала только на «полную», а ползунки были бесполезны.
FLOAT_KEYS = {
    "min_brightness": (0.0, 1.0),
    "max_brightness": (0.0, 1.0),
    "gamma": (0.1, 10.0),
    "pulse_gain": (0.0, 50.0),
    "avg_s": (0.05, 60.0),
    "abs_floor": (0.0, 1.0),
    "pulse_threshold": (-1.0, 5.0),
}

# Строковые ключи, которые по ошибке тоже попали в INT_KEYS: int(float())
# на «#C50DF1» падает, обработчик проглатывал исключение и молча возвращал
# значение по умолчанию — выбранный цвет просто не сохранялся.
STR_KEYS = {"static_color"}


def load():
    cfg = dict(DEFAULTS)
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k in DEFAULTS:
                    cfg[k] = v
            # devices отдельно (вложенный dict)
            if isinstance(data.get("devices"), dict):
                cfg["devices"] = data["devices"]
            # style_settings — вложенный dict, а в DEFAULTS он пустой,
            # поэтому обычный фильтр по ключам его бы отбросил
            if isinstance(data.get("style_settings"), dict):
                cfg["style_settings"] = data["style_settings"]
            # scenes — список словарей, в DEFAULTS он пустой, поэтому обычный
            # фильтр по ключам его бы отбросил
            if isinstance(data.get("scenes"), list):
                cfg["scenes"] = [s for s in data["scenes"] if isinstance(s, dict)]
    except Exception as e:
        print(f"[config] load warn: {e}")
    for k in INT_KEYS:
        try:
            if isinstance(cfg.get(k), bool):
                continue
            cfg[k] = int(float(cfg[k]))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS.get(k)
    for k, (lo, hi) in FLOAT_KEYS.items():
        try:
            if isinstance(cfg.get(k), bool):
                continue
            cfg[k] = float(min(hi, max(lo, float(cfg[k]))))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS.get(k)
    for k in STR_KEYS:
        if not isinstance(cfg.get(k), str) or not cfg[k]:
            cfg[k] = DEFAULTS.get(k)
    return cfg


def save(cfg):
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception as e:
        print(f"[config] save warn: {e}")
        return False
