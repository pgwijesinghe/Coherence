"""Builds a ready-to-run LockinConfig directly from detected hardware.

No card model is hardcoded here -- every AI/AO channel the driver reports becomes a
slot in the config, and the sample rate is clamped to whatever the device can
actually do. The goal is that plugging in any NI card (a 4461, a 4431, an 8-channel
card you've never used before) produces a working starting configuration with zero
manual entry, matching what you'd expect from commercial instrument software.

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


def _short_channel_name(physical_channel: str) -> str:
    """'Dev2/ai0' -> 'ai0'."""
    return physical_channel.split("/", 1)[-1]


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
    device: DeviceSummary | None,
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> LockinConfig:
    """Build a full config from one detected device, or fall back to the built-in
    simulated demo config if no hardware is connected."""
    if device is None or not device.ai_channel_names:
        return default_config()

    ai_channels = tuple(_short_channel_name(n) for n in device.ai_channel_names)
    fs = min(device.ai_max_multi_chan_rate_hz or sample_rate_hz, sample_rate_hz)

    acq = AcquisitionConfig(
        sample_rate_hz=fs,
        block_size=block_size,
        overlap_fraction=0.5,
        window="blackmanharris",
        device_name=device.name,
        ai_channels=ai_channels,
        input_range_v=10.0,
        simulated=False,
    )

    placeholder_freq = _placeholder_frequency(fs, block_size)

    channels = [
        ChannelConfig(name=f"AI{i}", frequency_hz=placeholder_freq, input_channel=i, enabled=(i == 0))
        for i in range(len(ai_channels))
    ]

    ao_amplitude = _default_ao_amplitude(device.ao_voltage_range)
    ao_channels = [
        AOChannelConfig(
            name=f"AO{i}",
            frequency_hz=placeholder_freq,
            ao_channel=_short_channel_name(name),
            amplitude_v=ao_amplitude,
            enabled=False,
        )
        for i, name in enumerate(device.ao_channel_names)
    ]

    return LockinConfig(acquisition=acq, channels=channels, ao_channels=ao_channels)
