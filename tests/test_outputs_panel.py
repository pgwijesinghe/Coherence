from coherence.config import AOChannelConfig
from coherence.ui.widgets.outputs_panel import OutputsPanel


def _ao_channels():
    return [
        AOChannelConfig(name="AO0", frequency_hz=1000.0, ao_channel="ao0", amplitude_v=1.0, enabled=True),
        AOChannelConfig(name="AO1", frequency_hz=2000.0, ao_channel="ao1", amplitude_v=0.5, enabled=False),
    ]


def test_set_channels_does_not_emit_edited_signal(qtbot):
    panel = OutputsPanel()
    qtbot.addWidget(panel)
    received = []
    panel.channels_edited.connect(lambda: received.append(True))
    panel.set_channels(_ao_channels())
    assert received == []  # programmatic population must not look like a user edit


def test_get_channels_round_trips_config(qtbot):
    panel = OutputsPanel()
    qtbot.addWidget(panel)
    original = _ao_channels()
    panel.set_channels(original)

    result = panel.get_channels()
    assert len(result) == 2
    assert result[0].name == "AO0"
    assert result[0].frequency_hz == 1000.0
    assert result[0].amplitude_v == 1.0
    assert result[0].enabled is True
    assert result[1].enabled is False


def test_editing_frequency_emits_signal_and_is_reflected_in_get_channels(qtbot):
    panel = OutputsPanel()
    qtbot.addWidget(panel)
    panel.set_channels(_ao_channels())

    received = []
    panel.channels_edited.connect(lambda: received.append(True))
    panel.item(0, 2).setText("1500.0")

    assert received == [True]
    assert panel.get_channels()[0].frequency_hz == 1500.0


def test_toggling_enabled_checkbox_emits_signal_and_updates_get_channels(qtbot):
    panel = OutputsPanel()
    qtbot.addWidget(panel)
    panel.set_channels(_ao_channels())

    received = []
    panel.channels_edited.connect(lambda: received.append(True))
    panel.cellWidget(1, 4).setChecked(True)

    assert received == [True]
    assert panel.get_channels()[1].enabled is True


def test_ao_channel_column_is_not_editable(qtbot):
    from PySide6.QtCore import Qt

    panel = OutputsPanel()
    qtbot.addWidget(panel)
    panel.set_channels(_ao_channels())
    assert not bool(panel.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable)


def test_set_editable_false_disables_enabled_checkboxes(qtbot):
    panel = OutputsPanel()
    qtbot.addWidget(panel)
    panel.set_channels(_ao_channels())

    panel.set_editable(False)
    assert panel.cellWidget(0, 4).isEnabled() is False

    panel.set_editable(True)
    assert panel.cellWidget(0, 4).isEnabled() is True
