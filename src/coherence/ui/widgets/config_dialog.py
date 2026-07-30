"""Acquisition + channel configuration editor."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coherence.config import WINDOW_CHOICES, AOChannelConfig, ChannelConfig, LockinConfig
from coherence.daq import discovery

_BLOCK_SIZES = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
_CHANNEL_COLUMNS = ["Name", "Frequency (Hz)", "Input Ch.", "Enabled"]
_AO_COLUMNS = ["Name", "AO Channel", "Frequency (Hz)", "Amplitude (V)", "Enabled"]


class ConfigDialog(QDialog):
    def __init__(self, config: LockinConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Acquisition & Channel Configuration")
        self.setMinimumSize(980, 520)
        self._config = copy.deepcopy(config)
        self._detected_devices: dict[str, discovery.DeviceSummary] = {}

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, stretch=1)

        body.addWidget(self._build_acquisition_group(), stretch=2)
        body.addWidget(self._build_channels_group(), stretch=2)
        body.addWidget(self._build_outputs_group(), stretch=2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._load_from_config()
        self._refresh_derived_labels()

    # -- acquisition panel -------------------------------------------------
    def _build_acquisition_group(self) -> QWidget:
        group = QGroupBox("Acquisition")
        form = QFormLayout(group)

        self._sample_rate = QDoubleSpinBox()
        self._sample_rate.setRange(1_000.0, 2_000_000.0)
        self._sample_rate.setDecimals(1)
        self._sample_rate.setSuffix(" Hz")
        self._sample_rate.valueChanged.connect(self._refresh_derived_labels)
        form.addRow("Sample rate", self._sample_rate)

        self._block_size = QComboBox()
        for n in _BLOCK_SIZES:
            self._block_size.addItem(str(n), n)
        self._block_size.currentIndexChanged.connect(self._refresh_derived_labels)
        form.addRow("FFT block size (N)", self._block_size)

        self._overlap = QDoubleSpinBox()
        self._overlap.setRange(0.0, 0.9375)
        self._overlap.setSingleStep(0.125)
        self._overlap.setDecimals(3)
        self._overlap.valueChanged.connect(self._refresh_derived_labels)
        form.addRow("Overlap fraction", self._overlap)

        self._window = QComboBox()
        self._window.addItems(list(WINDOW_CHOICES))
        form.addRow("Window", self._window)

        device_row = QHBoxLayout()
        self._device_name = QComboBox()
        self._device_name.setEditable(True)
        self._device_name.currentTextChanged.connect(self._on_device_changed)
        device_row.addWidget(self._device_name, stretch=1)
        refresh_btn = QPushButton("Detect")
        refresh_btn.setToolTip("Re-scan for connected NI devices")
        refresh_btn.clicked.connect(self._refresh_devices)
        device_row.addWidget(refresh_btn)
        form.addRow("NI device", device_row)

        self._device_info_label = QLabel("")
        self._device_info_label.setWordWrap(True)
        self._device_info_label.setStyleSheet("color: #9aa2b1;")
        form.addRow("", self._device_info_label)

        self._ai_channels = QLineEdit()
        self._ai_channels.setPlaceholderText("ai0, ai1, ai2")
        form.addRow("AI channels", self._ai_channels)

        self._input_range = QDoubleSpinBox()
        self._input_range.setRange(0.1, 42.0)
        self._input_range.setSuffix(" Vpk")
        form.addRow("Input range", self._input_range)

        form.addRow(QLabel(""))
        self._bin_spacing_label = QLabel()
        self._update_rate_label = QLabel()
        self._block_duration_label = QLabel()
        for label in (self._bin_spacing_label, self._update_rate_label, self._block_duration_label):
            label.setStyleSheet("color: #9aa2b1;")
        form.addRow("Bin spacing", self._bin_spacing_label)
        form.addRow("Update rate", self._update_rate_label)
        form.addRow("Block duration", self._block_duration_label)

        return group

    # -- channel table -------------------------------------------------
    def _build_channels_group(self) -> QWidget:
        group = QGroupBox("Channels")
        layout = QVBoxLayout(group)

        self._table = QTableWidget(0, len(_CHANNEL_COLUMNS))
        self._table.setHorizontalHeaderLabels(_CHANNEL_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add channel")
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected_rows)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return group

    def _add_row(self, name: str = "", freq: float = 50_000.0, input_ch: int = 0, enabled: bool = True) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name or f"CH{row + 1}"))
        self._table.setItem(row, 1, QTableWidgetItem(f"{freq:.1f}"))
        self._table.setItem(row, 2, QTableWidgetItem(str(input_ch)))
        check = QCheckBox()
        check.setChecked(enabled)
        self._table.setCellWidget(row, 3, check)

    def _remove_selected_rows(self) -> None:
        for row in sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True):
            self._table.removeRow(row)

    # -- output (AO / reference) table -------------------------------------------------
    def _build_outputs_group(self) -> QWidget:
        group = QGroupBox("Outputs (AO / Reference)")
        layout = QVBoxLayout(group)

        hint = QLabel(
            "Reference signal(s) generated continuously while acquisition runs -- "
            "e.g. wire an AO channel to drive your sample, matching a traditional "
            "lock-in's reference output."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa2b1;")
        layout.addWidget(hint)

        self._ao_table = QTableWidget(0, len(_AO_COLUMNS))
        self._ao_table.setHorizontalHeaderLabels(_AO_COLUMNS)
        self._ao_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._ao_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._ao_table, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add output")
        add_btn.clicked.connect(self._add_ao_row)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected_ao_rows)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return group

    def _add_ao_row(
        self, name: str = "", ao_channel: str = "ao0", freq: float = 1_000.0,
        amplitude: float = 1.0, enabled: bool = True,
    ) -> None:
        row = self._ao_table.rowCount()
        self._ao_table.insertRow(row)
        self._ao_table.setItem(row, 0, QTableWidgetItem(name or f"REF{row + 1}"))
        self._ao_table.setItem(row, 1, QTableWidgetItem(ao_channel))
        self._ao_table.setItem(row, 2, QTableWidgetItem(f"{freq:.1f}"))
        self._ao_table.setItem(row, 3, QTableWidgetItem(f"{amplitude:.3f}"))
        check = QCheckBox()
        check.setChecked(enabled)
        self._ao_table.setCellWidget(row, 4, check)

    def _remove_selected_ao_rows(self) -> None:
        for row in sorted({idx.row() for idx in self._ao_table.selectedIndexes()}, reverse=True):
            self._ao_table.removeRow(row)

    # -- hardware discovery -------------------------------------------------
    def _refresh_devices(self, select: str | None = None) -> None:
        """Re-scan for connected NI devices and repopulate the picker.

        Device names (Dev1, Dev2, PXI1Slot2, ...) are assigned by NI-MAX and are not
        portable between machines -- always show what's actually detected right now
        rather than trusting a name saved in a config from a different machine.
        """
        target = select if select is not None else self._device_name.currentText()
        self._detected_devices = {d.name: d for d in discovery.list_devices()}

        self._device_name.blockSignals(True)
        self._device_name.clear()
        for name, dev in self._detected_devices.items():
            label = f"{name}  ({dev.product_type})" if dev.product_type else name
            self._device_name.addItem(label, name)
        self._device_name.blockSignals(False)

        idx = self._device_name.findData(target)
        if idx >= 0:
            self._device_name.setCurrentIndex(idx)
        else:
            self._device_name.setCurrentText(target)
        self._update_device_info_label()

    def _on_device_changed(self) -> None:
        self._update_device_info_label()

    def _current_device_name(self) -> str:
        data = self._device_name.currentData()
        return data if data else self._device_name.currentText().strip()

    def _update_device_info_label(self) -> None:
        if not discovery.nidaqmx_available():
            self._device_info_label.setText("nidaqmx is not installed -- device list cannot be detected.")
            return
        dev = self._detected_devices.get(self._current_device_name())
        if dev is None:
            self._device_info_label.setText("Not currently detected -- check it's connected and powered.")
            return
        rate = f"{dev.ai_max_multi_chan_rate_hz:,.0f} Hz" if dev.ai_max_multi_chan_rate_hz else "unknown"
        self._device_info_label.setText(
            f"Detected: {len(dev.ai_channel_names)} AI channel(s) {dev.ai_channel_names}, "
            f"max multi-channel AI rate {rate}."
        )

    # -- load / derive / save -------------------------------------------------
    def _load_from_config(self) -> None:
        acq = self._config.acquisition
        self._sample_rate.setValue(acq.sample_rate_hz)
        idx = self._block_size.findData(acq.block_size)
        self._block_size.setCurrentIndex(idx if idx >= 0 else 3)
        self._overlap.setValue(acq.overlap_fraction)
        self._window.setCurrentText(acq.window)
        self._refresh_devices(select=acq.device_name)
        self._ai_channels.setText(", ".join(acq.ai_channels))
        self._input_range.setValue(acq.input_range_v)

        self._table.setRowCount(0)
        for ch in self._config.channels:
            self._add_row(ch.name, ch.frequency_hz, ch.input_channel, ch.enabled)

        self._ao_table.setRowCount(0)
        for ao in self._config.ao_channels:
            self._add_ao_row(ao.name, ao.ao_channel, ao.frequency_hz, ao.amplitude_v, ao.enabled)

    def _refresh_derived_labels(self) -> None:
        fs = self._sample_rate.value()
        n = self._block_size.currentData() or 2048
        bin_spacing = fs / n
        hop = max(1, round(n * (1.0 - self._overlap.value())))
        update_rate = fs / hop
        self._bin_spacing_label.setText(f"{bin_spacing:,.2f} Hz")
        self._update_rate_label.setText(f"{update_rate:,.1f} Hz")
        self._block_duration_label.setText(f"{1000.0 * n / fs:,.2f} ms")

    def _on_accept(self) -> None:
        try:
            ai_channels = tuple(c.strip() for c in self._ai_channels.text().split(",") if c.strip())
            self._config.acquisition.sample_rate_hz = self._sample_rate.value()
            self._config.acquisition.block_size = self._block_size.currentData()
            self._config.acquisition.overlap_fraction = self._overlap.value()
            self._config.acquisition.window = self._window.currentText()
            self._config.acquisition.device_name = self._current_device_name()
            self._config.acquisition.ai_channels = ai_channels or ("ai0",)
            self._config.acquisition.input_range_v = self._input_range.value()

            channels: list[ChannelConfig] = []
            for row in range(self._table.rowCount()):
                name = self._table.item(row, 0).text().strip()
                freq = float(self._table.item(row, 1).text())
                input_ch = int(self._table.item(row, 2).text())
                enabled = self._table.cellWidget(row, 3).isChecked()
                channels.append(ChannelConfig(name=name, frequency_hz=freq, input_channel=input_ch, enabled=enabled))
            self._config.channels = channels

            ao_channels: list[AOChannelConfig] = []
            for row in range(self._ao_table.rowCount()):
                name = self._ao_table.item(row, 0).text().strip()
                ao_channel = self._ao_table.item(row, 1).text().strip()
                freq = float(self._ao_table.item(row, 2).text())
                amplitude = float(self._ao_table.item(row, 3).text())
                enabled = self._ao_table.cellWidget(row, 4).isChecked()
                ao_channels.append(
                    AOChannelConfig(
                        name=name, frequency_hz=freq, ao_channel=ao_channel,
                        amplitude_v=amplitude, enabled=enabled,
                    )
                )
            self._config.ao_channels = ao_channels
        except (ValueError, AttributeError) as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Invalid configuration", str(exc))
            return
        self.accept()

    @property
    def result_config(self) -> LockinConfig:
        return self._config
