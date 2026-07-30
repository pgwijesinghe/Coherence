from coherence.config import ChannelConfig
from coherence.ui.widgets.channel_table import ChannelTable


def _channels():
    return [
        ChannelConfig(name="AI0", frequency_hz=1000.0, input_channel=0, enabled=True),
        ChannelConfig(name="AI1", frequency_hz=2000.0, input_channel=1, enabled=False),
    ]


def test_enabled_checkbox_reflects_config(qtbot):
    table = ChannelTable()
    qtbot.addWidget(table)
    table.set_channels(_channels())

    assert table.cellWidget(0, 6).isChecked() is True
    assert table.cellWidget(1, 6).isChecked() is False


def test_toggling_checkbox_emits_signal_with_row_and_state(qtbot):
    table = ChannelTable()
    qtbot.addWidget(table)
    table.set_channels(_channels())

    received = []
    table.enabled_changed.connect(lambda row, checked: received.append((row, checked)))
    table.cellWidget(1, 6).setChecked(True)

    assert received == [(1, True)]


def test_set_editable_toggles_checkbox_interactivity(qtbot):
    table = ChannelTable()
    qtbot.addWidget(table)
    table.set_channels(_channels())

    table.set_editable(False)
    assert table.cellWidget(0, 6).isEnabled() is False

    table.set_editable(True)
    assert table.cellWidget(0, 6).isEnabled() is True
