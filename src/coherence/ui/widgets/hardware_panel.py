"""Hardware tab: what's actually connected, independent of what you're measuring.

Separating "what hardware exists" from "what am I demodulating" mirrors how real
instrument software is laid out -- device/channel capabilities are a setup-once
concern, reference frequencies and enabled channels are what you touch every run.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coherence.daq import discovery
from coherence.daq.discovery import DeviceSummary

_COLUMNS = ["Device", "Product Type", "AI Channels", "AO Channels", "Max AI Rate", "Max AO Rate", "Simulated"]


class HardwarePanel(QWidget):
    device_activated = Signal(object)
    """Emitted with a DeviceSummary when the user picks 'Use this device'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: list[DeviceSummary] = []

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #9aa2b1;")
        header.addWidget(self._summary_label, stretch=1)
        rescan_btn = QPushButton("Rescan")
        rescan_btn.setToolTip("Re-scan for connected NI devices")
        rescan_btn.clicked.connect(self.rescan)
        header.addWidget(rescan_btn)
        layout.addLayout(header)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._use_device_btn = QPushButton("Use This Device")
        self._use_device_btn.setEnabled(False)
        self._use_device_btn.clicked.connect(self._on_use_device)
        footer.addWidget(self._use_device_btn)
        layout.addLayout(footer)

        self._table.itemSelectionChanged.connect(
            lambda: self._use_device_btn.setEnabled(bool(self._table.selectedIndexes()))
        )

        self.rescan()

    def rescan(self) -> None:
        self._devices = discovery.list_devices()
        self._table.setRowCount(len(self._devices))
        for row, dev in enumerate(self._devices):
            self._table.setItem(row, 0, QTableWidgetItem(dev.name))
            self._table.setItem(row, 1, QTableWidgetItem(dev.product_type))
            self._table.setItem(row, 2, QTableWidgetItem(str(len(dev.ai_channel_names))))
            self._table.setItem(row, 3, QTableWidgetItem(str(len(dev.ao_channel_names))))
            ai_rate = f"{dev.ai_max_multi_chan_rate_hz:,.0f} Hz" if dev.ai_max_multi_chan_rate_hz else "--"
            ao_rate = f"{dev.ao_max_rate_hz:,.0f} Hz" if dev.ao_max_rate_hz else "--"
            self._table.setItem(row, 4, QTableWidgetItem(ai_rate))
            self._table.setItem(row, 5, QTableWidgetItem(ao_rate))
            self._table.setItem(row, 6, QTableWidgetItem("yes" if dev.is_simulated else "no"))

        if not discovery.nidaqmx_available():
            self._summary_label.setText("nidaqmx is not installed -- only the Simulated backend is available.")
        elif not self._devices:
            self._summary_label.setText("No NI devices detected. Check the device is connected and powered.")
        else:
            self._summary_label.setText(f"{len(self._devices)} device(s) detected.")
        self._use_device_btn.setEnabled(False)

    def _on_use_device(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not rows:
            return
        device = self._devices[next(iter(rows))]
        self.device_activated.emit(device)
