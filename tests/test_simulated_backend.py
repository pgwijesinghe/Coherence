import threading
import time

import numpy as np

from coherence.config import AcquisitionConfig, ChannelConfig
from coherence.daq.simulated_backend import SimulatedBackend


def test_streams_chunks_of_expected_shape_and_rate():
    acq = AcquisitionConfig(sample_rate_hz=20_000.0)
    channels = [ChannelConfig(name="CH1", frequency_hz=1000.0, input_channel=0)]
    backend = SimulatedBackend(acq, channels, chunk_size=256, noise_std=0.0, animate=False)

    received = []
    lock = threading.Lock()

    def on_chunk(chunk):
        with lock:
            received.append(chunk)

    backend.start(on_chunk)
    time.sleep(0.5)
    backend.stop()

    assert len(received) > 0
    assert all(c.shape == (256, 1) for c in received)
    total_samples = sum(c.shape[0] for c in received)
    # ~0.5s at 20kHz => ~10000 samples; allow generous slack for scheduling jitter
    assert 4000 < total_samples < 16000


def test_injected_tone_has_expected_frequency_via_fft():
    acq = AcquisitionConfig(sample_rate_hz=20_000.0)
    channels = [ChannelConfig(name="CH1", frequency_hz=2000.0, input_channel=0)]
    backend = SimulatedBackend(acq, channels, chunk_size=4096, noise_std=0.0, animate=False)

    chunks = []
    got_one = threading.Event()

    def on_chunk(chunk):
        chunks.append(chunk)
        got_one.set()

    backend.start(on_chunk)
    assert got_one.wait(timeout=2.0)
    backend.stop()

    block = chunks[0][:, 0]
    spectrum = np.abs(np.fft.rfft(block))
    freqs = np.fft.rfftfreq(len(block), d=1.0 / acq.sample_rate_hz)
    peak_freq = freqs[np.argmax(spectrum)]
    assert peak_freq == freqs[np.argmin(np.abs(freqs - 2000.0))]
