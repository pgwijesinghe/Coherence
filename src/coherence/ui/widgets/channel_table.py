"""Live numeric read-out table: one row per demodulation channel.

The physical AI channel roster (which channels exist, which are enabled) lives
here in the main window per-request; the demodulation reference frequency for
each stays a Configure-dialog concern -- it's a "wiring/setup" parameter, changed
far less often than which channels you're currently watching.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHeaderView, QTableWidget, QTableWidgetItem

from coherence.config import ChannelConfig
from coherence.ui.data_store import ChannelSeries
from coherence.ui.theme import channel_color

_COLUMNS = ["Channel", "Freq (Hz)", "Amplitude", "Phase (deg)", "X", "Y", "Enabled"]
_ENABLED_COL = len(_COLUMNS) - 1


class ChannelTable(QTableWidget):
    enabled_changed = Signal(int, bool)
    """(row, checked) -- emitted when the user toggles a channel's Enabled checkbox."""

    def __init__(self, parent=None):
        super().__init__(0, len(_COLUMNS), parent)
        self.setHorizontalHeaderLabels(_COLUMNS)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_COLUMNS)):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._row_of: dict[str, int] = {}

    def set_channels(self, channels: list[ChannelConfig]) -> None:
        self.setRowCount(len(channels))
        self._row_of.clear()
        for row, ch in enumerate(channels):
            self._row_of[ch.name] = row
            name_item = QTableWidgetItem(ch.name)
            name_item.setForeground(QColor(channel_color(row)))
            self.setItem(row, 0, name_item)
            self.setItem(row, 1, QTableWidgetItem(f"{ch.frequency_hz:,.1f}"))
            for col in range(2, _ENABLED_COL):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, col, item)

            check = QCheckBox()
            check.setChecked(ch.enabled)
            check.toggled.connect(lambda checked, r=row: self.enabled_changed.emit(r, checked))
            self.setCellWidget(row, _ENABLED_COL, check)

    def set_editable(self, editable: bool) -> None:
        """Enabled-state editing is only allowed while stopped -- toggling which
        physical channels are demodulated requires rebuilding the FFT engine, which
        means a full pipeline restart, same as any other Configure-dialog change."""
        for row in range(self.rowCount()):
            widget = self.cellWidget(row, _ENABLED_COL)
            if widget is not None:
                widget.setEnabled(editable)

    def update_latest(self, latest: dict[str, tuple[float, float, float, float]]) -> None:
        """latest: {channel name: (amplitude, phase_rad, x, y)} -- see LiveDataStore.latest()."""
        for name, row in self._row_of.items():
            vals = latest.get(name)
            if vals is None:
                continue
            amp, phase_rad, x, y = vals
            self.item(row, 2).setText(f"{amp:.5g}")
            self.item(row, 3).setText(f"{np.degrees(phase_rad):.2f}")
            self.item(row, 4).setText(f"{x:.5g}")
            self.item(row, 5).setText(f"{y:.5g}")

    def update_from_snapshot(self, series: dict[str, ChannelSeries]) -> None:
        self.update_latest(
            {
                name: (s.amplitude[-1], s.phase_rad[-1], s.x[-1], s.y[-1])
                for name, s in series.items()
                if s.amplitude.size
            }
        )
