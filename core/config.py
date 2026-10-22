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
    "spark_boost": 1.0,         # насколько быстрые искры обгоняют медленный фон
    "spark_density": 1.0,       # плотность искр, 1.0 — умеренно
    # Размер частицы у искр. Отдельный от height_scale: раньше одна ручка
    # дёргала и высоту полёта, и толщину точки, и крупнее точки можно было
    # было сделать только вместе с высотой.
    "spark_size": 1.0,
    # Всплеск: сколько частиц в одном пучке (множитель к росту спектра)
    # и потолок его подъёма в долях высоты экрана.
    "spark_burst_count": 0.7,
    "spark_burst_height": 0.35,
    # Зависание на вершине. 0 — частица гаснет, как только долетела до
    # вершины; 1 — половину срока жизни висит наверху (так было раньше,
    # и это давало пышный фонтан, но и постоянное облако под потолком).
    # 2 — ещё дольше.
    "spark_hover": 1.0,
    # Пучок на сильном басовом ударе. 0 — выключено, 1 — на самый сильный
    # удар летит около 30 частиц разом из одной точки.
    "spark_bass_burst": 0.5,
    "spark_glow": 0.5,         # свечение искр: ореол вокруг точки, 0 = выкл
    # Ореол вокруг фигуры целиком, для всех стилей кроме искр (у них свой).
    # Аналог CSS-овского drop-shadow: рисуем вчетверо меньший кадр и
    # растягиваем обратно, Qt сглаживает при масштабировании.
    "shape_glow": 0.0,
    # Где по высоте полёта горит ореол: от 0 (у базовой линии) до 1
    # (на вершине). Начало <= конец. По умолчанию горит внизу и гаснет
    # к вершине; если начало поднять к 0.6, то снизу искры чистые, а
    # загораются только у верха.
    "spark_glow_start": 0.0,
    "spark_glow_end": 0.55,
    # Насколько искры могут быть тёмными. 1 — как есть, цвет из замера
    # экрана; 0 — всегда ярко и насыщенно, тёмный экран не гасит искры.
    "spark_dark": 0.25,   # старое имя, переносится в color_dark при загрузке
    # Насколько цвета могут быть тёмными — для фигур оверлея и для подсветки
    # ПК сразу. 1 — как есть, строго тем же цветом, что определился с экрана;
    # 0 — всегда ярко и насыщенно.
    "color_dark": 0.25,
    # Как часто берётся цвет с экрана, мс. Замер стоит около 1 мс, поэтому
    # ограничивает не он, а сглаживание auto_speed: при 500 мс до 90% цвета
    # доходило за секунду.
    "color_sample_ms": 200,
    # Мешает ли окно панели замеру. По умолчанию НЕ мешает: когда панель
    # развёрнута во весь экран, на экране нет ничего, кроме неё — значит она и
    # есть тот контент, цвет которого пользователь хочет видеть. Раньше здесь
    # стояло «мешает», и цвет замирал наглухо ровно тогда, когда пользователь
    # смотрел на панель. Оставлено галкой для тех, кому нужно старое поведение.
    "panel_blocks": False,
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
    # свой ИСТОЧНИК цвета по типу устройства. Пусто ("") — как в rgb_mode.
    # Так можно держать видеокарту на цвете экрана, а блютус-ленту — на
    # музыке или на одной постоянной, и наоборот.
    "color_source_gpu": "",     # "" | screen | music | static
    "color_source_ble": "",     # "" | screen | music | static
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
    "chunk", "monitor", "color_monitor", "idle_fps", "color_sample_ms",
    "ble_max_fps", "color_hold", "dark_threshold", "reassert_min",
}

