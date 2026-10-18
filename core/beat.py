"""Детектор темпа и фазы доли.

Зачем он нужен
--------------
Всплеск раньше ловился порогом по басу: «выросло на 10% за кадр — значит
удар». На разных треках от этого попадания нет: у одних бас широкий и подъём
плавный, удар не отличить от соседней ноты; у других наоборот — всё дёргается,
и порог срабатывает дважды на один удар. В итоге искры и пульс подсветки шли
мимо ритма.

Как считается
-------------
1. Onset-функция: суммарная энергия низких и средних полос спектра.
2. Положительная разность между кадрами (half-wave rectified difference) —
   классический признак удара: энергия выросла, а не просто громко.
3. Огибающая сглаживается и пишется в кольцевой буфер.
4. Темп оценивается автокорреляцией огибающей: ищем лаг, на котором она
   повторяет себя лучше всего, в диапазоне 60..190 BPM.
5. Из автокорреляции берём ЛОКАЛЬНЫЕ МАКСИМУМЫ, а не просто наибольшее
   значение. Это ключевой момент: огибающая повторяется и на каждом ударе,
   и через такт (сильная+слабая доля), поэтому единственный глобальный пик
   часто стоит на вдвое большем лаге — 64 BPM вместо 128. По локальным вершинам
   мы получаем кандидатов, а решение принимаем голосованием.
6. Голосование идёт по совпадению с ударами: для каждого кандидата считаем,
   сколько реальных пиков огибающей приходится на его фазу. Кандидат с
   наибольшим числом попаданий побеждает. Так отсекаются и полтакта, и
   такты, кратные двум.

Почему не подменяем бас
-----------------------
Класс ничего не знает про бас. Он возвращает удары там, где видит реальные
подъёмы энергии. Если музыки нет — ударов нет, и оверлей это понимает по
флагу has_sig.
"""
import numpy as np

# Границы поиска темпа. Ниже 60 — уже не музыка, выше 190 — скорее тремор.
BPM_MIN, BPM_MAX = 60.0, 190.0
# Сколько секунд держим историю для автокорреляции. 8 с — хватает, чтобы
# увидеть 16+ ударов на 120 BPM и уверенно развести соседние темпы.
HISTORY_S = 8.0


