"""Exercises LockinPipeline itself (previously only covered indirectly by smoke
scripts) -- in particular, the case where the FFT worker thread hits an exception
mid-run. Before the fix, that exception silently killed the worker while
`stats.running` stayed True forever and no further blocks were ever processed,
even though the acquisition backend kept running underneath.
"""

import time

import pytest

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.core.pipeline import LockinPipeline
from coherence.daq.simulated_backend import SimulatedBackend


class _FlakyBackend(SimulatedBackend):
    """Wraps SimulatedBackend but reports a handful of recoverable errors up front,
    mimicking a DAQmx read-overrun -- acquisition itself keeps working underneath."""

    def __init__(self, *args, error_count=3, **kwargs):
        super().__init__(*args, **kwargs)
        self._errors_to_report = error_count

    def drain_errors(self):
        if self._errors_to_report > 0:
            self._errors_to_report -= 1
            return [RuntimeError("simulated DAQmx read overrun")]
        return []


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_happy_path_produces_results_and_running_stats():
    acq = AcquisitionConfig(sample_rate_hz=20_000.0, block_size=512, overlap_fraction=0.0)
    channels = [ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0)]
    config = LockinConfig(acquisition=acq, channels=channels)
    backend = SimulatedBackend(acq, channels, chunk_size=256, noise_std=0.0, animate=False)

    pipeline = LockinPipeline(config, backend)
    results = []
    pipeline.add_result_callback(results.append)
    pipeline.start()
    try:
        assert _wait_until(lambda: len(results) >= 3)
        stats = pipeline.stats
        assert stats.running is True
        assert stats.last_error is None
    finally:
        pipeline.stop()


def test_worker_exception_is_surfaced_not_silent():
    """A channel referencing an input_channel column that doesn't exist in the
    acquired block (e.g. AI channel count misconfigured vs. the channel table)
    must not silently kill the worker thread while stats.running stays True."""
    acq = AcquisitionConfig(sample_rate_hz=20_000.0, block_size=512, overlap_fraction=0.0)
    # SimulatedBackend infers num_channels from the channels list itself, so to
    # reproduce "fewer physical columns than the engine expects" we hand the
    # pipeline a channel set at construction time that differs from what the
    # backend was built for.
    backend_channels = [ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0)]
    backend = SimulatedBackend(acq, backend_channels, chunk_size=256, noise_std=0.0, animate=False)

    engine_channels = [ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0),
                       ChannelConfig(name="CH2", frequency_hz=1_200.0, input_channel=5)]
    config = LockinConfig(acquisition=acq, channels=engine_channels)

    pipeline = LockinPipeline(config, backend)
    pipeline.start()
    try:
        assert _wait_until(lambda: pipeline.stats.last_error is not None)
        stats = pipeline.stats
        assert stats.running is False
        assert "5" in stats.last_error or "index" in stats.last_error.lower() or "bounds" in stats.last_error.lower()
        # worker thread must actually be gone, not spinning silently
        assert pipeline._worker is not None
        pipeline._worker.join(timeout=1.0)
        assert not pipeline._worker.is_alive()
    finally:
        pipeline.stop()  # must not hang even though the worker already exited on its own


def test_streaming_engine_selected_by_config_produces_results_with_no_block_wait():
    """acquisition.engine="streaming" must route the pipeline through
    StreamingLockinEngine (and the small-chunk read loop) instead of FFTLockinEngine --
    verified by checking results actually arrive, since the two engines aren't
    interchangeable objects to compare directly."""
    from coherence.dsp.streaming_engine import StreamingLockinEngine

    acq = AcquisitionConfig(sample_rate_hz=20_000.0, engine="streaming")
    channels = [ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0, time_constant_s=0.02)]
    config = LockinConfig(acquisition=acq, channels=channels)
    backend = SimulatedBackend(acq, channels, chunk_size=64, noise_std=0.0, animate=False)

    pipeline = LockinPipeline(config, backend)
    assert isinstance(pipeline._engine, StreamingLockinEngine)
    results = []
    pipeline.add_result_callback(results.append)
    pipeline.start()
    try:
        assert _wait_until(lambda: len(results) >= 5)
        assert results[0].channels["CH1"].frequency_hz == 1_000.0
        stats = pipeline.stats
        assert stats.running is True
        assert stats.last_error is None
    finally:
        pipeline.stop()


def test_backend_reported_errors_are_recoverable_not_fatal():
    """A backend-level error (e.g. a DAQmx read overrun) must be counted as an
    overrun and logged, but must NOT stop the pipeline or set stats.last_error --
    the acquisition backend is still running underneath, it just lost some samples."""
    acq = AcquisitionConfig(sample_rate_hz=20_000.0, block_size=512, overlap_fraction=0.0)
    channels = [ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0)]
    config = LockinConfig(acquisition=acq, channels=channels)
    backend = _FlakyBackend(acq, channels, chunk_size=256, noise_std=0.0, animate=False, error_count=3)

    pipeline = LockinPipeline(config, backend)
    results = []
    pipeline.add_result_callback(results.append)
    pipeline.start()
    try:
        assert _wait_until(lambda: pipeline.stats.overruns >= 3)
        assert _wait_until(lambda: len(results) >= 3)  # still producing real results
        stats = pipeline.stats
        assert stats.running is True
        assert stats.last_error is None
    finally:
        pipeline.stop()
