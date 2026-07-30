"""FFT-based frequency-division-multiplexed lock-in core.

DSP theory recap (see project README for the full derivation): a single DFT bin

    X[k] = sum_n w[n] x[n] exp(-j*2*pi*k*n/N)

is exactly the output of one IQ (synchronous) demodulator whose low-pass filter is the
FIR window w[n] and whose integration time is N/fs. Reading K bins out of one FFT is
therefore equivalent to running K independent lock-ins, but the FFT cost (O(N log N))
is paid once regardless of K -- the point of the whole exercise. Correctness requires:

1. Coherent sampling: each tone must sit on (or extremely close to) an integer bin,
   i.e. freq * N / fs is (near) an integer. Off-bin tones suffer scalloping loss and
   phase error -- see LockinConfig.coherence_error_hz.
2. Phase-reference correction: raw bin phase is relative to the *start of the current
   block*, which slides in absolute time as blocks advance (especially with overlap).
   We remove that rotation so phase read-out is continuous and physically meaningful.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from coherence.config import LockinConfig
from coherence.dsp.windows import WindowSpec, make_window

logger = logging.getLogger(__name__)

_COHERENCE_WARN_FRACTION = 0.02
"""Warn if a tone sits more than 2% of a bin-width away from the nearest bin center."""


@dataclass(slots=True)
class ChannelResult:
    name: str
    frequency_hz: float
    amplitude: float
    """Peak amplitude, same units as the input samples (e.g. volts)."""
    phase_rad: float
    """Phase relative to the acquisition's absolute sample-0 reference, wrapped to (-pi, pi]."""
    x: float
    """In-phase component (amplitude * cos(phase))."""
    y: float
    """Quadrature component (amplitude * sin(phase))."""


@dataclass(slots=True)
class SpectrumView:
    input_channel: int
    freqs_hz: np.ndarray
    magnitude_db: np.ndarray


@dataclass(slots=True)
class BlockResult:
    block_start_sample: int
    timestamp_s: float
    channels: dict[str, ChannelResult]
    spectra: dict[int, SpectrumView]


class FFTLockinEngine:
    """Processes fixed-size acquisition blocks into per-channel amplitude/phase."""

    def __init__(self, config: LockinConfig, spectrum_max_rate_hz: float = 10.0):
        self._acq = config.acquisition
        self._channels = [c for c in config.channels if c.enabled]
        self._window: WindowSpec = make_window(self._acq.window, self._acq.block_size)
        self._fs = self._acq.sample_rate_hz
        self._n = self._acq.block_size

        # The full diagnostic spectrum is a UI nicety, not part of the lock-in output --
        # a spectrum plot repainting faster than ~10 Hz is imperceptible, so there's no
        # reason to build (and ship through the results path) a full SpectrumView on
        # every block at the demodulation update rate. Demod bins still update every block.
        self._spectrum_decimation = max(1, round(self._acq.update_rate_hz / spectrum_max_rate_hz))
        self._blocks_seen = 0

        nyquist_hz = self._fs / 2.0
        self._bin_index: dict[str, int] = {}
        for ch in self._channels:
            k = config.bin_index(ch)
            if k < 0 or k > self._n // 2:
                raise ValueError(
                    f"Channel {ch.name!r} at {ch.frequency_hz:,.1f} Hz is above the Nyquist "
                    f"limit of {nyquist_hz:,.1f} Hz for a {self._fs:,.0f} Hz sample rate "
                    f"(computed FFT bin {k}, valid range 0..{self._n // 2}). Either raise the "
                    "sample rate or lower this channel's frequency."
                )
            self._bin_index[ch.name] = k
            err = config.coherence_error_hz(ch)
            if err > _COHERENCE_WARN_FRACTION * self._acq.bin_spacing_hz:
                logger.warning(
                    "Channel %s at %.3f Hz is %.3f Hz off the nearest FFT bin "
                    "(bin spacing %.3f Hz) -- expect scalloping loss and phase error. "
                    "Choose block_size/sample_rate so frequency*block_size/sample_rate is an integer.",
                    ch.name,
                    ch.frequency_hz,
                    err,
                    self._acq.bin_spacing_hz,
                )

        self._channels_by_input: dict[int, list] = {}
        for ch in self._channels:
            self._channels_by_input.setdefault(ch.input_channel, []).append(ch)

        self._freq_axis = np.fft.rfftfreq(self._n, d=1.0 / self._fs)

    @property
    def window(self) -> WindowSpec:
        return self._window

    def process(self, block: np.ndarray, block_start_sample: int, timestamp_s: float) -> BlockResult:
        """block: shape (block_size, n_ai_channels), raw volts."""
        if block.shape[0] != self._n:
            raise ValueError(f"expected block of {self._n} samples, got {block.shape[0]}")

        channel_results: dict[str, ChannelResult] = {}
        spectra: dict[int, SpectrumView] = {}
        w = self._window.coefficients
        cg = self._window.coherent_gain
        compute_spectra = (self._blocks_seen % self._spectrum_decimation) == 0
        self._blocks_seen += 1

        for input_ch, channels in self._channels_by_input.items():
            column = block[:, input_ch] if block.ndim > 1 else block
            spectrum = np.fft.rfft(column * w)
            if compute_spectra:
                mag = np.abs(spectrum)
                with np.errstate(divide="ignore"):
                    spectra[input_ch] = SpectrumView(
                        input_channel=input_ch,
                        freqs_hz=self._freq_axis,
                        magnitude_db=20.0 * np.log10(np.maximum(mag, 1e-300) / (self._n * cg / 2.0)),
                    )

            for ch in channels:
                k = self._bin_index[ch.name]
                bin_value = spectrum[k]
                scale = 1.0 if k == 0 or k == self._n // 2 else 2.0
                amplitude = scale * abs(bin_value) / (self._n * cg)
                raw_phase = float(np.angle(bin_value))
                ref_rotation = 2.0 * np.pi * ch.frequency_hz * block_start_sample / self._fs
                phase = _wrap_to_pi(raw_phase - ref_rotation)
                channel_results[ch.name] = ChannelResult(
                    name=ch.name,
                    frequency_hz=ch.frequency_hz,
                    amplitude=float(amplitude),
                    phase_rad=phase,
                    x=float(amplitude * np.cos(phase)),
                    y=float(amplitude * np.sin(phase)),
                )

        return BlockResult(
            block_start_sample=block_start_sample,
            timestamp_s=timestamp_s,
            channels=channel_results,
            spectra=spectra,
        )


def _wrap_to_pi(angle: float) -> float:
    return float((angle + np.pi) % (2.0 * np.pi) - np.pi)
