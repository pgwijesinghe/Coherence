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
    """Emitted with a list[DeviceSummary] -- either the selected rows, or every
    detected device -- to combine into one acquisition config."""

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
        # Multiple rows can be combined into one synchronized multi-device acquisition --
        # e.g. selecting 3 of 6 chassis cards to use together. Ctrl/Shift-click to select more than one.
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._use_all_btn = QPushButton("Use All Detected")
        self._use_all_btn.setToolTip("Combine every detected device into one synchronized acquisition")
        self._use_all_btn.clicked.connect(self._on_use_all)
        footer.addWidget(self._use_all_btn)
        self._use_selected_btn = QPushButton("Use Selected")
        self._use_selected_btn.setEnabled(False)
        self._use_selected_btn.setToolTip("Combine only the selected device(s)")
        self._use_selected_btn.clicked.connect(self._on_use_selected)
        footer.addWidget(self._use_selected_btn)
        layout.addLayout(footer)

        self._table.itemSelectionChanged.connect(
            lambda: self._use_selected_btn.setEnabled(bool(self._table.selectedIndexes()))
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
            self._summary_label.setText(
                'nidaqmx is not installed -- only the Simulated backend is available. '
                'Install with: uv pip install -e ".[hardware]"'
            )
        elif not self._devices:
            version = discovery.driver_version()
            self._summary_label.setText(
                f"NI-DAQmx {version or '?'} is installed but reports no devices. "
                "Power the chassis before booting the PC, and check the devices "
                "appear in NI MAX."
            )
        else:
            version = discovery.driver_version()
            ai_capable = [d for d in self._devices if d.ai_channel_names]
            total_ai = sum(len(d.ai_channel_names) for d in ai_capable)
            total_ao = sum(len(d.ao_channel_names) for d in self._devices)
            note = " -- none has AI channels, so there is nothing to acquire from" if not ai_capable else (
                f", {total_ai} AI + {total_ao} AO channel(s) total"
            )
            self._summary_label.setText(
                f"NI-DAQmx {version or '?'}: {len(self._devices)} device(s) detected{note}"
            )
        self._use_selected_btn.setEnabled(False)

    def _on_use_all(self) -> None:
        if self._devices:
            self.device_activated.emit(list(self._devices))

    def _on_use_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return
        self.device_activated.emit([self._devices[r] for r in rows])
