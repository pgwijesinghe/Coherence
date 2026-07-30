"""Builds a ready-to-run LockinConfig directly from detected hardware.

No card model is hardcoded here -- every AI/AO channel the driver reports becomes a
slot in the config, and the sample rate is clamped to whatever the slowest connected
device can actually do. The goal is that plugging in any NI card, or a whole chassis
full of them, produces a working starting configuration with zero manual entry.

Multiple devices are combined into one config: every AI channel across every detected
device becomes one demodulation slot, and every AO channel becomes one output slot,
regardless of which physical card it's on. That's what lets a chassis with several
cards show up as one flat channel list instead of "pick one card" -- the acquisition
side (`nidaq_backend.py`) puts every referenced device's channels into a single
synchronized DAQmx task.

Defaults are intentionally conservative and inert:
  - Only the first AI channel starts enabled (so there's something to look at
    immediately), the rest are visible but off until you opt in.
  - Every AO channel starts disabled -- auto-generating a reference signal onto a
    physical output without the user asking for it is the wrong default.
  - The placeholder frequency assigned to every channel is chosen to already sit
    exactly on an FFT bin (see `_placeholder_frequency`), so a fresh autoconfigured
    setup never starts out already violating the coherent-sampling requirement --
    even before the user dials in their real frequency in Configure.
"""

from __future__ import annotations

from coherence.config import (
    AcquisitionConfig,
    AOChannelConfig,
    ChannelConfig,
    LockinConfig,
    default_config,
)
from coherence.daq.discovery import DeviceSummary

DEFAULT_SAMPLE_RATE_HZ = 51_200.0
DEFAULT_BLOCK_SIZE = 2048


def _placeholder_frequency(sample_rate_hz: float, block_size: int) -> float:
    """An arbitrary but always-coherent starting frequency: comfortably inside
    Nyquist (1/8 of the sample rate) and, by construction, an exact integer bin."""
    bin_index = max(1, block_size // 8)
    return sample_rate_hz * bin_index / block_size


def _default_ao_amplitude(voltage_range: tuple[float, float] | None) -> float:
    if not voltage_range:
        return 1.0
    lo, hi = voltage_range
    return round(0.5 * min(abs(lo), abs(hi)), 3) or 1.0


def autoconfigure(
    devices: list[DeviceSummary],
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> LockinConfig:
    """Build a full config spanning every AI-capable device passed in, or fall back
    to the built-in simulated demo config if none has any AI channels."""
    ai_capable = [d for d in devices if d.ai_channel_names]
    if not ai_capable:
        return default_config()

    ai_channels = tuple(name for d in ai_capable for name in d.ai_channel_names)

    # The whole acquisition runs at one shared rate, so it's bounded by whichever
    # participating device is slowest -- not just the target default.
    device_max_rates = [d.ai_max_multi_chan_rate_hz for d in ai_capable if d.ai_max_multi_chan_rate_hz]
    fs = min([sample_rate_hz, *device_max_rates]) if device_max_rates else sample_rate_hz

    acq = AcquisitionConfig(
        sample_rate_hz=fs,
        block_size=block_size,
        overlap_fraction=0.5,
        window="blackmanharris",
        ai_channels=ai_channels,
        input_range_v=10.0,
        simulated=False,
    )

    placeholder_freq = _placeholder_frequency(fs, block_size)

    channels = [
        ChannelConfig(name=f"AI{i}", frequency_hz=placeholder_freq, input_channel=i, enabled=(i == 0))
        for i in range(len(ai_channels))
    ]

    # AO channels are harvested from every detected device, not just the AI-capable
    # ones -- an AO-only module (a 4463, say) sharing the chassis should still be
    # usable as a reference/stimulus output even though it contributes no AI.
    ao_channels = [
        AOChannelConfig(
            name=f"AO{i}",
            frequency_hz=placeholder_freq,
            ao_channel=name,
            amplitude_v=_default_ao_amplitude(d.ao_voltage_range),
            enabled=False,
        )
        for i, (d, name) in enumerate((d, name) for d in devices for name in d.ao_channel_names)
    ]

    return LockinConfig(acquisition=acq, channels=channels, ao_channels=ao_channels)
