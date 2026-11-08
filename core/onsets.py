"""Атаки по полосам спектра: где и насколько сильно ударило прямо сейчас.

Искрам нужно две вещи, и обе по каждой полосе отдельно:

    energy — сколько звука в полосе сейчас, относительно общей громкости
             трека. Ровный звук (пэд, тянущаяся нота) даёт ровный поток.
    onset  — насколько резко полоса выросла в этом кадре звука. Удар бочки
             даёт всплеск слева, хэт — справа, а тянущийся аккорд не даёт
             ничего, сколько бы громким он ни был.

Атака — это положительный прирост полосы от прошлого кадра (spectral flux,
точнее SuperFlux: от максимума соседних полос, см. update),
выше её собственного адаптивного порога: среднее + 2.2 σ прироста за
последние ~0.6 с. В статистику прирост попадает обрезанным по порогу:
иначе сами удары раздували бы σ, и на ровном ритме бочка поднимала себе
порог выше собственной высоты. Порог у каждой полосы свой, поэтому слабые по уровню
верха (хэты) ловятся так же, как бас, а дрожание громкой полосы не
считается ударом.

Громкость меряется не по текущему пику, а по медленной опоре: растёт за ~8 с,
спадает за ~30 с. Поэтому тихий куплет остаётся тихим, а не растягивается
до полной высоты, и припев после него заметно сильнее.

Модуль без Qt: его проверяют тесты в CI.
"""
import math

import numpy as np


class BandOnsets:
    # Постоянные времени, секунды
    TAU_STATS = 0.6       # окно статистики прироста для порога
    TAU_UP = 8.0          # опора громкости растёт
    TAU_DOWN = 30.0       # и спадает
    TAU_BAND = 3.0        # опора уровня самой полосы (спад)
    K_SIGMA = 2.2         # порог: среднее + K σ прироста
    DEAD = 0.15           # атаки слабее — шум, а не удар
    REF_MIN = 0.5         # опора громкости не опускается ниже: в тишине
    REF_INIT = 2.0        # не раздувается шум. Стартовая — типичная музыка.

    def __init__(self, n):
        self.n = int(n)
        self.prev = None
        self.mu = np.zeros(self.n, np.float32)
        self.var = np.zeros(self.n, np.float32)
        self.band_ref = np.zeros(self.n, np.float32)
        self.ref = self.REF_INIT
        self.energy = np.zeros(self.n, np.float32)
        self.onset = np.zeros(self.n, np.float32)
        self.frames = 0

    @staticmethod
    def _k(dt, tau):
        return 1.0 - math.exp(-max(dt, 1e-4) / tau)

    def update(self, x, dt, fresh=True):
        """Новый кадр спектра x (n полос, >= 0) спустя dt секунд.

        fresh=False — звук с прошлого вызова не обновлялся: энергия та же,
        атак нет (иначе один и тот же удар считался бы дважды).
        Возвращает (energy, onset), оба массива длины n.
        """
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if len(x) != self.n:
            self.__init__(len(x))
        if not fresh:
            self.onset = np.zeros(self.n, np.float32)
            return self.energy, self.onset
        if self.prev is None:
            self.prev = x.copy()
            self.band_ref = np.maximum(x, 0.05).astype(np.float32)
            self.energy = np.clip(x / self.ref, 0.0, 1.6).astype(np.float32)
            self.onset = np.zeros(self.n, np.float32)
            return self.energy, self.onset
        self.frames += 1

        # --- общая громкость: медленная опора ---
        peak = float(np.percentile(x, 97)) if self.n >= 8 else float(x.max())
        tau = self.TAU_UP if peak > self.ref else self.TAU_DOWN
        self.ref += (peak - self.ref) * self._k(dt, tau)
        self.ref = max(self.REF_MIN, self.ref)
        ref = self.ref

        # --- опора уровня каждой полосы: мгновенно вверх, медленно вниз ---
        kb = self._k(dt, self.TAU_BAND)
        br = self.band_ref
        br = np.where(x > br, x, br + (x - br) * kb)
        # не ниже доли общей опоры: иначе полоса, где почти пусто, считала бы
        # ударом любое шевеление шума
        br = np.maximum(br, 0.12 * ref).astype(np.float32)
        self.band_ref = br

        # --- прирост и порог ---
        # Прирост считаем от максимума СОСЕДНИХ полос прошлого кадра
        # (SuperFlux): тон, который плавно едет по частоте (свип, вибрато,
        # глиссандо), переходит в соседнюю полосу и ударом не считается.
        # Настоящий удар поднимает полосы, которые до этого молчали все.
        pv = self.prev
        if self.n >= 3:
            pv = np.maximum(pv, np.maximum(np.r_[pv[1:], pv[-1]], np.r_[pv[0], pv[:-1]]))
        flux = np.maximum(x - pv, 0.0).astype(np.float32)
        self.prev = x.copy()
        thr = self.mu + self.K_SIGMA * np.sqrt(np.maximum(self.var, 0.0)) + 0.06 * br
        over = np.maximum(flux - thr, 0.0)
        # Сила атаки: прирост сверх порога в долях уровня самой полосы,
        # умноженный на то, насколько полоса громкая на фоне всего трека.
        # Первое уравнивает тихие верха с басом, второе возвращает разницу
        # между тихим куплетом и громким припевом.
        loud = np.clip(x / ref, 0.0, 1.6)
        o = over / (0.5 * br) * (0.25 + 0.75 * np.minimum(loud, 1.2))
        # мёртвая зона: шум даёт россыпь крошечных «атак» по всем полосам,
        # которые вместе складывались бы в постоянный дождь искр
        self.onset = np.clip(o - self.DEAD, 0.0, 2.0).astype(np.float32)
        # статистику обновляем ПОСЛЕ сравнения, чтобы удар не поднял себе
        # порог раньше, чем был пойман
        ks = self._k(dt, self.TAU_STATS)
        d = np.minimum(flux, thr) - self.mu
        self.mu = (self.mu + d * ks).astype(np.float32)
        self.var = ((1.0 - ks) * (self.var + ks * d * d)).astype(np.float32)

        self.energy = loud.astype(np.float32)
        return self.energy, self.onset

    def silence(self, dt):
        """Звука нет: энергия гаснет за ~0.1 с, атак нет, prev обнуляется,
        чтобы первый звук после тишины сработал как удар."""
        self.energy = (self.energy * math.exp(-max(dt, 0.0) / 0.1)).astype(np.float32)
        self.onset = np.zeros(self.n, np.float32)
        if self.prev is not None:
            self.prev = np.zeros(self.n, np.float32)
        return self.energy, self.onset
