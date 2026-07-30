from coherence.daq import discovery
from coherence.daq.discovery import DeviceSummary
from coherence.ui.widgets.hardware_panel import HardwarePanel


def test_rescan_populates_table_from_detected_devices(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)

    devices = discovery.list_devices()
    assert panel._table.rowCount() == len(devices)
    if devices:
        assert panel._table.item(0, 0).text() == devices[0].name


def test_use_selected_button_disabled_until_row_selected(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    assert panel._use_selected_btn.isEnabled() is False


def test_use_selected_emits_only_the_selected_devices(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    panel._devices = [
        DeviceSummary(name="Dev1", product_type="4461", is_simulated=False, ai_channel_names=["Dev1/ai0"]),
        DeviceSummary(name="Dev2", product_type="4461", is_simulated=False, ai_channel_names=["Dev2/ai0"]),
    ]
    panel.rescan = lambda: None  # freeze the fake devices in place for this test
    panel._table.setRowCount(2)
    from PySide6.QtWidgets import QTableWidgetItem

    panel._table.setItem(0, 0, QTableWidgetItem("Dev1"))
    panel._table.setItem(1, 0, QTableWidgetItem("Dev2"))

    panel._table.selectRow(1)
    assert panel._use_selected_btn.isEnabled() is True

    received = []
    panel.device_activated.connect(received.append)
    panel._on_use_selected()

    assert len(received) == 1
    assert [d.name for d in received[0]] == ["Dev2"]


def test_use_all_emits_every_detected_device(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    panel._devices = [
        DeviceSummary(name="Dev1", product_type="4461", is_simulated=False, ai_channel_names=["Dev1/ai0"]),
        DeviceSummary(name="Dev2", product_type="4461", is_simulated=False, ai_channel_names=["Dev2/ai0"]),
    ]

    received = []
    panel.device_activated.connect(received.append)
    panel._on_use_all()

    assert len(received) == 1
    assert [d.name for d in received[0]] == ["Dev1", "Dev2"]


def test_use_all_does_nothing_when_no_devices_detected(qtbot):
    panel = HardwarePanel()
    qtbot.addWidget(panel)
    panel._devices = []

    received = []
    panel.device_activated.connect(received.append)
    panel._on_use_all()

    assert received == []
