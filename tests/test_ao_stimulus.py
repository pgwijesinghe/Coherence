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
        # "DevX/ao0" is never opened -- _device_waveform doesn't touch hardware
        channels=[AOChannelSpec(ao_channel="DevX/ao0", tones=tones, voltage_range=voltage_range)],
    )


def test_coherent_tone_builds_without_glitch_and_matches_amplitude():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator([ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)])
    waveform = gen._device_waveform(gen._channels)
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
    waveform = gen._device_waveform(gen._channels)
    step_within_buffer = waveform[1] - waveform[0]
    step_across_wrap = waveform[0] - waveform[-1]
    # both should reflect the same instantaneous slope at 1000 Hz -- not an exact match
    # (discrete steps at different phase points), but same order of magnitude, not a jump
    assert abs(step_across_wrap) < 5 * abs(step_within_buffer) + 1e-6


def test_incoherent_tone_is_rejected():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator([ToneSpec(frequency_hz=1000.3, amplitude_v=1.0)])
    with pytest.raises(ValueError, match="integer number of cycles"):
        gen._device_waveform(gen._channels)


def test_clipping_is_rejected():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator(
        [ToneSpec(frequency_hz=1000.0, amplitude_v=3.0), ToneSpec(frequency_hz=1200.0, amplitude_v=1.0)]
    )
    with pytest.raises(ValueError, match="exceeds the safe AO range"):
        gen._device_waveform(gen._channels)


def test_two_tone_composite_has_both_frequencies():
    from coherence.daq.ao_stimulus import ToneSpec

    gen = _single_channel_generator(
        [ToneSpec(frequency_hz=1000.0, amplitude_v=1.0), ToneSpec(frequency_hz=1200.0, amplitude_v=0.5)]
    )
    waveform = gen._device_waveform(gen._channels)
    spectrum = np.abs(np.fft.rfft(waveform))
    freqs = np.fft.rfftfreq(len(waveform), d=1.0 / 51_200.0)
    bin_1000 = np.argmin(np.abs(freqs - 1000.0))
    bin_1200 = np.argmin(np.abs(freqs - 1200.0))
    assert spectrum[bin_1000] > spectrum[bin_1200] > 0


def test_channels_spanning_devices_are_grouped_and_built_independently():
    """Regression test: channels on different devices used to all go through one
    combined waveform array (implying one shared task); they must be grouped by
    device and built as separate per-device waveforms instead, since DSA cards
    reject a task spanning multiple devices."""
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec

    gen = AOStimulusGenerator(
        sample_rate_hz=51_200.0,
        buffer_size=2048,
        channels=[
            AOChannelSpec(ao_channel="DevX/ao0", tones=[ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)]),
            AOChannelSpec(ao_channel="DevY/ao0", tones=[ToneSpec(frequency_hz=1200.0, amplitude_v=0.3)]),
        ],
    )

    by_device = gen._channels_by_device()
    assert set(by_device) == {"DevX", "DevY"}
    assert len(by_device["DevX"]) == 1 and len(by_device["DevY"]) == 1

    wave_x = gen._device_waveform(by_device["DevX"])
    wave_y = gen._device_waveform(by_device["DevY"])
    assert wave_x.shape == (2048,)  # one channel on this device -> 1D, not stacked
    assert wave_y.shape == (2048,)
    assert np.max(np.abs(wave_x)) == pytest.approx(1.0, rel=0.01)
    assert np.max(np.abs(wave_y)) == pytest.approx(0.3, rel=0.01)


def test_multiple_channels_on_one_device_stack_into_one_waveform():
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec

    gen = AOStimulusGenerator(
        sample_rate_hz=51_200.0,
        buffer_size=2048,
        channels=[
            AOChannelSpec(ao_channel="DevX/ao0", tones=[ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)]),
            AOChannelSpec(ao_channel="DevX/ao1", tones=[ToneSpec(frequency_hz=1200.0, amplitude_v=0.3)]),
        ],
    )
    by_device = gen._channels_by_device()
    assert set(by_device) == {"DevX"}
    waveform = gen._device_waveform(by_device["DevX"])
    assert waveform.shape == (2, 2048)


