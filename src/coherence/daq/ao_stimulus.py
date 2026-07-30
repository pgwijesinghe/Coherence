"""Minimal continuous multitone AO generator, for reference-signal / loopback use.

This project is a lock-in *amplifier* (input demodulation) at heart -- this module
only exists so it can also drive the reference/stimulus signal a lock-in experiment
needs, the way a traditional lock-in's "AO/REF" section does, without pulling in a
general-purpose waveform generator.

The one-buffer-cycle + hardware-regeneration approach only produces a glitch-free,
phase-continuous waveform if every tone completes an exact integer number of cycles
within the buffer -- which is exactly the coherent-bin condition
`LockinConfig.coherence_error_hz` / `ao_coherence_error_hz` already check for on the
acquisition and generation sides respectively. Reusing `block_size` as the AO buffer
length means "coherent for demodulation" and "seamless to regenerate" are the same
condition, so satisfying one automatically satisfies the other.

Multi-device outputs: like the AI backend (see nidaq_backend.py), DSA cards reject
being combined into a single multi-device task, so channels spanning more than one
device get one task per device, synchronized via the same shared reference clock +
DSA sync pulse + start trigger sequence, mastered by the first device referenced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from coherence.daq import discovery, sync

logger = logging.getLogger(__name__)

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, RegenerationMode

    _NIDAQMX_AVAILABLE = True
except ImportError:
    _NIDAQMX_AVAILABLE = False


@dataclass(slots=True)
class ToneSpec:
    frequency_hz: float
    amplitude_v: float
    phase_rad: float = 0.0


@dataclass(slots=True)
class AOChannelSpec:
    """One physical AO channel, generating the sum of one or more tones."""

    ao_channel: str
    """Full physical channel path, e.g. 'Dev1/ao0' or 'PXI1Slot5/ao0' -- device-
    qualified so outputs can live on any card in a multi-device setup."""
    tones: list[ToneSpec] = field(default_factory=list)
    voltage_range: tuple[float, float] = (-10.0, 10.0)


class AOStimulusGenerator:
    """Continuously regenerates a fixed multitone waveform across one or more AO
    channels, possibly spanning multiple devices."""

    def __init__(
        self,
        sample_rate_hz: float,
        buffer_size: int,
        channels: list[AOChannelSpec],
    ):
        if not _NIDAQMX_AVAILABLE:
            raise RuntimeError(
                "nidaqmx is not installed (or the NI-DAQmx driver is missing). "
                "Install with `uv pip install -e .[hardware]`."
            )
        if not channels:
            raise ValueError("at least one AO channel is required")
        self._sample_rate_hz = sample_rate_hz
        self._buffer_size = buffer_size
        self._channels = channels
        self._tasks: dict[str, "nidaqmx.Task"] = {}

        self.sync_report: list[str] = []
        """Human-readable log of synchronization actually applied across devices --
        empty when everything is on one device (nothing to synchronize)."""

    def _channels_by_device(self) -> dict[str, list[AOChannelSpec]]:
        by_device: dict[str, list[AOChannelSpec]] = {}
        for spec in self._channels:
            by_device.setdefault(spec.ao_channel.split("/", 1)[0], []).append(spec)
        return by_device

    def _build_channel_waveform(self, spec: AOChannelSpec) -> np.ndarray:
        t = np.arange(self._buffer_size) / self._sample_rate_hz
        waveform = np.zeros(self._buffer_size, dtype=np.float64)
        for tone in spec.tones:
            cycles = tone.frequency_hz * self._buffer_size / self._sample_rate_hz
            if abs(cycles - round(cycles)) > 1e-6:
                raise ValueError(
                    f"{spec.ao_channel}: tone at {tone.frequency_hz} Hz does not complete an "
                    f"integer number of cycles in a {self._buffer_size}-sample buffer at "
                    f"{self._sample_rate_hz} Hz ({cycles:.4f} cycles) -- it will glitch on "
                    "each regeneration wrap. Pick a frequency that is an integer multiple "
                    "of sample_rate_hz / buffer_size."
                )
            waveform += tone.amplitude_v * np.sin(2 * np.pi * tone.frequency_hz * t + tone.phase_rad)

        peak = float(np.max(np.abs(waveform))) if self._buffer_size else 0.0
        lo, hi = spec.voltage_range
        if peak > min(abs(lo), abs(hi)):
            raise ValueError(
                f"{spec.ao_channel}: composite waveform peak {peak:.3f} V exceeds the safe "
                f"AO range {spec.voltage_range} -- reduce tone amplitude(s)."
            )
        return waveform

    def _device_waveform(self, specs: list[AOChannelSpec]) -> np.ndarray:
        """Shape (buffer_size,) for a single channel on this device, (n_channels,
        buffer_size) for multiple -- matching what nidaqmx's Task.write expects."""
        per_channel = [self._build_channel_waveform(spec) for spec in specs]
        return per_channel[0] if len(per_channel) == 1 else np.stack(per_channel, axis=0)

    def start(self) -> None:
        if self._tasks:
            raise RuntimeError("already started")

        by_device = self._channels_by_device()
        tasks: dict[str, "nidaqmx.Task"] = {}
        try:
            for dev, specs in by_device.items():
                task = nidaqmx.Task()
                tasks[dev] = task
                for spec in specs:
                    lo, hi = spec.voltage_range
                    task.ao_channels.add_ao_voltage_chan(spec.ao_channel, min_val=lo, max_val=hi)

            for task in tasks.values():
                task.timing.cfg_samp_clk_timing(
                    rate=self._sample_rate_hz,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=self._buffer_size,
                )
                task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION

            master_dev = next(iter(by_device))
            if len(tasks) > 1:
                device_info = {d.name: d for d in discovery.list_devices()}
                self.sync_report.append(f"Master: {master_dev} (ao task)")

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
                        task.triggers.start_trigger.cfg_dig_edge_start_trig(f"/{master_dev}/ao/StartTrigger")
                        applied += 1
                    except Exception as exc:  # noqa: BLE001
                        self.sync_report.append(f"{dev}: start trigger routing failed: {exc}")
                if applied:
                    self.sync_report.append(
                        f"Start trigger /{master_dev}/ao/StartTrigger -> {applied} slave task(s)"
                    )

                for line in self.sync_report:
                    logger.info("%s", line)

            for dev, specs in by_device.items():
                tasks[dev].write(self._device_waveform(specs), auto_start=False)

            # Slaves armed first, master started last -- its start is the trigger edge
            # that releases every armed slave at the same instant.
            for dev, task in tasks.items():
                if dev != master_dev:
                    task.start()
            tasks[master_dev].start()

            self._tasks = tasks
        except Exception:
            for task in tasks.values():
                try:
                    task.close()
                except Exception:  # noqa: BLE001
                    pass
            self._tasks = {}
            raise

    def stop(self) -> None:
        for task in self._tasks.values():
            task.stop()
            task.close()
        self._tasks = {}

    def __enter__(self) -> "AOStimulusGenerator":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
