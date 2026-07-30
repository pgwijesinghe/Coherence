from coherence.daq import discovery
from coherence.ui.widgets.hardware_panel import HardwarePanel


def test_rescan_populates_table_from_detected_devices(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)

    devices = discovery.list_devices()
    assert panel._table.rowCount() == len(devices)
    if devices:
        assert panel._table.item(0, 0).text() == devices[0].name


def test_use_device_button_disabled_until_row_selected(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    assert panel._use_device_btn.isEnabled() is False


def test_use_device_emits_selected_device(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    if panel._table.rowCount() == 0:
        return  # nothing connected on this machine -- nothing to select

    panel._table.selectRow(0)
    assert panel._use_device_btn.isEnabled() is True

    received = []
    panel.device_activated.connect(received.append)
    panel._on_use_device()

    assert len(received) == 1
    assert received[0].name == panel._devices[0].name