def test_requires_at_least_one_channel():
    from coherence.daq.ao_stimulus import AOStimulusGenerator

    with pytest.raises(ValueError, match="at least one AO channel"):
        AOStimulusGenerator(sample_rate_hz=51_200.0, buffer_size=2048, channels=[])


# -- multi-device task creation + sync (mocked nidaqmx -- see docs/hardware-notes.md
# for why a multi-device task can't be used directly on DSA cards) ----------------


class _FakeTaskFactory:
    """Replaces nidaqmx.Task() with a MagicMock per call, recording start() order
    across all of them so start-order (slaves first, master last) is verifiable."""

    def __init__(self):
        from unittest.mock import MagicMock

        self._MagicMock = MagicMock
        self.tasks = []
        self.start_order = []

    def __call__(self, *a, **kw):
        task = self._MagicMock(name=f"Task{len(self.tasks)}")
        task.timing = self._MagicMock()
        task.out_stream = self._MagicMock()
        task.ao_channels = self._MagicMock()
        task.triggers.start_trigger = self._MagicMock()
        index = len(self.tasks)
        task.start.side_effect = lambda idx=index: self.start_order.append(idx)
        self.tasks.append(task)
        return task


def test_multi_device_start_creates_one_task_per_device_and_syncs(monkeypatch):
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec
    from coherence.daq.discovery import DeviceSummary

    factory = _FakeTaskFactory()
    monkeypatch.setattr("coherence.daq.ao_stimulus.nidaqmx.Task", factory)
    monkeypatch.setattr(
        "coherence.daq.ao_stimulus.discovery.list_devices",
        lambda: [
            DeviceSummary(name="DevX", product_type="PXIe-4461", is_simulated=False, is_dsa=True, bus_type="PXIE"),
            DeviceSummary(name="DevY", product_type="PXIe-4461", is_simulated=False, is_dsa=True, bus_type="PXIE"),
        ],
    )

    gen = AOStimulusGenerator(
        sample_rate_hz=51_200.0,
        buffer_size=2048,
        channels=[
            AOChannelSpec(ao_channel="DevX/ao0", tones=[ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)]),
            AOChannelSpec(ao_channel="DevY/ao0", tones=[ToneSpec(frequency_hz=1200.0, amplitude_v=0.3)]),
        ],
    )
    gen.start()

    # exactly one task per device -- never one task spanning both
    assert len(factory.tasks) == 2
    for task in factory.tasks:
        assert task.ao_channels.add_ao_voltage_chan.call_count == 1

    assert any("Reference clock" in line for line in gen.sync_report)
    assert any("Sync pulse" in line for line in gen.sync_report)
    assert any("Start trigger" in line for line in gen.sync_report)

    # master (DevX, index 0) starts last, after the slave (DevY, index 1)
    assert factory.start_order == [1, 0]

    gen.stop()
    for task in factory.tasks:
        task.stop.assert_called_once()
        task.close.assert_called_once()


def test_single_device_start_needs_no_synchronization(monkeypatch):
    """One device -- no cross-task sync applies, sync_report stays empty."""
    from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec

    factory = _FakeTaskFactory()
    monkeypatch.setattr("coherence.daq.ao_stimulus.nidaqmx.Task", factory)

    gen = AOStimulusGenerator(
        sample_rate_hz=51_200.0,
        buffer_size=2048,
        channels=[AOChannelSpec(ao_channel="DevX/ao0", tones=[ToneSpec(frequency_hz=1000.0, amplitude_v=1.0)])],
    )
    gen.start()

    assert len(factory.tasks) == 1
    assert gen.sync_report == []
    gen.stop()
