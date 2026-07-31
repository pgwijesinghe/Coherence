from coherence.config import AOChannelConfig, default_config
from coherence.ui.widgets.config_dialog import ConfigDialog


def test_dialog_loads_existing_config(qtbot):
    cfg = default_config()
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    assert dialog._sample_rate.value() == cfg.acquisition.sample_rate_hz
    assert dialog._block_size.currentData() == cfg.acquisition.block_size
    assert dialog._table.rowCount() == len(cfg.channels)


def test_accept_builds_updated_config(qtbot):
    cfg = default_config()
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    dialog._sample_rate.setValue(96_000.0)
    dialog._add_row("CH4", 30_000.0, 0, time_constant_s=0.05, enabled=True)
    dialog._on_accept()

    result = dialog.result_config
    assert result.acquisition.sample_rate_hz == 96_000.0
    assert len(result.channels) == len(cfg.channels) + 1
    assert result.channels[-1].name == "CH4"
    assert result.channels[-1].frequency_hz == 30_000.0


def test_dialog_loads_existing_ao_channels(qtbot):
    cfg = default_config()
    cfg.ao_channels = [AOChannelConfig(name="REF1", frequency_hz=1_000.0, ao_channel="ao0", amplitude_v=2.0)]
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    assert dialog._ao_table.rowCount() == 1
    assert dialog._ao_table.item(0, 0).text() == "REF1"
    assert dialog._ao_table.item(0, 2).text() == "1000.0"


def test_accept_builds_updated_ao_channels(qtbot):
    cfg = default_config()
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    dialog._add_ao_row("REF1", "ao0", 2_000.0, 1.5, True)
    dialog._on_accept()

    result = dialog.result_config
    assert len(result.ao_channels) == 1
    ao = result.ao_channels[0]
    assert ao.name == "REF1"
    assert ao.ao_channel == "ao0"
    assert ao.frequency_hz == 2_000.0
    assert ao.amplitude_v == 1.5


def test_engine_and_time_constant_round_trip(qtbot):
    cfg = default_config()
    cfg.acquisition.engine = "streaming"
    cfg.channels[0].time_constant_s = 0.025
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    assert dialog._engine.currentData() == "streaming"
    assert dialog._table.item(0, 3).text() == "0.025"

    dialog._on_accept()
    result = dialog.result_config
    assert result.acquisition.engine == "streaming"
    assert result.channels[0].time_constant_s == 0.025


def test_derived_labels_update_on_change(qtbot):
    cfg = default_config()
    dialog = ConfigDialog(cfg)
    qtbot.addWidget(dialog)

    dialog._sample_rate.setValue(200_000.0)
    idx = dialog._block_size.findData(2000) if dialog._block_size.findData(2000) >= 0 else 3
    dialog._block_size.setCurrentIndex(idx)
    n = dialog._block_size.currentData()
    expected_spacing = 200_000.0 / n
    assert f"{expected_spacing:,.2f} Hz" in dialog._bin_spacing_label.text()
