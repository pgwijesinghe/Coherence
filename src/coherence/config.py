"""Configuration dataclasses for the FDM lock-in pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

WINDOW_CHOICES = ("blackmanharris", "hann", "hamming", "flattop", "rectangular")


@dataclass(slots=True)
class ChannelConfig:
    """One frequency-multiplexed demodulation channel."""

    name: str
    frequency_hz: float
    input_channel: int = 0
    """Physical AI channel index this tone is read back on."""
    enabled: bool = True
    color: str | None = None
    """Optional explicit plot color (hex string); auto-assigned if None."""


@dataclass(slots=True)
class AOChannelConfig:
    """One reference/stimulus tone generated continuously out of one AO channel.

    The generated waveform is one buffer of length `AcquisitionConfig.block_size`,
    regenerated forever by the DAQ hardware -- which is glitch-free only if the tone
    completes an exact integer number of cycles in that buffer, i.e. the same
    coherent-bin condition the AI demodulation side already requires. Use
    `LockinConfig.ao_coherence_error_hz` to check before starting.
    """

    name: str
    frequency_hz: float
    ao_channel: str = "Dev1/ao0"
    """Full physical AO channel path, e.g. 'Dev1/ao0' or 'PXI1Slot5/ao0'. Device-
    qualified (not a bare 'ao0') so a stimulus channel can live on any card in a
    multi-device setup, not just whichever one AI happens to be reading from."""
    amplitude_v: float = 1.0
    enabled: bool = True


@dataclass(slots=True)
class AcquisitionConfig:
    """Sampling + FFT block parameters, shared by every channel regardless of which
    physical device it's actually on."""

    sample_rate_hz: float = 204_800.0
    block_size: int = 2048
    overlap_fraction: float = 0.5
    """0 = disjoint blocks, 0.5 = 50% overlap, etc. Trades compute for update rate/latency."""
    window: str = "blackmanharris"
    ai_channels: tuple[str, ...] = ("Dev1/ai0",)
    """Full physical channel paths, e.g. ('PXI1Slot3/ai0', 'PXI1Slot3/ai1',
    'PXI1Slot5/ai0', ...). Channels from more than one device here are acquired in a
    single DAQmx task spanning all of them -- see nidaq_backend.py for what that
    relies on. `ChannelConfig.input_channel` indexes into this tuple positionally,
    same as always; it no longer implies "same device" for every entry."""
    input_range_v: float = 10.0
    simulated: bool = True

    @property
    def devices(self) -> tuple[str, ...]:
        """Unique device names referenced by ai_channels, in first-seen order --
        the first one is the synchronization master (see nidaq_backend.py)."""
        seen: list[str] = []
        for ch in self.ai_channels:
            dev = ch.split("/", 1)[0]
            if dev not in seen:
                seen.append(dev)
        return tuple(seen)

    @property
    def hop_size(self) -> int:
        hop = int(round(self.block_size * (1.0 - self.overlap_fraction)))
        return max(1, hop)

    @property
    def bin_spacing_hz(self) -> float:
        return self.sample_rate_hz / self.block_size

    @property
    def update_rate_hz(self) -> float:
        return self.sample_rate_hz / self.hop_size

    @property
    def block_duration_s(self) -> float:
        return self.block_size / self.sample_rate_hz


@dataclass(slots=True)
class LockinConfig:
    """Top-level configuration bundle."""

    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    channels: list[ChannelConfig] = field(default_factory=list)
    ao_channels: list[AOChannelConfig] = field(default_factory=list)

    def bin_index(self, channel: ChannelConfig) -> int:
        return round(channel.frequency_hz * self.acquisition.block_size / self.acquisition.sample_rate_hz)

    def coherence_error_hz(self, channel: ChannelConfig) -> float:
        """Distance (Hz) between the channel's tone and the nearest FFT bin center.

        Should be << bin spacing for leakage-free, scallop-free extraction.
        """
        return self._coherence_error_hz(channel.frequency_hz)

    def ao_coherence_error_hz(self, ao_channel: AOChannelConfig) -> float:
        """Same check for a generated AO tone -- it must also complete an exact integer
        number of cycles in one block_size-length buffer, or hardware regeneration glitches
        at the wrap point every time the buffer loops."""
        return self._coherence_error_hz(ao_channel.frequency_hz)

    def _coherence_error_hz(self, frequency_hz: float) -> float:
        exact_bin = frequency_hz * self.acquisition.block_size / self.acquisition.sample_rate_hz
        return abs(exact_bin - round(exact_bin)) * self.acquisition.bin_spacing_hz


def effective_ai_config(config: LockinConfig) -> LockinConfig:
    """Build a config that only acquires the physical AI channels actually needed by
    enabled channels, instead of every AI channel the device has.

    Acquiring unused channels wastes real bandwidth -- every one is more data to read,
    copy, and push through the ring buffer on every single callback, which matters a
    lot on real hardware where the DAQmx callback thread is competing with the FFT
    worker and the Qt GUI thread for the GIL. There's no "enable a channel live
    without a restart" capability lost by narrowing this down: toggling which channels
    are enabled already requires stopping and restarting acquisition, since the FFT
    engine's channel grouping is fixed at construction time.

    Channel *names* are preserved unchanged (only `input_channel` is remapped), so
    results still line up with whatever the UI's tables were built from -- disabled
    channels just never receive an update, exactly as before this existed.
    """
    enabled = [c for c in config.channels if c.enabled]
    physical = config.acquisition.ai_channels

    needed_indices = sorted({c.input_channel for c in enabled})
    new_ai_channels = tuple(physical[i] for i in needed_indices)
    remap = {old_idx: new_idx for new_idx, old_idx in enumerate(needed_indices)}

    new_channels = [
        ChannelConfig(
            name=c.name,
            frequency_hz=c.frequency_hz,
            input_channel=remap[c.input_channel],
            enabled=True,
            color=c.color,
        )
        for c in enabled
    ]

    new_acquisition = replace(config.acquisition, ai_channels=new_ai_channels)
    return LockinConfig(acquisition=new_acquisition, channels=new_channels, ao_channels=config.ao_channels)


def default_config() -> LockinConfig:
    """A representative 3-channel demo config: 50/51/52 kHz tones at 204.8 kS/s."""
    acq = AcquisitionConfig(
        sample_rate_hz=204_800.0,
        block_size=2048,
        overlap_fraction=0.5,
        window="blackmanharris",
        ai_channels=("Dev1/ai0", "Dev1/ai1", "Dev1/ai2"),
        simulated=True,
    )
    channels = [
        ChannelConfig(name="CH1", frequency_hz=50_000.0, input_channel=0),
        ChannelConfig(name="CH2", frequency_hz=51_000.0, input_channel=1),
        ChannelConfig(name="CH3", frequency_hz=52_000.0, input_channel=2),
    ]
    return LockinConfig(acquisition=acq, channels=channels)
