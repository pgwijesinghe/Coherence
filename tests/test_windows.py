import numpy as np

from coherence.dsp.windows import make_window


def test_rectangular_window_is_unity_gain():
    spec = make_window("rectangular", 1024)
    assert np.allclose(spec.coefficients, 1.0)
    assert spec.coherent_gain == 1.0
    assert spec.enbw_bins == 1.0


def test_hann_window_coherent_gain_and_enbw():
    spec = make_window("hann", 2048)
    assert 0.49 < spec.coherent_gain < 0.51
    assert spec.enbw_bins == 1.5


def test_blackmanharris_has_low_sidelobes_high_enbw():
    rect = make_window("rectangular", 4096)
    bh = make_window("blackmanharris", 4096)
    assert bh.enbw_bins > rect.enbw_bins  # wider main lobe = more noise bandwidth
    assert bh.coherent_gain < rect.coherent_gain
