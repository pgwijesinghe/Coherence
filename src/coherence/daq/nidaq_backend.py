"""NI-DAQmx backend for the PXIe-4461 (or any multi-channel simultaneous-sampling AI card).

Requires the `nidaqmx` Python package and the NI-DAQmx driver to be installed; both are
optional (see the `hardware` extra in pyproject.toml) since this module must still be
importable on a dev machine that only ever runs the simulated backend.

Two acquisition paths, chosen automatically by how many physical devices are involved:

- **One device**: a single DAQmx task, data delivered via an every-N-samples event
  callback. This is the original, heavily-verified path (see scripts/loopback_test.py) --
  left untouched.
- **More than one device**: DSA cards (4461/4462/4463, and this project's own 4431) reject
  being combined into a single multi-device task outright -- DAQmx raises "One or more
  devices do not support multidevice tasks". The only way to acquire from several of them
  together is one Task *per device*, kept sample-aligned by explicitly sharing a reference
  clock, a DSA "sync pulse" (needed because delta-sigma converters carry internal filter
  state that a shared clock alone doesn't reset), and one hardware start trigger -- then
  reading all the per-device tasks in lockstep from a single polling thread. This sequence
  is ported from a sibling project (nidaqstudio) already verified against real multi-card
  PXIe-4461 chassis hardware; see `docs/hardware-notes.md` for the full rationale.

`clock_source` / `start_trigger_source` remain available on the single-device path for an
external or non-standard timing arrangement.
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np

from coherence.config import AcquisitionConfig
from coherence.daq import discovery, sync
from coherence.daq.base import AcquisitionBackend, ChunkCallback

logger = logging.getLogger(__name__)

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration
    from nidaqmx.stream_readers import AnalogMultiChannelReader

    _NIDAQMX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the driver installed
    _NIDAQMX_AVAILABLE = False

_READ_TIMEOUT_S = 10.0


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

        self._task: "nidaqmx.Task | None" = None  # single-device path
        self._tasks: dict[str, "nidaqmx.Task"] = {}  # multi-device path, keyed by device name
        self._readers: dict[str, "AnalogMultiChannelReader"] = {}
        self._channels_by_device: dict[str, list[str]] = {}
        self._device_order: list[str] = []
        self._read_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.sync_report: list[str] = []
        """Human-readable log of what synchronization was actually applied -- reference
        clock source, sync pulse routing, start trigger -- for the multi-device path.
        Empty on the single-device path (nothing to synchronize)."""

        self._num_channels = len(self._acq.ai_channels)
        self._error_queue: queue.Queue[BaseException] = queue.Queue()

    @property
    def sample_rate_hz(self) -> float:
        return self._acq.sample_rate_hz

    @property
    def num_channels(self) -> int:
        return self._num_channels

    def _validate_against_detected_hardware(self) -> None:
        """Catch the most common misconfigurations -- a stale/wrong device name, a
        channel that doesn't exist, a sample rate no participating device can do --
        before opening a task, so the error names what's wrong instead of surfacing a
        raw DAQmx status code.

        Device names (`Dev1`, `Dev2`, `PXI1Slot2`, ...) are assigned by NI-MAX and are
        NOT portable across machines or reinstalls. Skips silently if nidaqmx can't
        enumerate anything (e.g. driver present but no permission to query devices) --
        that's not this function's job to diagnose.
        """
        devices = discovery.list_devices()
        if not devices:
            return  # discovery itself couldn't reach the driver; let task creation surface why
        by_name = {d.name: d for d in devices}

        referenced = self._acq.devices
        missing_devices = [d for d in referenced if d not in by_name]
        if missing_devices:
            available = ", ".join(f"{d.name} ({d.product_type})" for d in devices) or "none"
            raise RuntimeError(
                f"Device(s) {missing_devices} not found. Detected devices: {available}. "
                "Device names are assigned by NI-MAX and differ between machines -- "
                "rescan in the Hardware tab and rebuild the roster from what's actually connected."
            )

        for dev_name in referenced:
            device = by_name[dev_name]
            wanted = [ch for ch in self._acq.ai_channels if ch.split("/", 1)[0] == dev_name]
            unknown = [ch for ch in wanted if ch not in device.ai_channel_names]
            if unknown:
                raise RuntimeError(
                    f"{device.name} has no channel(s) {unknown}. It has: {device.ai_channel_names}."
                )

        max_rates = {
            d: by_name[d].ai_max_multi_chan_rate_hz
            for d in referenced
            if by_name[d].ai_max_multi_chan_rate_hz is not None
        }
        if max_rates:
            limiting_device, limiting_rate = min(max_rates.items(), key=lambda kv: kv[1])
            if self._acq.sample_rate_hz > limiting_rate:
                note = (
                    f" (the slowest of {len(referenced)} devices in this acquisition)"
                    if len(referenced) > 1
                    else ""
                )
                raise RuntimeError(
                    f"Sample rate {self._acq.sample_rate_hz:,.0f} Hz exceeds {limiting_device}'s "
                    f"max multi-channel AI rate of {limiting_rate:,.0f} Hz{note}. Lower the sample "
                    "rate in Configure (and re-check bin coherence for your demodulation "
                    "frequencies at the new rate)."
                )

    def start(self, on_chunk: ChunkCallback) -> None:
        if self._task is not None or self._tasks:
            raise RuntimeError("already started")

        self._validate_against_detected_hardware()

        if len(self._acq.devices) > 1:
            self._start_multi_device(on_chunk)
        else:
            self._start_single_device(on_chunk)

    # ------------------------------------------------------------------ single device
    def _start_single_device(self, on_chunk: ChunkCallback) -> None:
        task = nidaqmx.Task()
        try:
            for ai in self._acq.ai_channels:
                task.ai_channels.add_ai_voltage_chan(
                    ai,
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
                f"Failed to open {self._acq.ai_channels} at {self._acq.sample_rate_hz:,.0f} Hz: {exc}"
            ) from exc

    # ------------------------------------------------------------------ multi device
    def _start_multi_device(self, on_chunk: ChunkCallback) -> None:
        channels_by_device: dict[str, list[str]] = {}
        for ch in self._acq.ai_channels:
            channels_by_device.setdefault(ch.split("/", 1)[0], []).append(ch)

        tasks: dict[str, "nidaqmx.Task"] = {}
        try:
            for dev, chans in channels_by_device.items():
                task = nidaqmx.Task()
                tasks[dev] = task
                for ch in chans:
                    task.ai_channels.add_ai_voltage_chan(
                        ch,
                        terminal_config=TerminalConfiguration.DEFAULT,
                        min_val=-self._acq.input_range_v,
                        max_val=self._acq.input_range_v,
                    )

            for task in tasks.values():
                task.timing.cfg_samp_clk_timing(
                    rate=self._acq.sample_rate_hz,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=self._driver_buffer_samples,
                )
                task.in_stream.input_buf_size = self._driver_buffer_samples

            master_dev = self._acq.devices[0]
            device_info = {d.name: d for d in discovery.list_devices()}
            self.sync_report.append(f"Master: {master_dev} (ai task)")

            prefer_100mhz = any(
                "PXIE" in device_info[dev].bus_type.upper() for dev in tasks if dev in device_info
            )
            self.sync_report.extend(sync.apply_reference_clock(tasks, prefer_100mhz))

            if any(device_info.get(dev) and device_info[dev].is_dsa for dev in tasks):
                self.sync_report.extend(sync.apply_sync_pulse(tasks, master_dev))

            slaves = {dev: t for dev, t in tasks.items() if dev != master_dev}
            applied = 0
            for dev, task in slaves.items():
                try:
                    task.triggers.start_trigger.cfg_dig_edge_start_trig(f"/{master_dev}/ai/StartTrigger")
                    applied += 1
                except Exception as exc:
                    logger.warning("%s: start trigger routing failed: %s", dev, exc)
            if applied:
                self.sync_report.append(f"Start trigger /{master_dev}/ai/StartTrigger -> {applied} slave task(s)")

            for line in self.sync_report:
                logger.info("%s", line)

            readers = {dev: AnalogMultiChannelReader(task.in_stream) for dev, task in tasks.items()}

            # Slaves are started first -- .start() on a task with a configured digital-edge
            # start trigger just arms it and returns, it doesn't fire until the trigger
            # edge arrives. The master starts last, which is the edge that releases every
            # armed slave at the same instant.
            for dev, task in tasks.items():
                if dev != master_dev:
                    task.start()
            tasks[master_dev].start()

            self._tasks = tasks
            self._readers = readers
            self._channels_by_device = channels_by_device
            self._device_order = list(channels_by_device.keys())

            self._stop_event.clear()
            self._read_thread = threading.Thread(
                target=self._read_loop, args=(on_chunk,), name="NIDaqMultiDeviceReader", daemon=True
            )
            self._read_thread.start()
        except Exception as exc:
            for task in tasks.values():
                try:
                    task.close()
                except Exception:  # noqa: BLE001
                    pass
            self._tasks = {}
            raise RuntimeError(
                f"Failed to open multi-device acquisition across {list(channels_by_device)} "
                f"at {self._acq.sample_rate_hz:,.0f} Hz: {exc}"
            ) from exc

    def _read_loop(self, on_chunk: ChunkCallback) -> None:
        n = self._callback_chunk_size
        while not self._stop_event.is_set():
            try:
                combined = np.empty((n, self._num_channels), dtype=np.float64)
                col = 0
                for dev in self._device_order:
                    chans = self._channels_by_device[dev]
                    buf = np.empty((len(chans), n), dtype=np.float64)
                    self._readers[dev].read_many_sample(
                        buf, number_of_samples_per_channel=n, timeout=_READ_TIMEOUT_S
                    )
                    combined[:, col : col + len(chans)] = buf.T
                    col += len(chans)
                on_chunk(combined)
            except Exception as exc:  # surfaced to the pipeline thread, not swallowed
                self._error_queue.put(exc)

    def stop(self) -> None:
        self._stop_event.set()
        if self._read_thread is not None:
            self._read_thread.join(timeout=2.0)
            self._read_thread = None

        if self._task is not None:
            self._task.stop()
            self._task.close()
            self._task = None

        for dev, task in self._tasks.items():
            try:
                task.stop()
                task.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing %s task: %s", dev, exc)
        self._tasks = {}
        self._readers = {}

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