# Дробные ключи. Их нельзя класть в INT_KEYS: int() от 0.35 даёт 0, и
# значение, выставленное в панели, молча превращалось в ноль при каждой
# загрузке. Так пропадали обе границы яркости подсветки: «мин. яркость»
# крутится в диапазоне 0..0.50, а «макс. яркость» шлёт 0.20..1.00, но обе
# стояли в списке целых рядом с прочими числами — и всегда грузились нулём,
# то есть подсветка работала только на «полную», а ползунки были бесполезны.
FLOAT_KEYS = {
    # Свечение фигуры и искр хранится как доля, а ползунок показывает сотни:
    # 100 это 1.0. Потолок в 3.0 — как у ползунка, дальше всё равно не читается.
    "shape_glow": (0.0, 3.0),
    "spark_dark": (0.0, 1.0),
    "color_dark": (0.0, 1.0),
    "spark_glow": (0.0, 3.0),
    "spark_size": (0.2, 3.0),
    "spark_burst_count": (0.0, 8.0),
    "spark_burst_height": (0.02, 0.95),
    "spark_hover": (0.0, 2.0),
    "spark_bass_burst": (0.0, 1.0),
    # Потолок 8.0, а не 4.0: бюджет частиц считается как «количество линий»
    # × 4 с потолком 2048, и при 128 линиях одно деление плотности — это
    # 512 частиц. На старом пределе в 4.0 верхние значения ползунка упирались
    # в полный буфер и не меняли ничего.
    "spark_density": (0.15, 8.0),
    "spark_glow_start": (0.0, 1.0),
    "spark_glow_end": (0.0, 1.0),
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
STR_KEYS = {"static_color", "color_source_gpu", "color_source_ble"}


def _read_cfg_file():
    """Прочитать конфиг: сначала основной файл, при неудаче — копию .bak.

    Основной файл однажды оказался забит нулями, и приложение из-за
    этого не стартовало. Теперь битый файл не смертелен: берёмся за
    резервную копию, а если и она недоступна — стартуем с настройками по
    умолчанию, вместо того чтобы падать."""
    for path in (CONFIG_PATH, CONFIG_PATH + ".bak"):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("не словарь")
            return data, path
        except Exception as e:
            print(f"[config] {os.path.basename(path)} не читается: {e}")
    return {}, None


def load():
    cfg = dict(DEFAULTS)
    data = {}          # на случай, если файла нет или он битый
    try:
        data, _src = _read_cfg_file()
        if data:
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
    # Перенос старого имени. «Темнота искр» сначала была только у искр, потом
    # стала общей для всех фигур и для подсветки — ключ тоже переименован, а
    # под пользовательскими настройками лежит старое имя. Раньше это значение
    # просто пропадало бы, и параметр молча возвращался к 0.25.
    try:
        old = float(data.get("spark_dark", None))
    except (TypeError, ValueError):
        old = None
    if old is not None:
        cfg["color_dark"] = old
    # То же, но из личных списков стилей: параметр был «только у искр», значит
    # подобрал его пользователь именно там, и настройка лежит в style_settings.
    # Самое близкое к дефолту берём последним — так переносится то, что
    # меняли в последний раз.
    try:
        pairs = []
        for name, sset in (cfg.get("style_settings") or {}).items():
            if not isinstance(sset, dict) or "spark_dark" not in sset:
                continue
            try:
                v = float(sset["spark_dark"])
            except (TypeError, ValueError):
                continue
            pairs.append((0 if name == "sparks" else 1, len(pairs), v))
        if pairs:
            pairs.sort()
            cfg["color_dark"] = max(0.0, min(1.0, float(pairs[-1][2])))
    except Exception:
        pass
    return cfg


def save(cfg):
    """Записать конфиг.

    Запись идёт через временный файл и os.replace — она атомарна. Но
    20.10.2026 основной файл оказался забит нулями: приложение убили
    в момент автосохранения, и подпорки не осталось — настройки пришлось
    брать из git. Поэтому рядом лежит .bak — копия прошлого удачного
    файла. Её не трогает текущее сохранение, и если основной файл вдруг
    окажется битым, настройки поднимутся оттуда.
    """
    try:
        # копия прошлого хорошего состояния — до подмены основного файла
        try:
            if os.path.isfile(CONFIG_PATH) and os.path.getsize(CONFIG_PATH) > 0:
                with open(CONFIG_PATH, "rb") as src:
                    head = src.read(64)
                if head[:1] not in (b"\x00",):      # не пустой и не нули
                    with open(CONFIG_PATH + ".bak", "wb") as dst:
                        dst.write(open(CONFIG_PATH, "rb").read())
        except Exception:
            pass
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception as e:
        print(f"[config] save warn: {e}")
        return False
