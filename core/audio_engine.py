"""Один общий аудио-движок на pyaudiowpatch.

Отдаёт одновременно:
  spec  — логарифмический спектр 40Гц..16кГц для оверлея (как в overlay_visualizer)
  level — общий уровень + bass (20..250Гц) для RGB-пульса (как в MusicRGB)
  wave  — осциллограмма для стиля wave
  has_signal — есть ли звук выше silence_thr
При отсутствии loopback — встроенный demo-синт (вся волна двигается).
"""
import math
import threading
import time

import numpy as np


# PortAudio (pyaudiowpatch) не потокобезопасен при concurrent Pa_Initialize:
# одновременные PyAudio() из нескольких потоков роняют процесс с 0xC0000005.
# Весь доступ к PortAudio — только через этот лок.
_PA_LOCK = threading.Lock()


def _pa_new():
    import pyaudiowpatch as pyaudio
    with _PA_LOCK:
        return pyaudio.PyAudio(), pyaudio


def _pa_terminate(p):
    with _PA_LOCK:
        try:
            p.terminate()
        except Exception:
            pass


class SharedAudio(threading.Thread):
    def __init__(self, cfg_getter, n_bars=64):
        super().__init__(daemon=True)
        self._cfg = cfg_getter  # callable -> dict
        self.samplerate = 48000
        self.block = 2048
        self.lock = threading.Lock()
        self.spec = np.zeros(n_bars, dtype=np.float32)
        self.wave = np.zeros(1024, dtype=np.float32)
        self.level = 0.0   # общий RMS*boost для пульса
        self.bass = 0.0    # бас 20..250 RMS*boost
        self.has_signal = False
        self.status = "init"
        self.active = ""      # фактически открытый loopback (может отличаться от настроенного)
        self.n_bars = n_bars
        self._bin_idx = self._make_bins(n_bars)
        self._win_cache = {}
        self._t = 0.0
        # Скользящее окно: последние chunk сэмплов. Спектр считается по
        # нему на каждом шаге hop = chunk/4, а не раз в целый chunk.
        self._ring = np.zeros(0, dtype=np.float32)
        # Номер свежего кадра звука. Оверлей тикает чаще, чем приходит звук,
        # и по номеру отличает новый спектр от того же самого, прочитанного
        # второй раз: иначе один удар считался бы дважды или терялся.
        self.frame_seq = 0
        # Автоусиление тихого звука: медленная огибающая пика спектра и
        # время её последнего обновления.
        self._agc_env = 0.0
        self._agc_t = None
        self.agc_gain = 1.0
        self.running = True

    # ---------- сетка бинов ----------
    def _make_bins(self, n_bars):
        freqs = np.fft.rfftfreq(self.block, 1 / self.samplerate)
        lo, hi = 40, 16000
        edges = np.logspace(np.log10(lo), np.log10(hi), n_bars + 1)
        idx = np.zeros((n_bars, 2), dtype=int)
        for i in range(n_bars):
            a = int(np.searchsorted(freqs, edges[i]))
            b = int(np.searchsorted(freqs, edges[i + 1]))
            b = max(b, a + 2)
            idx[i] = (a, min(b, len(freqs) - 1))
        return idx

    def set_bars(self, n):
        with self.lock:
            if n != self.n_bars:
                self.n_bars = n
                self.spec = np.zeros(n, dtype=np.float32)
                self._bin_idx = self._make_bins(n)

    # ---------- спектр ----------
    def spectrum_from_fft(self, mono, sensitivity, bass_gain, mid_gain, treble_gain):
        windowed = mono * np.hanning(len(mono))
        mag = np.abs(np.fft.rfft(windowed))
        out = np.zeros(self.n_bars, dtype=np.float32)
        for i, (a, b) in enumerate(self._bin_idx):
            if i >= len(out):  # set_bars из GUI-потока мог ужать сетку прямо сейчас
                break
            seg = mag[a:b] if b > a else mag[a:a + 1]
            out[i] = float(np.max(seg)) if len(seg) else 0.0
        out = np.log1p(out * 0.02) * float(sensitivity)
        n = len(out)
        if n >= 6:
            b1, b2 = n // 3, 2 * n // 3
            out[:b1] *= float(bass_gain)
            out[b1:b2] *= float(mid_gain)
            out[b2:] *= float(treble_gain)
        return np.clip(out, 0.0, 3.0).astype(np.float32)

    def _bass_level(self, mono, chunk, sr):
        key = (chunk, sr)
        win = self._win_cache.get(key)
        if win is None:
            win = np.hanning(chunk).astype(np.float32)
            freq = np.fft.rfftfreq(chunk, 1.0 / sr)
            band = (freq >= 20) & (freq <= 250)
            self._win_cache[key] = (win, band)
        else:
            win, band = win
        spec = np.fft.rfft(mono * win)
        return math.sqrt(2.0 * float(np.sum(np.abs(spec[band]) ** 2))) / (chunk * chunk)

    # ---------- demo-синт ----------
    @staticmethod
    def hop_of(chunk):
        """Шаг скользящего окна: четверть блока, не меньше 256 сэмплов."""
        return max(256, int(chunk) // 4)

    def demo_block(self, n=None):
        n = int(n or self.block)
        self._t += n / self.samplerate
        t = self._t + np.arange(n) / self.samplerate
        beat = 0.6 + 0.4 * np.sin(2 * np.pi * 2 * self._t)
        bass = np.sin(2 * np.pi * 55 * t) * beat
        low2 = 0.5 * np.sin(2 * np.pi * 110 * t) * beat
        mid = 0.35 * np.sin(2 * np.pi * 440 * t + 2.0 * np.sin(2 * np.pi * 0.7 * self._t))
        lead = 0.25 * np.sin(2 * np.pi * 880 * t + np.sin(2 * np.pi * 1.1 * self._t))
        shimmer = 0.18 * np.sin(2 * np.pi * 2200 * t) + 0.12 * np.sin(2 * np.pi * 5500 * t)
        gate = np.maximum(0.0, np.sin(2 * np.pi * 4 * t + self._t)) ** 8
        hats = 0.12 * np.sin(2 * np.pi * 7000 * t) * gate
        return ((bass + low2 + mid + lead + shimmer + hats) * 0.35).astype(np.float32)

    # ---------- обработка ----------
    def feed(self, x, sr, cfg=None):
        """Принять новый кусок звука (моно, float32) и пересчитать всё.

        Раньше блок в chunk сэмплов читался целиком и только потом шёл в
        FFT: при 2048 и 48 кГц спектр обновлялся раз в 43 мс, а оверлей на
        60 fps два кадра из трёх рисовал старый звук. Удар попадал в искры
        с опозданием до 43 мс плюс время самого блока, и на 16-х хэтах два
        удара могли слиться в один блок.

        Теперь читаем шагом hop = chunk/4 (~11 мс) и считаем FFT по
        скользящему окну из последних chunk сэмплов. Разрешение по частоте
        прежнее (окно той же длины), шкала спектра и уровней та же, а
        свежий спектр приходит вчетверо чаще.
        """
        cfg2 = cfg if cfg is not None else self._cfg()
        chunk = max(256, int(cfg2.get("chunk") or 2048))
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return
        ring = np.concatenate([self._ring, x])
        if len(ring) > chunk:
            ring = ring[-chunk:]
        self._ring = ring
        if len(ring) < chunk:
            # окно ещё не набрано (первые миллисекунды): дополняем нулями
            win = np.concatenate([np.zeros(chunk - len(ring), np.float32), ring])
        else:
            win = ring
        rms = float(np.sqrt(np.mean(win * win)))
        try:
            bass = self._bass_level(win, chunk, sr)
        except Exception:
            bass = rms
        boost = float(cfg2.get("bass_boost", 1.6))
        lvl = max(rms, bass * boost)
        with self.lock:
            if self.block != chunk or self.samplerate != sr:
                self.block = chunk
                self.samplerate = sr
                self._bin_idx = self._make_bins(self.n_bars)
        spec = self.spectrum_from_fft(
            win, cfg2.get("sensitivity", 1.4),
            cfg2.get("bass_gain", 1.0), cfg2.get("mid_gain", 1.25),
            cfg2.get("treble_gain", 1.7))
        # Тишина определялась по мгновенному среднему одного блока:
        # на любой паузе между нотами или в начале трека has_signal
        # на кадр обнулялся, и при auto_hide волна гасла и вспыхала
        # снова. Теперь порог двусторонний — уже набранный сигнал
        # держится, пока реально не стихнет.
        thr = float(cfg2.get("silence_thr", 0.002))
        lvl_abs = float(np.abs(win).mean())
        with self.lock:
            was = self.has_signal
            if was:
                self.has_signal = lvl_abs > thr * 0.5   # отпускаем позже
            else:
                self.has_signal = lvl_abs > thr * 1.5   # берём позже
            gain = self._agc_step(spec, cfg2, self.has_signal, float(x.size) / float(sr))
            if gain != 1.0:
                spec = np.clip(spec * gain, 0.0, 3.0).astype(np.float32)
                lvl *= gain
                bass *= gain
            if self.has_signal:
                n = min(len(spec), len(self.spec))
                self.spec[:n] = spec[:n]
            else:
                # в тишине спектр плавно гаснет, а не обрывается
                self.spec *= 0.9
            self.wave = (win[::2][:1024].copy() if len(win) >= 64
                         else np.zeros(1024, dtype=np.float32))
            self.level = lvl
            self.bass = bass * boost
            self.frame_seq += 1

    def _agc_step(self, spec, cfg, has_signal, dt):
        """Автоусиление тихого звука. Возвращает множитель для спектра и уровня.

        Спектр считается по абсолютной шкале, поэтому тихая музыка давала
        спектр около 0.06 из 3 и почти не двигала ни оверлей, ни подсветку:
        на втрое тише чувствительность падала втрое. Здесь следим за медленной
        огибающей пика спектра (быстро вверх, медленно вниз, ~8 с) и тянем
        её к целевому уровню, но не больше чем в `auto_gain_max` раз. Пока
        тихо совсем (нет сигнала), огибающая стоит и не раздувает шум.
        auto_gain_max = 1 — выключено."""
        try:
            gmax = float(cfg.get("auto_gain_max", 8.0))
        except (TypeError, ValueError):
            gmax = 8.0
        if gmax <= 1.001:
            self.agc_gain = 1.0
            return 1.0
        # время — по звуку (сколько сэмплов пришло), а не по часам: так
        # поведение не зависит от нагрузки и одинаково в тестах
        dt = max(0.0, min(0.5, float(dt)))
        if not (has_signal and len(spec)):
            # тишина: ни огибающую, ни множитель не трогаем — иначе множитель
            # уползал бы к максимуму, а первый удар после паузы уходил в
            # потолок спектра и терял резкость
            return float(self.agc_gain)
        smax = float(np.max(spec))
        env = self._agc_env
        first = env <= 0.0                         # первый звук после запуска
        tau = 0.4 if smax > env else 8.0           # вверх быстро, вниз медленно
        a = 1.0 - math.exp(-dt / tau) if dt > 0 else 1.0
        self._agc_env = smax if first else env + (smax - env) * a
        env = max(self._agc_env, 0.02)
        target = 1.0
        g = max(1.0, min(gmax, target / env))
        # плавное изменение множителя, чтобы картинка не прыгала
        # Множитель растёт медленно (~3 с), чтобы подъём не читался детектором
        # ударов как нарастание звука, и падает быстрее (~0.4 с), чтобы громкий
        # кусок не упирался в потолок спектра.
        if first:
            # начальный множитель ставим сразу: растить его три секунды значило
            # бы первые секунды трека показывать его тихим
            self.agc_gain = g
            return float(g)
        tau_g = 5.0 if g > self.agc_gain else 0.4
        step = dt if dt > 0 else 1.0 / 60.0
        self.agc_gain += (g - self.agc_gain) * (1.0 - math.exp(-step / tau_g))
        return float(self.agc_gain)

    # ---------- устройства ----------
    @staticmethod
    def list_loopbacks():
        try:
            p, _ = _pa_new()
            try:
                with _PA_LOCK:
                    return [d["name"] for d in p.get_loopback_device_info_generator()]
            finally:
                _pa_terminate(p)
        except Exception:
            return []

    # ---------- главный цикл ----------
    def run(self):
        try:
            self._run()
        except Exception:
            import traceback
            import logging
            logging.getLogger("aurafusion").error(
                "audio thread died:\n%s", traceback.format_exc())
            try:
                self.status = "поток звука упал, жду рестарт..."
            except Exception:
                pass

    def _run(self):
        p, pyaudio = _pa_new()
        stream = None
        ctx = None
        last_check = 0.0
        try:
            while self.running:
                cfg = self._cfg()
                want = str(cfg.get("audio_device") or "")
                chunk = max(256, int(cfg.get("chunk") or 2048))
                demo = bool(cfg.get("demo"))
                if demo:
                    hop = self.hop_of(chunk)
                    mono = self.demo_block(hop)
                    time.sleep(hop / self.samplerate * 0.9)
                    self.feed(mono, self.samplerate, cfg)
                    with self.lock:
                        # у демо свой уровень: синт ровный, RMS дал бы ноль
                        lvl = float(np.clip(np.abs(mono).mean() * 8, 0, 1))
                        self.level = lvl
                        self.bass = lvl
                        self.has_signal = True
                    self.status = "demo"
                    continue

                # пересоздать стрим при смене устройства/chunk
                now = time.time()
                if stream is not None and ctx is not None and now - last_check >= 2.0:
                    last_check = now
                    try:
                        with _PA_LOCK:
                            default_name = p.get_default_wasapi_loopback()["name"]
                        if not want and default_name != ctx["name"]:
                            raise RuntimeError("default changed")
                    except RuntimeError:
                        try:
                            stream.close()
                        except Exception:
                            pass
                        stream = None
                    except Exception:
                        pass  # запрос дефолта упал — стрим рабочий, не трогаем

                if stream is None or ctx is None or ctx.get("want") != want or ctx.get("chunk") != chunk:
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                    stream, ctx = None, None
                    dev = None
                    try:
                        with _PA_LOCK:
                            if want:
                                for d in p.get_loopback_device_info_generator():
                                    if d["name"] == want:
                                        dev = d
                                        break
                            if dev is None:
                                try:
                                    dev = p.get_default_wasapi_loopback()
                                except Exception:
                                    # дефолт не отдаётся (кривые имена/драйвер) —
                                    # берём первый доступный loopback
                                    cands = [d for d in p.get_loopback_device_info_generator()]
                                    dev = cands[0] if cands else None
                        if dev is None:
                            raise RuntimeError("нет loopback-устройств")
                    except Exception as e:
                        self.status = f"нет звука: {e}"
                        import logging as _lg
                        _lg.getLogger("aurafusion").warning(
                            "audio pick failed (want=%r): %s", want, e)
                        with self.lock:
                            self.has_signal = False
                        time.sleep(1.0)
                        continue
                    try:
                        sr = int(dev["defaultSampleRate"])
                        nch = dev["maxInputChannels"] or 2
                        with _PA_LOCK:
                            stream = p.open(format=pyaudio.paFloat32, channels=nch, rate=sr,
                                            frames_per_buffer=self.hop_of(chunk), input=True,
                                            input_device_index=dev["index"])
                        ctx = {"want": want, "name": dev["name"], "nch": nch,
                               "chunk": chunk, "sr": sr, "hop": self.hop_of(chunk)}
                        with self.lock:
                            self.block = chunk
                            self.samplerate = sr
                            self._bin_idx = self._make_bins(self.n_bars)
                        self._ring = np.zeros(0, dtype=np.float32)
                        self.status = f"loopback: {dev['name']}"
                        self.active = dev["name"]
                        import logging as _lg2
                        _lg2.getLogger("aurafusion").info(
                            "audio opened: %s sr=%s ch=%s chunk=%s",
                            dev["name"], sr, nch, chunk)
                    except Exception as e:
                        self.status = f"не открыл звук: {e}"
                        import logging as _lg3
                        _lg3.getLogger("aurafusion").warning("audio open failed: %s", e)
                        time.sleep(1.0)
                        continue

                try:
                    raw = stream.read(ctx["hop"], exception_on_overflow=False)
                except Exception as e:
                    self.status = f"переподключение ({e})"
                    try:
                        stream.close()
                    except Exception:
                        pass
                    stream, ctx = None, None
                    time.sleep(0.5)
                    continue

                x = np.frombuffer(raw, dtype=np.float32)
                if x.size == 0:
                    continue
                nch = ctx["nch"]
                if nch > 1 and x.size % nch == 0:
                    x = x.reshape(-1, nch).mean(axis=1)
                self.feed(x, ctx["sr"])
        finally:
            try:
                if stream is not None:
                    with _PA_LOCK:
                        stream.close()
            except Exception:
                pass
            _pa_terminate(p)

    def snapshot(self):
        with self.lock:
            return (self.spec.copy(), self.wave.copy(),
                    float(self.level), float(self.bass), bool(self.has_signal))

    def stop(self):
        self.running = False
