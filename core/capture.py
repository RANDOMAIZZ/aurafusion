"""Захват экрана через DXGI Desktop Duplication (dxcam).

Раньше кадр снимался через QScreen.grabWindow — это GDI BitBlt с экрана:
~38 мс на кадр 1920x1080 (до 60 мс), и вызов из фонового потока изредка ронял
процесс целиком (Windows fatal exception 0x8001010d в crash.log, стек в
_grab_once). DXGI отдаёт тот же кадр за 1-4 мс прямо из видеокарты и
безопасен в любом потоке, потому что не трогает Qt вовсе.

Окна с WDA_EXCLUDEFROMCAPTURE (наш оверлей) в кадр DXGI не попадают.

Если dxcam не установлен или DXGI недоступен (удалённый рабочий стол,
старый драйвер), grab() возвращает None, и вызывающий берёт старый путь.
"""
import threading

import numpy as np

try:
    from PIL import Image as _Image
except Exception:          # pragma: no cover - Pillow есть в requirements
    _Image = None


class DxgiCapture:
    """Камеры DXGI по выходам. Использовать из ОДНОГО потока (поток съёмки)."""

    def __init__(self, log=None):
        self._log = log or (lambda *a: None)
        self._cams = {}
        self._last = {}
        self._outputs = None
        self.ok = True
        self.error = ""
        self._lock = threading.Lock()
        try:
            import dxcam  # noqa: F401
            self._dxcam = dxcam
        except Exception as e:
            self.ok = False
            self.error = "dxcam недоступен: %s" % e
            self._dxcam = None

    def _output_rects(self):
        """Прямоугольники выходов DXGI в координатах рабочего стола."""
        if self._outputs is None:
            rects = []
            try:
                fac = getattr(self._dxcam, "__factory")
                for i, o in enumerate(fac.outputs[0]):
                    r = o.desc.DesktopCoordinates
                    rects.append((i, int(r.left), int(r.top),
                                  int(r.right - r.left), int(r.bottom - r.top)))
            except Exception as e:
                self._log("capture: не прочитать выходы DXGI: %s" % e)
            self._outputs = rects
        return self._outputs

    def output_for(self, geom):
        """Номер выхода DXGI для экрана Qt с геометрией (x, y, w, h).

        Порядок экранов у Qt и у DXGI разный (на четырёх мониторах он
        действительно не совпадает), поэтому сопоставляем по положению на
        рабочем столе, а не по номеру."""
        x, y, w, h = geom
        best, best_d = None, None
        for i, ox, oy, ow, oh in self._output_rects():
            d = abs(ox - x) + abs(oy - y) + abs(ow - w) + abs(oh - h)
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def grab(self, geom):
        """Кадр экрана (H, W, 3) uint8 RGB или None.

        DXGI отдаёт None, если картинка с прошлого раза не менялась, — тогда
        возвращаем прошлый кадр этого выхода."""
        if not self.ok:
            return None
        idx = self.output_for(geom)
        if idx is None:
            return None
        try:
            cam = self._cams.get(idx)
            if cam is None:
                cam = self._dxcam.create(output_idx=idx, output_color="RGB")
                if cam is None:
                    raise RuntimeError("dxcam.create вернул None")
                self._cams[idx] = cam
                self._log("capture: DXGI-камера на выходе %d" % idx)
            frame = cam.grab()
        except Exception as e:
            self.ok = False
            self.error = str(e)
            self._log("capture: DXGI отказал (%s), возвращаюсь к старому захвату" % e)
            return None
        if frame is None:
            return self._last.get(idx)
        self._last[idx] = frame
        return frame


def array_to_sample(frame, region, out_w=96, out_h=54):
    """Кадр (H, W, 3) uint8 -> сэмпл float32 0..1 размером out_h x out_w.

    Сначала уменьшение в 4 раза коробочным фильтром (PIL reduce, в C — около
    миллисекунды), это повторяет прежний путь «экран -> 480x270», затем кроп
    области в долях и билинейное уменьшение до сэмпла."""
    if frame is None or _Image is None:
        return None
    h, w = int(frame.shape[0]), int(frame.shape[1])
    if w < 16 or h < 16:
        return None
    img = _Image.fromarray(np.ascontiguousarray(frame))
    f = 4 if min(w, h) >= 400 else 1
    if f > 1:
        img = img.reduce(f)
    sw, sh = img.size
    rx, ry, rw, rh = region
    x0 = min(max(0, int(sw * rx)), sw - 2)
    y0 = min(max(0, int(sh * ry)), sh - 2)
    x1 = min(sw, max(x0 + 2, int(sw * (rx + rw))))
    y1 = min(sh, max(y0 + 2, int(sh * (ry + rh))))
    small = np.asarray(img.crop((x0, y0, x1, y1)).resize(
        (out_w, out_h), _Image.BILINEAR), dtype=np.float32) / 255.0
    if small.shape != (out_h, out_w, 3):
        return None
    return small
