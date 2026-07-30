"""Verifies the core equivalence claim: a coherent FFT bin recovers the injected
amplitude and phase of a synthetic tone, and phase read-out stays continuous
(reference-corrected) across blocks that start at different absolute sample offsets.
"""

import numpy as np
import pytest

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.dsp.fft_engine import FFTLockinEngine

FS = 204_800.0
N = 2048


def _make_engine(window="blackmanharris"):
    acq = AcquisitionConfig(sample_rate_hz=FS, block_size=N, overlap_fraction=0.0, window=window)
    channels = [
        ChannelConfig(name="CH1", frequency_hz=50_000.0, input_channel=0),
        ChannelConfig(name="CH2", frequency_hz=51_000.0, input_channel=0),
    ]
    return FFTLockinEngine(LockinConfig(acquisition=acq, channels=channels)), channels


def _tone(freq, amp, phase, start_sample, n, fs=FS):
    t = (start_sample + np.arange(n)) / fs
    return amp * np.sin(2 * np.pi * freq * t + phase)


@pytest.mark.parametrize("start_sample", [0, 1000, 123_456])
def test_recovers_amplitude_and_phase_at_any_block_offset(start_sample):
    engine, channels = _make_engine()
    amp, phase = 0.7, 1.1  # radians, arbitrary
    ch = channels[0]

    block = _tone(ch.frequency_hz, amp, phase, start_sample, N)[:, None]
    result = engine.process(block, block_start_sample=start_sample, timestamp_s=0.0)

    r = result.channels["CH1"]
    assert r.amplitude == pytest.approx(amp, rel=0.01)
    # np.sin(x) = np.cos(x - pi/2); our engine's phase convention treats the tone as
    # a complex exponential correlation, so compare via wrapped angular distance.
    phase_convention = phase - np.pi / 2
    diff = (r.phase_rad - phase_convention + np.pi) % (2 * np.pi) - np.pi
    assert diff == pytest.approx(0.0, abs=0.02)


def test_two_closely_spaced_tones_are_isolated_by_the_window():
    engine, channels = _make_engine()
    block = np.zeros((N, 1))
    block[:, 0] += _tone(50_000.0, 1.0, 0.3, 0, N)
    block[:, 0] += _tone(51_000.0, 0.05, -0.9, 0, N)  # weak neighbor, should not leak in

    result = engine.process(block, block_start_sample=0, timestamp_s=0.0)
    assert result.channels["CH1"].amplitude == pytest.approx(1.0, rel=0.02)
    assert result.channels["CH2"].amplitude == pytest.approx(0.05, rel=0.05)


def test_off_bin_tone_warns(caplog):
    acq = AcquisitionConfig(sample_rate_hz=FS, block_size=N, window="hann")
    channels = [ChannelConfig(name="CH1", frequency_hz=50_050.0, input_channel=0)]  # half a bin off
    with caplog.at_level("WARNING"):
        FFTLockinEngine(LockinConfig(acquisition=acq, channels=channels))
    assert any("off the nearest FFT bin" in msg for msg in caplog.messages)


def test_channel_above_nyquist_is_rejected_at_construction():
    """Regression test: a channel frequency above Nyquist used to compute an FFT bin
    index past the end of the rfft array and raise an opaque IndexError deep inside
    process() on the very first block -- e.g. "index 520 is out of bounds for axis 0
    with size 513" for a 1024-sample block. Must fail loudly and clearly at
    construction time instead, before any acquisition ever starts."""
    sample_rate = 1_000_000.0 * 1024 / 520  # exactly reproduces the reported bin index 520
    acq = AcquisitionConfig(sample_rate_hz=sample_rate, block_size=1024, window="hann")
    channels = [ChannelConfig(name="CH1", frequency_hz=1_000_000.0, input_channel=0)]
    with pytest.raises(ValueError, match="Nyquist"):
        FFTLockinEngine(LockinConfig(acquisition=acq, channels=channels))


def test_channel_at_exact_nyquist_bin_is_accepted():
    acq = AcquisitionConfig(sample_rate_hz=FS, block_size=N, window="hann")
    nyquist_freq = FS / 2.0  # bin N//2, the last valid rfft index
    channels = [ChannelConfig(name="CH1", frequency_hz=nyquist_freq, input_channel=0)]
    FFTLockinEngine(LockinConfig(acquisition=acq, channels=channels))  # should not raise


def test_spectra_are_decimated_but_demod_channels_update_every_block():
    """The full diagnostic spectrum only needs to refresh at UI rates (~10 Hz);
    building it on every block at the demod update rate was wasted work shipped
    through the results path. Demod amplitude/phase must still come every block."""
    engine, channels = _make_engine()  # overlap 0 -> update rate 100 Hz -> decimation 10
    block = _tone(channels[0].frequency_hz, 1.0, 0.0, 0, N)[:, None]

    results = [engine.process(block, block_start_sample=i * N, timestamp_s=0.0) for i in range(12)]

    assert all(r.channels for r in results)  # demod output never skipped
    spectra_blocks = [i for i, r in enumerate(results) if r.spectra]
    assert spectra_blocks == [0, 10]  # first block, then every 10th
