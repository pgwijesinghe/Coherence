import pytest

from coherence.daq.autoconfig import autoconfigure
from coherence.daq.discovery import DeviceSummary


def test_no_device_falls_back_to_simulated_demo():
    config = autoconfigure(None)
    assert config.acquisition.simulated is True
    assert len(config.channels) > 0


def test_autoconfigures_one_channel_per_physical_ai():
    device = DeviceSummary(
        name="Dev2",
        product_type="USB-4431",
        is_simulated=False,
        ai_channel_names=["Dev2/ai0", "Dev2/ai1", "Dev2/ai2", "Dev2/ai3"],
        ao_channel_names=["Dev2/ao0"],
        ai_max_multi_chan_rate_hz=102_400.0,
        ao_max_rate_hz=96_000.0,
        ao_voltage_range=(-3.5, 3.5),
    )
    config = autoconfigure(device)

    assert config.acquisition.device_name == "Dev2"
    assert config.acquisition.ai_channels == ("ai0", "ai1", "ai2", "ai3")
    assert len(config.channels) == 4
    assert [c.input_channel for c in config.channels] == [0, 1, 2, 3]
    # only the first channel starts enabled -- something to look at immediately,
    # everything else opt-in
    assert config.channels[0].enabled is True
    assert all(not c.enabled for c in config.channels[1:])

    assert len(config.ao_channels) == 1
    assert config.ao_channels[0].ao_channel == "ao0"
    assert config.ao_channels[0].enabled is False  # never auto-enabled


def test_placeholder_frequencies_are_coherent():
    device = DeviceSummary(
        name="Dev2", product_type="USB-4431", is_simulated=False,
        ai_channel_names=["Dev2/ai0"], ao_channel_names=["Dev2/ao0"],
        ai_max_multi_chan_rate_hz=102_400.0,
    )
    config = autoconfigure(device)
    for ch in config.channels:
        assert config.coherence_error_hz(ch) < 1e-9
    for ao in config.ao_channels:
        assert config.ao_coherence_error_hz(ao) < 1e-9


def test_sample_rate_clamped_to_device_max():
    device = DeviceSummary(
        name="Dev3", product_type="Some Slow Card", is_simulated=False,
        ai_channel_names=["Dev3/ai0"], ao_channel_names=[],
        ai_max_multi_chan_rate_hz=10_000.0,  # below the 51.2 kHz default target
    )
    config = autoconfigure(device)
    assert config.acquisition.sample_rate_hz == pytest.approx(10_000.0)


def test_sample_rate_not_pushed_to_device_max_when_max_is_high():
    """A device capable of e.g. 1 MS/s shouldn't get maxed out by default --
    51.2 kHz is a reasonable, safe starting point, not "as fast as possible"."""
    device = DeviceSummary(
        name="Dev4", product_type="Fast Card", is_simulated=False,
        ai_channel_names=["Dev4/ai0"], ao_channel_names=[],
        ai_max_multi_chan_rate_hz=1_000_000.0,
    )
    config = autoconfigure(device)
    assert config.acquisition.sample_rate_hz == pytest.approx(51_200.0)


def test_device_with_no_ai_channels_falls_back_to_simulated():
    device = DeviceSummary(name="DevX", product_type="AO-only card", is_simulated=False)
    config = autoconfigure(device)
    assert config.acquisition.simulated is True
