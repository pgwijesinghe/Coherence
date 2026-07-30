"""Window generation and the gain/bandwidth figures needed to calibrate FFT-bin lock-in output."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import windows as sp_windows

_ENBW_BINS = {
    # Equivalent noise bandwidth, expressed in units of bin spacing (fs/N).
    "rectangular": 1.0,
    "hann": 1.5,
    "hamming": 1.36,
    "blackmanharris": 2.0,
    "flattop": 3.77,
}


@dataclass(frozen=True, slots=True)
class WindowSpec:
    name: str
    coefficients: np.ndarray
    coherent_gain: float
    """Mean of the window; divides out the DC gain the window applies to a sinusoid's amplitude."""
    enbw_bins: float
    """Equivalent noise bandwidth in units of bin spacing (fs/N) -- the FFT-lock-in's 'filter order' analog."""

    def enbw_hz(self, sample_rate_hz: float, block_size: int) -> float:
        return self.enbw_bins * sample_rate_hz / block_size


def make_window(name: str, block_size: int) -> WindowSpec:
    """Build a window and its calibration constants.

    coherent_gain and enbw come from standard tables (Harris 1978) rather than being
    re-derived per instance, since the window shape (not just its length) sets them.
    """
    name = name.lower()
    if name == "rectangular":
        coeffs = np.ones(block_size, dtype=np.float64)
    elif name == "hann":
        coeffs = sp_windows.hann(block_size, sym=False)
    elif name == "hamming":
        coeffs = sp_windows.hamming(block_size, sym=False)
    elif name == "blackmanharris":
        coeffs = sp_windows.blackmanharris(block_size, sym=False)
    elif name == "flattop":
        coeffs = sp_windows.flattop(block_size, sym=False)
    else:
        raise ValueError(f"Unknown window {name!r}")

    coherent_gain = float(np.mean(coeffs))
    enbw_bins = _ENBW_BINS.get(name, float(block_size * np.sum(coeffs**2) / np.sum(coeffs) ** 2))
    return WindowSpec(name=name, coefficients=coeffs, coherent_gain=coherent_gain, enbw_bins=enbw_bins)
