import numpy as np
import pytest

from coherence.daq import discovery

pytestmark = pytest.mark.skipif(
    not discovery.nidaqmx_available(), reason="nidaqmx not installed on this machine"
)


def _single_channel_generator(tones, buffer_size=2048, sample_rate=51_200.0, voltage_range=(-3.5, 3.5)):
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator

    return AOStimulusGenerator(
        sample_rate_hz=sample_rate,
        buffer_size=buffer_size,
        # "DevX/ao0" is never opened -- _build_waveform doesn't touch hardware
        channels=[AOChannelSpec(ao_channel="DevX/ao0", tones=tones, voltage_range=voltage_range)],
    )


def test_coherent_tone_builds_without_glitch_and_matches_amplitude():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator([ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)])
    waveform = gen._build_waveform()
    assert waveform.shape == (2048,)
    assert waveform[0] == pytest.approx(0.0, abs=1e-9)  # sin(0) with zero phase
    assert np.max(np.abs(waveform)) == pytest.approx(1.0, rel=0.01)


def test_seamless_wrap_for_coherent_tone():
    """The whole point of the coherence requirement: buffer[-1] -> buffer[0] must
    continue the waveform smoothly, since the card regenerates this buffer forever."""
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator(
        [ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)], buffer_size=2048, sample_rate=51_200.0
    )
    waveform = gen._build_waveform()
    step_within_buffer = waveform[1] - waveform[0]
    step_across_wrap = waveform[0] - waveform[-1]
    # both should reflect the same instantaneous slope at 1000 Hz -- not an exact match
    # (discrete steps at different phase points), but same order of magnitude, not a jump
    assert abs(step_across_wrap) < 5 * abs(step_within_buffer) + 1e-6


def test_incoherent_tone_is_rejected():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator([ToneSpec(frequency_hz=1000.3, amplitude_v=1.0)])
    with pytest.raises(ValueError, match="integer number of cycles"):
        gen._build_waveform()


def test_clipping_is_rejected():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator(
        [ToneSpec(frequency_hz=1000.0, amplitude_v=3.0), ToneSpec(frequency_hz=1200.0, amplitude_v=1.0)]
    )
    with pytest.raises(ValueError, match="exceeds the safe AO range"):
        gen._build_waveform()


def test_two_tone_composite_has_both_frequencies():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator(
        [ToneSpec(frequency_hz=1000.0, amplitude_v=1.0), ToneSpec(frequency_hz=1200.0, amplitude_v=0.5)]
    )
    waveform = gen._build_waveform()
    spectrum = np.abs(np.fft.rfft(waveform))
    freqs = np.fft.rfftfreq(len(waveform), d=1.0 / 51_200.0)
    bin_1000 = np.argmin(np.abs(freqs - 1000.0))
    bin_1200 = np.argmin(np.abs(freqs - 1200.0))
    assert spectrum[bin_1000] > spectrum[bin_1200] > 0


def test_multi_channel_waveform_shape_and_independence_across_devices():
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec

    gen = AOStimulusGenerator(
        sample_rate_hz=51_200.0,
        buffer_size=2048,
        channels=[
            AOChannelSpec(ao_channel="DevX/ao0", tones=[ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)]),
            AOChannelSpec(ao_channel="DevY/ao0", tones=[ToneSpec(frequency_hz=1200.0, amplitude_v=0.3)]),
        ],
    )
    waveform = gen._build_waveform()
    assert waveform.shape == (2, 2048)
    assert np.max(np.abs(waveform[0])) == pytest.approx(1.0, rel=0.01)
    assert np.max(np.abs(waveform[1])) == pytest.approx(0.3, rel=0.01)


def test_requires_at_least_one_channel():
    from coherence.daq.ao_stimulus import AOStimulusGenerator

    with pytest.raises(ValueError, match="at least one AO channel"):
        AOStimulusGenerator(sample_rate_hz=51_200.0, buffer_size=2048, channels=[])
