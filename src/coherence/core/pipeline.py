"""Wires an AcquisitionBackend -> RingBuffer -> FFTLockinEngine -> result callbacks.

The FFT worker thread is decoupled from the acquisition thread's chunk cadence: it
just keeps asking the ring buffer "is the next block ready yet", processes it the
moment it is, and advances by `hop_size` (< block_size when overlap is configured).
This is where the disjoint-vs-overlapping block trade-off from the design doc is
actually implemented: smaller hop = more FFTs/sec = lower latency & higher update
rate at the same ENBW, at proportionally higher CPU cost.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from coherence.config import LockinConfig
from coherence.core.ring_buffer import BufferOverrunError, RingBuffer
from coherence.daq.base import AcquisitionBackend
from coherence.dsp.fft_engine import BlockResult, FFTLockinEngine

logger = logging.getLogger(__name__)

ResultCallback = Callable[[BlockResult], None]


@dataclass(slots=True)
class PipelineStats:
    blocks_processed: int = 0
    overruns: int = 0
    measured_update_rate_hz: float = 0.0
    running: bool = False
    last_error: str | None = None
    """Set if the FFT worker thread died from an exception -- `running` goes False
    and no further blocks will ever be processed until the pipeline is restarted."""
    _last_report_t: float = field(default_factory=time.monotonic, repr=False)
    _blocks_since_report: int = field(default=0, repr=False)


class LockinPipeline:
    def __init__(
        self,
        config: LockinConfig,
        backend: AcquisitionBackend,
        ring_buffer_seconds: float = 2.0,
    ):
        self._config = config
        self._backend = backend
        self._engine = FFTLockinEngine(config)

        capacity = max(
            config.acquisition.block_size * 8,
            int(ring_buffer_seconds * config.acquisition.sample_rate_hz),
        )
        self._ring = RingBuffer(num_channels=backend.num_channels, capacity_samples=capacity)

        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._callbacks: list[ResultCallback] = []
        self._callbacks_lock = threading.Lock()
        self._stats = PipelineStats()
        self._stats_lock = threading.Lock()
        self._start_wall_time = 0.0

    @property
    def stats(self) -> PipelineStats:
        with self._stats_lock:
            return PipelineStats(
                **{
                    k: getattr(self._stats, k)
                    for k in ("blocks_processed", "overruns", "measured_update_rate_hz", "running", "last_error")
                }
            )

    def add_result_callback(self, callback: ResultCallback) -> None:
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def remove_result_callback(self, callback: ResultCallback) -> None:
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("pipeline already running")
        self._stop_event.clear()
        self._start_wall_time = time.time()
        self._backend.start(self._ring.push)
        self._worker = threading.Thread(target=self._run_worker, name="FFTLockinWorker", daemon=True)
        with self._stats_lock:
            self._stats = PipelineStats(running=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._backend.stop()
        with self._stats_lock:
            self._stats.running = False

    def _run_worker(self) -> None:
        block_size = self._config.acquisition.block_size
        hop = self._config.acquisition.hop_size
        fs = self._config.acquisition.sample_rate_hz
        read_pos = 0

        while not self._stop_event.is_set():
            for err in self._backend.drain_errors():
                # e.g. a DAQmx read-overrun ("application not able to keep up") -- the
                # backend keeps running underneath and will just have a gap in the data
                # for however long the stall lasted. Reported the same way as our own
                # ring-buffer overruns since both mean the same thing to the user: some
                # samples were lost, not that acquisition needs to be restarted.
                logger.warning("Acquisition backend reported a recoverable error: %s", err)
                with self._stats_lock:
                    self._stats.overruns += 1

            try:
                block = self._ring.try_read_block(read_pos, block_size)
            except BufferOverrunError:
                logger.warning("Ring buffer overrun; resynchronizing to latest data.")
                with self._stats_lock:
                    self._stats.overruns += 1
                read_pos = self._ring.write_pos - block_size
                continue

            if block is None:
                time.sleep(0.001)
                continue

            try:
                timestamp_s = self._start_wall_time + read_pos / fs
                result = self._engine.process(block, block_start_sample=read_pos, timestamp_s=timestamp_s)
            except Exception as exc:
                # A processing error (e.g. a channel's input_channel index doesn't exist in
                # the acquired block) will recur on every future block too -- stop cleanly and
                # surface it via `stats.last_error` rather than dying silently while `running`
                # stays True forever with no further data ever arriving.
                logger.exception("FFT worker failed processing a block -- stopping")
                with self._stats_lock:
                    self._stats.running = False
                    self._stats.last_error = str(exc)
                return

            self._dispatch(result)
            read_pos += hop
            self._update_stats()

    def _dispatch(self, result: BlockResult) -> None:
        with self._callbacks_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(result)
            except Exception:
                logger.exception("Result callback raised")

    def _update_stats(self) -> None:
        with self._stats_lock:
            self._stats.blocks_processed += 1
            self._stats._blocks_since_report += 1
            now = time.monotonic()
            elapsed = now - self._stats._last_report_t
            if elapsed >= 0.5:
                self._stats.measured_update_rate_hz = self._stats._blocks_since_report / elapsed
                self._stats._blocks_since_report = 0
                self._stats._last_report_t = now
