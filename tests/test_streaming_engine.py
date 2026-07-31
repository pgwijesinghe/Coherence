"""Verifies the continuous streaming lock-in engine: a channel's amplitude/phase
converge correctly regardless of chunk size, a result is available on every single
call (no block-fill wait), DC channels settle independently of other channels' time
constants, and channels sharing one physical input stay isolated -- all without any
coherent-sampling (frequency-lands-on-a-bin) requirement.
"""

import numpy as np
import pytest

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.dsp.streaming_engine import StreamingLockinEngine

FS = 51_200.0


def _tone(freq, amp, phase, start_sample, n, fs=FS):
    t = (start_sample + np.arange(n)) / fs
    return amp * np.cos(2 * np.pi * freq * t + phase)


def _run(engine, signal, chunk_size):
    """Feed `signal` (1D array) through engine.process in chunks, return the last result."""
    result = None
    for start in range(0, len(signal), chunk_size):
        piece = signal[start : start + chunk_size][:, None]
        result = engine.process(piece, chunk_start_sample=start, timestamp_s=0.0)
    return result


def test_recovers_amplitude_and_phase_after_settling():
    """Deliberately off-bin frequency (not an integer number of cycles in any tidy
    block) -- the whole point of dropping the coherent-sampling requirement."""
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [ChannelConfig(name="CH1", frequency_hz=1237.7, input_channel=0, time_constant_s=0.05)]
    engine = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))

    amp, phase = 0.83, 0.6
    duration_s = 20 * 0.05  # 20 time constants -- comfortably settled
    n = int(duration_s * FS)
    signal = _tone(1237.7, amp, phase, 0, n)

    result = _run(engine, signal, chunk_size=512)
    r = result.channels["CH1"]
    assert r.amplitude == pytest.approx(amp, rel=0.02)
    diff = (r.phase_rad - phase + np.pi) % (2 * np.pi) - np.pi
    assert diff == pytest.approx(0.0, abs=0.03)


def test_result_available_on_every_call_regardless_of_chunk_size():
    """Unlike FFTLockinEngine, there's no block_size to fill first -- a channel_results
    entry must come back from the very first, tiny chunk."""
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [ChannelConfig(name="CH1", frequency_hz=1000.0, input_channel=0, time_constant_s=0.05)]
    engine = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))

    tiny_chunk = _tone(1000.0, 1.0, 0.0, 0, 32)[:, None]
    result = engine.process(tiny_chunk, chunk_start_sample=0, timestamp_s=0.0)
    assert "CH1" in result.channels  # present immediately, even if not yet settled


def test_dc_channel_settles_independently_of_a_slow_channel_sharing_the_engine():
    """A DC (0 Hz) channel with a short time constant must reach its final value in far
    fewer samples than a channel configured with a long time constant -- proving the
    two are decoupled, unlike the FFT engine where every channel waits for one shared
    block_size regardless of what it's measuring."""
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [
        ChannelConfig(name="DC", frequency_hz=0.0, input_channel=0, time_constant_s=0.005),
        ChannelConfig(name="SLOW", frequency_hz=500.0, input_channel=1, time_constant_s=0.5),
    ]
    engine = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))

    n = int(0.05 * FS)  # 50 ms: 10 time constants for DC, only 1/10 of one for SLOW
    dc_level = np.full(n, 2.5)
    slow_tone = _tone(500.0, 1.0, 0.0, 0, n)
    block = np.stack([dc_level, slow_tone], axis=1)

    result = engine.process(block, chunk_start_sample=0, timestamp_s=0.0)
    assert result.channels["DC"].amplitude == pytest.approx(2.5, rel=0.02)
    # SLOW has barely begun to settle after only 1/10 of a time constant
    assert result.channels["SLOW"].amplitude < 0.5


def test_channels_sharing_one_physical_input_stay_isolated():
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [
        ChannelConfig(name="CH1", frequency_hz=1000.0, input_channel=0, time_constant_s=0.02),
        ChannelConfig(name="CH2", frequency_hz=3000.0, input_channel=0, time_constant_s=0.02),
    ]
    engine = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))

    n = int(20 * 0.02 * FS)
    signal = _tone(1000.0, 1.0, 0.2, 0, n) + _tone(3000.0, 0.3, -0.4, 0, n)
    result = _run(engine, signal, chunk_size=256)

    assert result.channels["CH1"].amplitude == pytest.approx(1.0, rel=0.03)
    assert result.channels["CH2"].amplitude == pytest.approx(0.3, rel=0.05)


def test_state_carries_correctly_across_chunk_boundaries():
    """Processing the same signal as one large chunk vs. many tiny ones must converge
    to (approximately) the same answer -- the filter state carried in `zi` between
    process() calls must behave as one continuous filter, not restart each call."""
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [ChannelConfig(name="CH1", frequency_hz=800.0, input_channel=0, time_constant_s=0.03)]

    n = int(20 * 0.03 * FS)
    signal = _tone(800.0, 1.0, 0.5, 0, n)

    engine_whole = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))
    result_whole = engine_whole.process(signal[:, None], chunk_start_sample=0, timestamp_s=0.0)

    engine_chunked = StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))
    result_chunked = _run(engine_chunked, signal, chunk_size=37)  # deliberately awkward size

    assert result_chunked.channels["CH1"].amplitude == pytest.approx(
        result_whole.channels["CH1"].amplitude, rel=1e-6
    )
    assert result_chunked.channels["CH1"].phase_rad == pytest.approx(
        result_whole.channels["CH1"].phase_rad, abs=1e-6
    )


def test_rejects_cutoff_at_or_above_nyquist():
    acq = AcquisitionConfig(sample_rate_hz=1000.0)
    # time_constant_s so short its implied cutoff exceeds Nyquist (500 Hz)
    channels = [ChannelConfig(name="CH1", frequency_hz=100.0, input_channel=0, time_constant_s=0.0001)]
    with pytest.raises(ValueError, match="Nyquist"):
        StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))


def test_requires_at_least_one_enabled_channel():
    acq = AcquisitionConfig(sample_rate_hz=FS)
    channels = [ChannelConfig(name="CH1", frequency_hz=1000.0, input_channel=0, enabled=False)]
    with pytest.raises(ValueError, match="at least one enabled channel"):
        StreamingLockinEngine(LockinConfig(acquisition=acq, channels=channels))
