"""NI-DAQmx backend for the PXIe-4461 (or any multi-channel simultaneous-sampling AI card).

Requires the `nidaqmx` Python package and the NI-DAQmx driver to be installed; both are
optional (see the `hardware` extra in pyproject.toml) since this module must still be
importable on a dev machine that only ever runs the simulated backend.

Synchronization notes (see project README section 4):
  - For multi-card setups, route a shared reference (PXI_CLK10 or a PXIe star-trigger
    clock) to every card's sample clock, and share a single start trigger, so that
    `block_start_sample` means the same absolute instant on every channel. Pass
    `clock_source` / `start_trigger_source` to wire this up via DAQmx terminal routing.
  - Keep `samples_per_channel` (the internal driver buffer) generous relative to
    `callback_chunk_size` so OS scheduling jitter doesn't cause an overrun -- Windows is
    not a hard real-time OS.
"""

from __future__ import annotations

import logging
import queue

import numpy as np

from coherence.config import AcquisitionConfig
from coherence.daq import discovery
from coherence.daq.base import AcquisitionBackend, ChunkCallback

logger = logging.getLogger(__name__)

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    _NIDAQMX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the driver installed
    _NIDAQMX_AVAILABLE = False


class NIDaqBackend(AcquisitionBackend):
    def __init__(
        self,
        acquisition: AcquisitionConfig,
        callback_chunk_size: int = 2048,
        driver_buffer_samples: int | None = None,
        clock_source: str | None = None,
        start_trigger_source: str | None = None,
    ):
        if not _NIDAQMX_AVAILABLE:
            raise RuntimeError(
                "nidaqmx is not installed (or the NI-DAQmx driver is missing). "
                "Install with `uv pip install -e .[hardware]` on a machine with the "
                "NI-DAQmx runtime, or use the simulated backend for development."
            )
        self._acq = acquisition
        self._callback_chunk_size = callback_chunk_size
        # Default to several seconds of onboard buffering, not just a small multiple of
        # the callback size. Python's GIL means our callback thread competes with the FFT
        # worker thread and the Qt GUI thread for CPU time; an occasional multi-hundred-ms
        # scheduling hiccup under load is normal, and with too little slack here that turns
        # into a hard DAQmx "application not able to keep up" read failure (-200279) that
        # kills acquisition entirely. More buffer just gives transient stalls somewhere to
        # land instead of overflowing.
        _seconds_of_buffer = 5.0
        requested_buffer = driver_buffer_samples or max(
            callback_chunk_size * 64, int(acquisition.sample_rate_hz * _seconds_of_buffer)
        )
        # DAQmx's own auto-sized buffer (picked from cfg_samp_clk_timing's samps_per_chan
        # hint) is not reliably a multiple of the callback interval on every device --
        # observed on a USB-4431, which silently rounded to a buffer size our 512-sample
        # interval didn't evenly divide, and refused to start with DAQmx error -200920.
        # Rounding up to an exact multiple here, then forcing it via in_stream.input_buf_size
        # in start(), makes the two always compatible regardless of what the driver would
        # have auto-picked.
        self._driver_buffer_samples = (
            -(-requested_buffer // callback_chunk_size) * callback_chunk_size
        )
        self._clock_source = clock_source
        self._start_trigger_source = start_trigger_source

        self._task: "nidaqmx.Task | None" = None
        self._num_channels = len(self._acq.ai_channels)
        self._error_queue: queue.Queue[BaseException] = queue.Queue()

    @property
    def sample_rate_hz(self) -> float:
        return self._acq.sample_rate_hz

    @property
    def num_channels(self) -> int:
        return self._num_channels

    def _validate_against_detected_hardware(self) -> None:
        """Catch the two most common misconfigurations -- a stale/wrong device name and
        a sample rate the device can't actually do -- before opening a task, so the error
        names what's wrong instead of surfacing a raw DAQmx status code.

        Device names (`Dev1`, `Dev2`, `PXI1Slot2`, ...) are assigned by NI-MAX and are
        NOT portable across machines or reinstalls; a config saved on one machine will
        commonly point at a device name that doesn't exist (or means something else) on
        another. Skips silently if nidaqmx can't enumerate anything (e.g. driver present
        but no permission to query devices) -- that's not this function's job to diagnose.
        """
        devices = discovery.list_devices()
        if not devices:
            return  # discovery itself couldn't reach the driver; let task creation surface why

        device = next((d for d in devices if d.name == self._acq.device_name), None)
        if device is None:
            available = ", ".join(f"{d.name} ({d.product_type})" for d in devices) or "none"
            raise RuntimeError(
                f"Device {self._acq.device_name!r} was not found. Detected devices: {available}. "
                "Device names are assigned by NI-MAX and differ between machines -- update "
                "the device name in Configure to match one of the detected devices above."
            )

        if len(self._acq.ai_channels) > len(device.ai_channel_names):
            raise RuntimeError(
                f"Configured {len(self._acq.ai_channels)} AI channels "
                f"{self._acq.ai_channels} but {device.name} only has "
                f"{len(device.ai_channel_names)}: {device.ai_channel_names}."
            )

        max_rate = device.ai_max_multi_chan_rate_hz
        if max_rate is not None and self._acq.sample_rate_hz > max_rate:
            raise RuntimeError(
                f"Sample rate {self._acq.sample_rate_hz:,.0f} Hz exceeds {device.name}'s "
                f"max multi-channel AI rate of {max_rate:,.0f} Hz. Lower the sample rate "
                "in Configure (and re-check bin coherence for your demodulation frequencies "
                "at the new rate)."
            )

    def start(self, on_chunk: ChunkCallback) -> None:
        if self._task is not None:
            raise RuntimeError("already started")

        self._validate_against_detected_hardware()

        task = nidaqmx.Task()
        try:
            for ai in self._acq.ai_channels:
                task.ai_channels.add_ai_voltage_chan(
                    f"{self._acq.device_name}/{ai}",
                    terminal_config=TerminalConfiguration.DEFAULT,
                    min_val=-self._acq.input_range_v,
                    max_val=self._acq.input_range_v,
                )

            task.timing.cfg_samp_clk_timing(
                rate=self._acq.sample_rate_hz,
                source=self._clock_source or "",
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=self._driver_buffer_samples,
            )
            # Some devices don't honor samps_per_chan above as the final buffer size --
            # force it explicitly so it's guaranteed to be the multiple of
            # callback_chunk_size we computed in __init__.
            task.in_stream.input_buf_size = self._driver_buffer_samples

            if self._start_trigger_source:
                task.triggers.start_trigger.cfg_dig_edge_start_trig(self._start_trigger_source)

            def _callback(task_handle, every_n_samples_event_type, number_of_samples, callback_data):
                try:
                    data = task.read(number_of_samples_per_channel=number_of_samples)
                    arr = np.asarray(data, dtype=np.float64)
                    if arr.ndim == 1:
                        arr = arr[:, None]
                    else:
                        arr = arr.T  # nidaqmx returns (n_channels, n_samples)
                    on_chunk(arr)
                except Exception as exc:  # surfaced to the pipeline thread, not swallowed
                    self._error_queue.put(exc)
                return 0

            task.register_every_n_samples_acquired_into_buffer_event(
                self._callback_chunk_size, _callback
            )
            task.start()
            self._task = task
        except Exception as exc:
            task.close()
            raise RuntimeError(
                f"Failed to open {self._acq.device_name!r} ({self._acq.ai_channels}) "
                f"at {self._acq.sample_rate_hz:,.0f} Hz: {exc}"
            ) from exc

    def stop(self) -> None:
        if self._task is not None:
            self._task.stop()
            self._task.close()
            self._task = None
        # Any errors observed during the run (e.g. read overruns) are reporting concerns,
        # not shutdown concerns -- log and drop rather than raise, so a prior transient
        # error can never prevent a clean stop (this previously broke closeEvent() too,
        # since Qt calls stop() from there on window close).
        for err in self.drain_errors():
            logger.warning("Unreported acquisition error at stop time: %s", err)

    def drain_errors(self) -> list[BaseException]:
        errors: list[BaseException] = []
        while True:
            try:
                errors.append(self._error_queue.get_nowait())
            except queue.Empty:
                break
        return errors
