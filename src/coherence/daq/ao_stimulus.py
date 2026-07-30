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
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

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
    """Physical channel, e.g. 'ao0'."""
    tones: list[ToneSpec] = field(default_factory=list)
    voltage_range: tuple[float, float] = (-10.0, 10.0)


class AOStimulusGenerator:
    """Continuously regenerates a fixed multitone waveform across one or more AO channels."""

    def __init__(
        self,
        device_name: str,
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
        self._device_name = device_name
        self._sample_rate_hz = sample_rate_hz
        self._buffer_size = buffer_size
        self._channels = channels
        self._task: "nidaqmx.Task | None" = None

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

    def _build_waveform(self) -> np.ndarray:
        """Shape (buffer_size,) for a single channel, (n_channels, buffer_size) for multiple --
        matching what nidaqmx's Task.write expects in each case."""
        per_channel = [self._build_channel_waveform(spec) for spec in self._channels]
        if len(per_channel) == 1:
            return per_channel[0]
        return np.stack(per_channel, axis=0)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("already started")
        waveform = self._build_waveform()

        task = nidaqmx.Task()
        try:
            for spec in self._channels:
                lo, hi = spec.voltage_range
                task.ao_channels.add_ao_voltage_chan(
                    f"{self._device_name}/{spec.ao_channel}", min_val=lo, max_val=hi
                )
            task.timing.cfg_samp_clk_timing(
                rate=self._sample_rate_hz,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=self._buffer_size,
            )
            task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION
            task.write(waveform, auto_start=False)
            task.start()
            self._task = task
        except Exception:
            task.close()
            raise

    def stop(self) -> None:
        if self._task is not None:
            self._task.stop()
            self._task.close()
            self._task = None

    def __enter__(self) -> "AOStimulusGenerator":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