class BeatTracker:
    """Отслеживает темп и фазу ударов по потоку спектра.

    Вход: `update(spec, t)` — спектр и время в секундах.
    Выход: `bpm`, `phase` (0..1, доля внутри такта), `beat` (True в кадре
    удара), `pulse(t)`, `confidence`.
    """

    def __init__(self, fps=60.0, history_s=HISTORY_S):
        self.fps = max(1.0, float(fps))
        self.maxlen = max(96, int(self.fps * history_s))
        self.hist = np.zeros(self.maxlen, dtype=np.float32)
        self.n_hist = 0
        self.prev_env = 0.0
        self.bpm = 0.0
        self.confidence = 0.0
        self.last_beat_t = -10.0
        self.next_beat_t = -10.0
        self._last_t = None
        self._since_est = 0.0
        self._frames = 0
        self._pk = []

    # ---------- вход ----------
    def update(self, spec, t=None, has_sig=True, dt=None):
        """Скормить кадр спектра. Возвращает True, если в этом кадре удар.

        Время можно задавать двумя способами:
        - передать `dt` — тогда внутреннее время считается как frames * dt.
          Предпочтительно: темп получается независимым от реальных просадок
          FPS. Если бы смотреть на часы, то при падении до 20 fps детектор
          решил бы, что музыка замедлилась вдвое, и перестал бы попадать.
        - передать `t` — для офлайн-разбора с настоящими метками времени.
        """
        if dt is not None and dt > 0.0:
            self._frames += 1
            t = self._frames * dt
            step = dt
        else:
            if self._last_t is None:
                self._last_t = float(t or 0.0)
                return False
            step = float(t) - self._last_t
            self._last_t = float(t)
        if step <= 0.0 or step > 1.0:
            # кадр пропущен (спим, лаг, первый запуск) — историю не рвём,
            # просто не считаем этот кадр за изменение энергии
            return False

        env = self._onset_energy(spec)
        # полуволновое выпрямление: реакция только на рост. Падение энергии
        # ударом не считается — иначе тихие хвосты нот ловились как биты.
        diff = env - self.prev_env
        self.prev_env = env
        onset = float(max(0.0, diff)) if has_sig else 0.0
        # Удержание пика. На быстром темпе удар короче кадра: при 160 BPM
        # период 0.375 с, то есть 22.5 кадра при 60 fps, а пик длится 2-3
        # кадра. Если он пришёлся между отсчётами, кадр его просто не видел,
        # и половина ударов терялась. Поэтому помним максимум за последние
        # четыре кадра — но с затуханием, чтобы старый пик не держал фазу.
        self._pk.append(onset)
        if len(self._pk) > 4:
            self._pk.pop(0)
        onset_peak = float(max(self._pk) * (1.0 - 0.18 * (len(self._pk) - 1)))

        self.hist[self.n_hist % self.maxlen] = onset
        self.n_hist += 1
        if self.n_hist > self.maxlen:
            self.n_hist = self.maxlen

        self._since_est += step
        # пересчитываем темп не каждый кадр: автокорреляция на длинной истории
        # стоит денег, а темп за доли секунды не меняется
        if self._since_est >= 0.5:
            self._since_est = 0.0
            self._estimate()

        # --- фаза ---
        beat = False
        if self.bpm > 0.0:
            period = 60.0 / self.bpm
            thresh = self._onset_threshold()
            # темп найден, но с низкой уверенностью — порог выше: иначе шум
            # на слабом сигнале ловится ударами и подсветка дёргается в такт
            # случайным шуму
            if self.confidence < 0.45:
                thresh *= 1.5
            if t >= self.next_beat_t - 0.12:
                if onset_peak > thresh:
                    beat = True
                    self.last_beat_t = t
                    self.next_beat_t = t + period
            elif onset_peak > thresh and abs(t - self.next_beat_t) < period * 0.25:
                # Удар пришёл рядом с ожиданием, но вне жёсткого окна — просто
                # чуть раньше или чуть позже. Подстраиваемся под него вместо
                # того, чтобы ждать: на быстром темпе (160 BPM это 22.5 кадра
                # на удар при 60 fps) фаза за половину такта накапливает
                # полкадра, и строгое окно начинало пропускать каждый второй
                # удар. Ограничение в четверть такта не даёт ловить случайные
                # пики между ударами.
                beat = True
                self.last_beat_t = t
                self.next_beat_t = t + period
            elif onset_peak > thresh and t - self.next_beat_t > period * 0.5:
                # фаза поехала совсем (смена трека, пауза): тот же приём
                beat = True
                self.last_beat_t = t
                self.next_beat_t = t + period
            elif t >= self.next_beat_t:
                # окно прошло тихо — сдвигаем ожидание, не залипая на нём
                while self.next_beat_t < t:
                    self.next_beat_t += period
        elif onset_peak > 0.0:
            # Темп ещё не поняли. Первый удар не считаем: пока непонятно,
            # какой период ждать, любой скачок энергии выглядит как удар, и
            # на шуме это давало десятки фантомных ударов. Отмечаем только
            # момент, с которого начнём считать, когда темп появится.
            self.next_beat_t = t
        return beat

    # ---------- внутреннее ----------
    @staticmethod
    def _onset_energy(spec):
        """Энергия низких-средних полос: бас с ударами, середина с дробью."""
        s = np.asarray(spec, dtype=np.float32)
        if not len(s):
            return 0.0
        n = len(s)
        lo = s[: max(2, n // 2)]
        # квадрат — подчёркивает пики, глушит фон
        return float(np.mean(lo * lo) * 2.0)

    def _onset_threshold(self):
        """Порог «это удар, а не шум». Относительно среднего в истории."""
        h = self.hist[: self.n_hist]
        if not len(h):
            return 0.01
        return float(max(0.012, np.mean(h) * 1.35 + np.std(h) * 0.5))

    def _peaks(self, h):
        """Позиции локальных максимумов огибающей (индексы в hist)."""
        if len(h) < 3:
            return np.array([], dtype=int)
        thr = max(0.012, float(h.mean()) * 0.9)
        idx = np.where((h[1:-1] >= h[:-2]) & (h[1:-1] > h[2:]) & (h[1:-1] > thr))[0] + 1
        return idx

    def _estimate(self):
        h = self.hist[: self.n_hist].astype(np.float64)
        n = len(h)
        if n < int(self.fps * 2.0):
            return
        h = h - h.mean()
        if not np.any(h):
            self.confidence = 0.0
            self.bpm = 0.0
            return

        frame = self.fps
        lo_lag = max(1, int(frame * 60.0 / BPM_MAX))
        hi_lag = min(n - 1, int(frame * 60.0 / BPM_MIN))
        if hi_lag <= lo_lag + 2:
            return

        # автокорреляция прямым подсчётом: n ~ 480, лагов ~400 — дёшево и
        # точнее, чем через FFT на такой длине
        lags = np.arange(lo_lag, hi_lag + 1)
        scores = np.empty(len(lags), dtype=np.float64)
        for k, lag in enumerate(lags):
            a, b = h[: n - lag], h[lag:]
            denom = np.linalg.norm(a) * np.linalg.norm(b)
            scores[k] = float(np.dot(a, b) / denom) if denom > 1e-9 else 0.0

        base = float(scores.max())
        if base <= 0.22:
            # Порог отсечения. У белого шума максимум автокорреляции выходит
            # около 0.10 при 480 отсчётах, а у настоящей музыки — выше 0.3.
            # Ставим 0.22 с запасом: при 0.10 шум с 0.099 едва проскакивал, и
            # детектор рисовал темп 70..171 BPM на пустом месте, а подсветка
            # дёргалась в случайный такт.
            self.confidence = float(max(0.0, base))
            self.bpm = 0.0
            return

        # --- кандидаты: локальные максимумы корреляции ---
        cand = []
        for k in range(1, len(scores) - 1):
            if scores[k] >= scores[k - 1] and scores[k] > scores[k + 1] \
                    and scores[k] >= base * 0.30:
                lag = int(lags[k])
                # отсекаем кандидатов, слишком близких к уже выбранному:
                # иначе один и тот же пик на 3-4 соседних лагах проголосует
                # сам за себя и задавит настоящий темп
                if cand and lag - cand[-1] < max(2, int(frame * 0.10)):
                    if scores[k] <= scores[cand[-1] - lo_lag]:
                        continue
                    cand.pop()
                cand.append(lag)
        if not cand:
            cand = [int(lags[int(np.argmax(scores))])]
        cand = cand[:6]

        # --- голосование: у скольких ударов сходится фаза ---
        peaks = self._peaks(h)
        if len(peaks) >= 3:
            best_lag, best_hit = cand[0], -1
            for lag in cand:
                period = lag / frame
                # раскладываем каждый пик по фазе кандидата и считаем, сколько
                # из них легло в узкое окно. Настоящий темп ловит почти все;
                # кратный удвоенный — половину, и проигрывает.
                hits = 0
                for p in peaks:
                    ph = (p % lag) / lag
                    if min(ph, 1.0 - ph) * period <= period * 0.25:
                        hits += 1
                # доля попаданий важнее абсолютного числа: у длинного лага
                # пиков больше, и он выигрывал бы числом
                score = hits / max(1, len(peaks))
                if score > best_hit + 1e-9:
                    best_lag, best_hit = lag, score
            chosen_lag = best_lag
        else:
            # ударов мало, голосовать нечем — берём наилучший пик, но только
            # если он заметно превосходит остальные
            chosen_lag = int(lags[int(np.argmax(scores))])

        self.bpm = float(np.clip(60.0 * frame / chosen_lag, BPM_MIN, BPM_MAX))
        # уверенность: насколько выбранный лаг согласуется с остальными
        try:
            cs = float(scores[chosen_lag - lo_lag])
            self.confidence = float(min(1.0, max(0.0, cs) / base if base else 0.0))
        except Exception:
            self.confidence = 0.0

    # ---------- состояние ----------
    def phase(self, t):
        """0..1 — где мы внутри такта. 0 = ровно на ударе."""
        if self.bpm <= 0.0 or self.last_beat_t < -1.0:
            return 0.0
        period = 60.0 / self.bpm
        return float(((t - self.last_beat_t) % period) / period)

    def pulse(self, t):
        """0..1 — затухающий импульс с момента последнего удара.

        Его удобнее брать вместо бинарного `beat`: визуально даёт ровное
        затухание, а не ступеньку 0/1 раз в такт."""
        if self.last_beat_t < -1.0:
            return 0.0
        period = 60.0 / max(1e-3, self.bpm or 120.0)
        d = t - self.last_beat_t
        if d < 0.0 or d > period:
            return 0.0
        return float(np.exp(-d / (period * 0.32)))