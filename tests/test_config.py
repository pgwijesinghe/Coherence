from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig, effective_ai_config


def test_coherent_channel_has_zero_error():
    acq = AcquisitionConfig(sample_rate_hz=204_800.0, block_size=2048)
    ch = ChannelConfig(name="CH1", frequency_hz=50_000.0)
    cfg = LockinConfig(acquisition=acq, channels=[ch])
    assert cfg.bin_index(ch) == 500
    assert cfg.coherence_error_hz(ch) < 1e-9


def test_off_bin_channel_reports_nonzero_error():
    acq = AcquisitionConfig(sample_rate_hz=204_800.0, block_size=2048)
    ch = ChannelConfig(name="CH1", frequency_hz=50_050.0)
    cfg = LockinConfig(acquisition=acq, channels=[ch])
    assert cfg.coherence_error_hz(ch) == 50.0


def test_derived_acquisition_properties():
    acq = AcquisitionConfig(sample_rate_hz=204_800.0, block_size=2048, overlap_fraction=0.5)
    assert acq.bin_spacing_hz == 100.0
    assert acq.hop_size == 1024
    assert acq.update_rate_hz == 200.0
    assert abs(acq.block_duration_s - 0.01) < 1e-12


def _four_channel_config():
    acq = AcquisitionConfig(ai_channels=("ai0", "ai1", "ai2", "ai3"))
    channels = [
        ChannelConfig(name="AI0", frequency_hz=1000.0, input_channel=0, enabled=True),
        ChannelConfig(name="AI1", frequency_hz=1000.0, input_channel=1, enabled=False),
        ChannelConfig(name="AI2", frequency_hz=1000.0, input_channel=2, enabled=False),
        ChannelConfig(name="AI3", frequency_hz=1000.0, input_channel=3, enabled=False),
    ]
    return LockinConfig(acquisition=acq, channels=channels)


def test_effective_ai_config_only_acquires_enabled_channel_columns():
    """Regression test: autoconfigure() acquires every physical AI channel a device
    has, but only demodulating one of them shouldn't mean acquiring all four -- that
    wastes real bandwidth and was a contributing cause of a real DAQmx read-overrun
    on a 4-channel card."""
    effective = effective_ai_config(_four_channel_config())
    assert effective.acquisition.ai_channels == ("ai0",)
    assert len(effective.channels) == 1
    assert effective.channels[0].name == "AI0"
    assert effective.channels[0].input_channel == 0  # remapped to the new (only) column
    assert effective.channels[0].enabled is True


def test_effective_ai_config_remaps_input_channel_to_new_position():
    acq = AcquisitionConfig(ai_channels=("ai0", "ai1", "ai2", "ai3"))
    channels = [
        ChannelConfig(name="AI1", frequency_hz=1000.0, input_channel=1, enabled=True),
        ChannelConfig(name="AI3", frequency_hz=2000.0, input_channel=3, enabled=True),
    ]
    config = LockinConfig(acquisition=acq, channels=channels)

    effective = effective_ai_config(config)
    assert effective.acquisition.ai_channels == ("ai1", "ai3")
    by_name = {c.name: c for c in effective.channels}
    assert by_name["AI1"].input_channel == 0
    assert by_name["AI3"].input_channel == 1


def test_effective_ai_config_preserves_other_acquisition_fields():
    acq = AcquisitionConfig(
        sample_rate_hz=51_200.0, block_size=2048, window="hann",
        ai_channels=("Dev2/ai0", "Dev2/ai1"),
    )
    channels = [ChannelConfig(name="AI0", frequency_hz=1000.0, input_channel=0, enabled=True)]
    effective = effective_ai_config(LockinConfig(acquisition=acq, channels=channels))
    assert effective.acquisition.sample_rate_hz == 51_200.0
    assert effective.acquisition.block_size == 2048
    assert effective.acquisition.window == "hann"
    assert effective.acquisition.ai_channels == ("Dev2/ai0",)


def test_effective_ai_config_dedupes_channels_sharing_one_physical_input():
    """Two demod channels can share one physical AI input (the FDM scenario this
    whole app targets) -- that must still only acquire one column."""
    acq = AcquisitionConfig(ai_channels=("ai0", "ai1"))
    channels = [
        ChannelConfig(name="CH1", frequency_hz=1000.0, input_channel=0, enabled=True),
        ChannelConfig(name="CH2", frequency_hz=1200.0, input_channel=0, enabled=True),
    ]
    effective = effective_ai_config(LockinConfig(acquisition=acq, channels=channels))
    assert effective.acquisition.ai_channels == ("ai0",)
    assert all(c.input_channel == 0 for c in effective.channels)


def test_effective_ai_config_preserves_ao_channels_untouched():
    from coherence.config import AOChannelConfig

    acq = AcquisitionConfig(ai_channels=("ai0",))
    config = LockinConfig(
        acquisition=acq,
        channels=[ChannelConfig(name="AI0", frequency_hz=1000.0, input_channel=0, enabled=True)],
        ao_channels=[AOChannelConfig(name="AO0", frequency_hz=1000.0, ao_channel="ao0")],
    )
    effective = effective_ai_config(config)
    assert effective.ao_channels == config.ao_channels
