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
    def demo_block(self):
        n = self.block
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
                    mono = self.demo_block()
                    time.sleep(self.block / self.samplerate * 0.9)
                    spec = self.spectrum_from_fft(
                        mono, cfg.get("sensitivity", 1.4),
                        cfg.get("bass_gain", 1.0), cfg.get("mid_gain", 1.25),
                        cfg.get("treble_gain", 1.7))
                    lvl = float(np.clip(np.abs(mono).mean() * 8, 0, 1))
                    with self.lock:
                        if self.block != chunk:
                            self.block = chunk
                            self._bin_idx = self._make_bins(self.n_bars)
                        n = min(len(spec), len(self.spec))
                        self.spec[:n] = spec[:n]
                        self.wave = mono[::2][:1024].copy()
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
                                            frames_per_buffer=chunk, input=True,
                                            input_device_index=dev["index"])
                        ctx = {"want": want, "name": dev["name"], "nch": nch,
                               "chunk": chunk, "sr": sr}
                        self.block = chunk
                        self._bin_idx = self._make_bins(self.n_bars)
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
                    raw = stream.read(ctx["chunk"], exception_on_overflow=False)
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
                cfg2 = self._cfg()
                rms = float(np.sqrt(np.mean(x * x)))
                try:
                    bass = self._bass_level(x, ctx["chunk"], ctx["sr"])
                except Exception:
                    bass = rms
                boost = float(cfg2.get("bass_boost", 1.6))
                lvl = max(rms, bass * boost)
                spec = self.spectrum_from_fft(
                    x, cfg2.get("sensitivity", 1.4),
                    cfg2.get("bass_gain", 1.0), cfg2.get("mid_gain", 1.25),
                    cfg2.get("treble_gain", 1.7))
                silent = float(np.abs(x).mean()) <= float(cfg2.get("silence_thr", 0.002))
                with self.lock:
                    n = min(len(spec), len(self.spec))
                    self.spec[:n] = spec[:n]
                    self.wave = (x[::2][:1024].copy() if len(x) >= 64
                                 else np.zeros(1024, dtype=np.float32))
                    self.level = lvl
                    self.bass = bass * boost
                    self.has_signal = not silent
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
