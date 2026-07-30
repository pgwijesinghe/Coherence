"""Editable AO reference/stimulus channel roster, live in the main window.

Unlike the AI side's reference frequency (a Configure-dialog concern -- see
channel_table.py), a lock-in's *output* frequency/amplitude is the classic thing
you tune live during a run, so it's editable directly here. The physical AO
channel mapping (which pin) stays read-only -- that's a hardware fact set by
autoconfiguration, not something to hand-edit casually.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHeaderView, QTableWidget, QTableWidgetItem

from coherence.config import AOChannelConfig

_COLUMNS = ["Name", "AO Channel", "Freq (Hz)", "Amplitude (V)", "Enabled"]
_ENABLED_COL = 4


class OutputsPanel(QTableWidget):
    channels_edited = Signal()
    """Emitted whenever the user edits a name/frequency/amplitude or toggles Enabled."""

    def __init__(self, parent=None):
        super().__init__(0, len(_COLUMNS), parent)
        self._loading = False
        self.setHorizontalHeaderLabels(_COLUMNS)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_COLUMNS)):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.itemChanged.connect(self._on_item_changed)

    def set_channels(self, ao_channels: list[AOChannelConfig]) -> None:
        self._loading = True
        try:
            self.setRowCount(len(ao_channels))
            for row, ao in enumerate(ao_channels):
                self.setItem(row, 0, QTableWidgetItem(ao.name))

                channel_item = QTableWidgetItem(ao.ao_channel)
                channel_item.setFlags(channel_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, 1, channel_item)

                self.setItem(row, 2, QTableWidgetItem(f"{ao.frequency_hz:,.1f}"))
                self.setItem(row, 3, QTableWidgetItem(f"{ao.amplitude_v:.3f}"))

                check = QCheckBox()
                check.setChecked(ao.enabled)
                check.toggled.connect(self._on_enabled_toggled)
                self.setCellWidget(row, _ENABLED_COL, check)
        finally:
            self._loading = False

    def set_editable(self, editable: bool) -> None:
        """Frequency/amplitude/enabled can be changed while running (hot-swaps just the
        AO generator); the main window may still choose to lock editing in other states."""
        self.setEditTriggers(
            (QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
            if editable
            else QAbstractItemView.EditTrigger.NoEditTriggers
        )
        for row in range(self.rowCount()):
            widget = self.cellWidget(row, _ENABLED_COL)
            if widget is not None:
                widget.setEnabled(editable)

    def get_channels(self) -> list[AOChannelConfig]:
        result = []
        for row in range(self.rowCount()):
            try:
                freq = float(self.item(row, 2).text().replace(",", ""))
                amp = float(self.item(row, 3).text())
            except (ValueError, AttributeError):
                continue
            check = self.cellWidget(row, _ENABLED_COL)
            result.append(
                AOChannelConfig(
                    name=self.item(row, 0).text().strip(),
                    frequency_hz=freq,
                    ao_channel=self.item(row, 1).text().strip(),
                    amplitude_v=amp,
                    enabled=check.isChecked() if check is not None else False,
                )
            )
        return result

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._loading:
            self.channels_edited.emit()

    def _on_enabled_toggled(self, _checked: bool) -> None:
        if not self._loading:
            self.channels_edited.emit()
