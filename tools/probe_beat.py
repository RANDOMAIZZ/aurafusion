"""Проверка детектора темпа на синтетике с известным BPM.

Генерируем спектр, где удары идут ровно с заданным темпом, и смотрим, что
детектор находит. Заодно проверяем ложные срабатывания на шуме и на музыке
без явного ритма.

Спектр здесь — как приходит от аудио: массив полос от низких к высоким.
Удар моделируем коротким басовым пиком, который затухает за ~0.12 с.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.beat import BeatTracker      # noqa: E402

BARS = 64
FPS = 60.0


def make_track(bpm, seconds, bars=BARS, jitter=0.0, seed=0, noise=0.02):
    """Спектрограмма с ударами на заданном темпе."""
    rng = np.random.default_rng(seed)
    n = int(FPS * seconds)
    out = np.zeros((n, bars), dtype=np.float32)
    period = 60.0 / bpm
    # слабый «пол» — как у настоящей музыки между ударами
    for i in range(n):
        t = i / FPS
        floor = 0.10 + 0.04 * np.sin(t * 2.0)
        out[i] = floor + noise * rng.random(bars)
    beat_t = 0.0
    while beat_t < seconds:
        idx = int(round(beat_t * FPS))
        j = jitter * (rng.random() - 0.5)
        start = idx + int(round(j * FPS))
        # удар: бас громко, потом затухает
        for k in range(int(0.20 * FPS)):
            i = start + k
            if not (0 <= i < n):
                continue
            env = np.exp(-k / (0.045 * FPS))
            out[i] += env * 1.4
        beat_t += period
    return out


def run(bpm, seconds=14.0, jitter=0.0, noise=0.02, seed=0):
    track = make_track(bpm, seconds, jitter=jitter, seed=seed, noise=noise)
    tr = BeatTracker(fps=FPS)
    beats = []
    for i, spec in enumerate(track):
        t = i / FPS
        if tr.update(spec, t, has_sig=True):
            beats.append(t)
    # отбрасываем первые 4 с: детектору нужно набрать историю
    beats = [b for b in beats if b > 4.0]
    intervals = np.diff(beats) if len(beats) > 2 else np.array([])
    want = 60.0 / bpm
    if len(intervals):
        # отбрасываем интервалы, кратные ударам (иногда ловим две доли)
        good = intervals[(intervals > want * 0.6) & (intervals < want * 1.5)]
        err = float(np.mean(np.abs(good - want)) / want) if len(good) else 9.9
        measured = float(60.0 / np.mean(good)) if len(good) else 0.0
    else:
        err, measured = 9.9, 0.0
    return {
        "want_bpm": bpm,
        "got_bpm": tr.bpm,
        "measured_bpm": measured,
        "err_pct": err * 100.0,
        "beats": len(beats),
        "confidence": tr.confidence,
        "irregular": float(np.std(intervals) / np.mean(intervals)) if len(intervals) else 9.9,
    }


def main():
    print("=== темп на разных треках ===")
    print("  нужно    нашли    ошибка    ударов   разброс")
    cases = [(60, 0.0), (75, 0.0), (90, 0.0), (100, 0.0), (120, 0.0),
             (128, 0.0), (140, 0.0), (160, 0.0), (174, 0.0),
             (128, 0.05), (90, 0.04)]
    bad = 0
    for bpm, jit in cases:
        r = run(bpm, jitter=jit)
        ok = r["err_pct"] < 8.0
        if not ok:
            bad += 1
        print("  %5d    %6.1f   %5.1f%%   %5d   %5.1f%%   %s"
              % (r["want_bpm"], r["measured_bpm"], r["err_pct"],
                 r["beats"], r["irregular"] * 100, "" if ok else "<-- мимо"))
    print()
    print("=== без ритма: шум и ровный гул (темпа быть не должно) ===")
    rng = np.random.default_rng(7)
    n = int(FPS * 14)
    noise_track = (0.10 + 0.30 * rng.random((n, BARS))).astype(np.float32)
    flat_track = np.full((n, BARS), 0.2, dtype=np.float32)
    for tag, track in (("шум", noise_track), ("ровный", flat_track)):
        tr = BeatTracker(fps=FPS)
        beats = 0
        for i, spec in enumerate(track):
            if tr.update(spec, i / FPS, has_sig=True):
                beats += 1
        print("  %-7s ложных ударов: %d   уверенность: %.2f   bpm: %.1f"
              % (tag, beats, tr.confidence, tr.bpm))
        if beats > 12:
            bad += 1
    print()
    print("=== тишина: has_sig=False ===")
    tr = BeatTracker(fps=FPS)
    beats = sum(1 for i in range(int(FPS * 10))
                if tr.update(np.full(BARS, 0.5, np.float32), i / FPS, has_sig=False))
    print("  ложных ударов в тишине: %d" % beats)
    if beats > 0:
        bad += 1
    print()
    print("=== фаза и импульс ===")
    tr = BeatTracker(fps=FPS)
    track = make_track(120, 12.0)
    ps = []
    for i, spec in enumerate(track):
        t = i / FPS
        tr.update(spec, t)
        if t > 5.0:
            ps.append(tr.pulse(t))
    print("  импульс: максимум %.2f, средний %.2f, доля кадров с импульсом >0.3: %.0f%%"
          % (max(ps), float(np.mean(ps)),
             100.0 * sum(1 for p in ps if p > 0.3) / len(ps)))
    print()
    print("ИТОГ:", "все случаи разобраны" if bad == 0 else "проблемных случаев: %d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())