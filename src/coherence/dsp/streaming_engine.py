"""Continuous streaming lock-in engine: NCO mixer + running low-pass filter per
channel, with no fixed block size anywhere.

FFTLockinEngine (fft_engine.py) waits for a whole block_size-length window, takes
one windowed FFT, and reads amplitude/phase off a fixed bin -- which couples update
rate, frequency resolution, and DC-leakage rejection all to one number (block_size).
For a channel sitting at a low frequency, or a DC/level channel that needs none of
that resolution at all, block_size still gates when the first (and every next)
answer becomes available.

This engine instead gives each channel its own numerically-controlled-oscillator
mixer and cascaded Butterworth low-pass filter, run continuously: every incoming
chunk -- of any size, as small as a single DAQ callback's worth of samples -- is
mixed and filtered immediately, carrying filter state across calls. There is no
window to fill before the first answer, and no requirement that a channel's
frequency complete a whole number of cycles in anything (the coherent-sampling
condition FFT-bin extraction needs). The only thing controlling noise bandwidth is
each channel's own filter cutoff (`ChannelConfig.time_constant_s`), completely
decoupled from how often the caller chooses to read a result out.

Because the mixing reference is built from each sample's *absolute* index
(chunk_start_sample + local offset) rather than a phase carried block-to-block, the
filtered output's phase is already referenced to absolute sample 0 -- unlike the FFT
engine, no separate block-start phase correction is needed here.

For a real tone `x(t) = A*cos(2*pi*f*t + phi)`, mixing by `exp(-j*2*pi*f*t)` gives
`A/2 * [exp(j*phi) + exp(-j*(4*pi*f*t + phi))]`; the low-pass filter removes the
double-frequency term, leaving a complex baseband value `A/2 * exp(j*phi)` at
steady state. A real-coefficient low-pass filter has exactly zero phase shift at its
own DC (i.e. at the channel's own frequency, post-mix) once settled, so the only
error at steady state is filter settling and noise -- not a systematic bias.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt

from coherence.config import LockinConfig
from coherence.dsp.fft_engine import BlockResult, ChannelResult

DEFAULT_FILTER_ORDER = 4
"""24 dB/octave roll-off past the cutoff -- a common middle-ground "slope" setting
on commercial lock-ins, cascaded as second-order sections for numerical stability at
low cutoff-to-sample-rate ratios."""


@dataclass(slots=True)
class _ChannelState:
    input_channel: int
    frequency_hz: float
    sos: np.ndarray
    zi: np.ndarray
    """Filter state, shape (n_sections, 2), complex -- carried across process() calls."""


class StreamingLockinEngine:
    """Continuous per-channel IQ demodulation: NCO mixer + cascaded Butterworth
    low-pass filter, run on whatever chunk size the acquisition delivers."""

    def __init__(self, config: LockinConfig, filter_order: int = DEFAULT_FILTER_ORDER):
        self._acq = config.acquisition
        self._fs = self._acq.sample_rate_hz
        self._channels = [c for c in config.channels if c.enabled]
        if not self._channels:
            raise ValueError("at least one enabled channel is required")

        nyquist_hz = self._fs / 2.0
        self._state: dict[str, _ChannelState] = {}
        for ch in self._channels:
            if ch.time_constant_s <= 0.0:
                raise ValueError(f"channel {ch.name!r}: time_constant_s must be positive")
            cutoff_hz = 1.0 / (2.0 * np.pi * ch.time_constant_s)
            if cutoff_hz >= nyquist_hz:
                raise ValueError(
                    f"channel {ch.name!r}: time constant {ch.time_constant_s:.3g} s implies a "
                    f"{cutoff_hz:,.1f} Hz filter cutoff, which is at or above the Nyquist limit "
                    f"of {nyquist_hz:,.1f} Hz for a {self._fs:,.0f} Hz sample rate -- use a "
                    "longer time constant or a higher sample rate."
                )
            sos = butter(filter_order, cutoff_hz, btype="low", fs=self._fs, output="sos")
            zi = np.zeros((sos.shape[0], 2), dtype=np.complex128)
            self._state[ch.name] = _ChannelState(
                input_channel=ch.input_channel, frequency_hz=ch.frequency_hz, sos=sos, zi=zi
            )

    def process(self, chunk: np.ndarray, chunk_start_sample: int, timestamp_s: float) -> BlockResult:
        """chunk: shape (n_samples, n_ai_channels), raw volts. n_samples is whatever
        the caller has on hand -- there is no required length, unlike
        FFTLockinEngine.process, which requires exactly block_size samples."""
        n = chunk.shape[0]
        sample_index = chunk_start_sample + np.arange(n)
        channel_results: dict[str, ChannelResult] = {}

        for ch in self._channels:
            state = self._state[ch.name]
            column = chunk[:, state.input_channel] if chunk.ndim > 1 else chunk
            theta = 2.0 * np.pi * state.frequency_hz * sample_index / self._fs
            mixed = column * np.exp(-1j * theta)
            filtered, state.zi = sosfilt(state.sos, mixed, zi=state.zi)

            baseband = filtered[-1]
            # A real tone's energy splits evenly between +f and -f; mixing and filtering
            # recovers only the +f half, so it must be doubled to read out true amplitude
            # -- except at DC, which has no separate negative-frequency image to account
            # for (the same reason FFTLockinEngine special-cases its bin 0 the same way).
            scale = 1.0 if ch.frequency_hz == 0.0 else 2.0
            amplitude = scale * abs(baseband)
            phase = float(np.angle(baseband))
            channel_results[ch.name] = ChannelResult(
                name=ch.name,
                frequency_hz=ch.frequency_hz,
                amplitude=float(amplitude),
                phase_rad=phase,
                x=float(amplitude * np.cos(phase)),
                y=float(amplitude * np.sin(phase)),
            )

        return BlockResult(
            block_start_sample=chunk_start_sample,
            timestamp_s=timestamp_s,
            channels=channel_results,
            spectra={},
        )
