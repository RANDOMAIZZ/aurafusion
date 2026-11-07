import numpy as np

from core import screen_color as SC
from core.capture import array_to_sample


def test_kmeans_picks_dominant_vivid_color():
    a = np.full((54, 96, 3), 0.05, np.float32)
    a[5:50, 10:90] = [0.9, 0.1, 0.1]
    r, g, b = SC.kmeans_target(a, lock={})
    assert r > 180 and g < 60 and b < 60


def test_juicy_on_solid_color():
    a = np.zeros((54, 96, 3), np.float32)
    a[:] = [0.1, 0.2, 0.9]
    r, g, b = SC.juicy_target(a, {}, 1)
    assert b > 180 and b > r and b > g


def test_center_weights_are_cached():
    w1 = SC._center_weights(54, 96)
    w2 = SC._center_weights(54, 96)
    assert w1 is w2


def test_array_to_sample_shape_and_region():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, 960:] = [255, 0, 0]           # правая половина красная
    left = array_to_sample(frame, (0.0, 0.0, 0.5, 1.0))
    right = array_to_sample(frame, (0.5, 0.0, 0.5, 1.0))
    assert left.shape == (54, 96, 3)
    assert left.max() < 0.05
    assert right[..., 0].mean() > 0.9


def test_array_to_sample_handles_portrait_monitor():
    frame = np.full((1920, 550, 3), 128, np.uint8)
    s = array_to_sample(frame, (0, 0, 1, 1))
    assert s.shape == (54, 96, 3)
    assert abs(float(s.mean()) - 128 / 255) < 0.02
