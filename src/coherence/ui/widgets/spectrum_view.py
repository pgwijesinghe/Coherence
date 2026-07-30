"""Live magnitude spectrum per physical input, with markers at each demodulated bin.

This is the diagnostic view an FFT-based lock-in gets "for free" that a bank of
per-channel IQ demodulators does not: the whole neighborhood around each tone,
useful for spotting leakage, drift off-bin, or interference between channels.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from coherence.config import ChannelConfig
from coherence.ui.theme import channel_color


class SpectrumView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel("Input channel:"))
        self._input_select = QComboBox()
        self._input_select.setMinimumWidth(120)
        header.addWidget(self._input_select)
        header.addStretch(1)
        layout.addLayout(header)

        self._plot = pg.PlotWidget()
        self._plot.setLabel("left", "Magnitude", units="dBV")
        self._plot.setLabel("bottom", "Frequency", units="Hz")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._curve = self._plot.plot([], [], pen=pg.mkPen(color="#56B4E9", width=1))
        layout.addWidget(self._plot, stretch=1)

        self._markers: list[pg.InfiniteLine] = []
        self._channels: list[ChannelConfig] = []

    def set_channels(self, channels: list[ChannelConfig]) -> None:
        self._channels = channels
        self._input_select.clear()
        for input_ch in sorted({c.input_channel for c in channels}):
            self._input_select.addItem(f"AI{input_ch}", input_ch)

        for m in self._markers:
            self._plot.removeItem(m)
        self._markers.clear()
        for row, ch in enumerate(channels):
            line = pg.InfiniteLine(
                pos=ch.frequency_hz,
                angle=90,
                pen=pg.mkPen(color=channel_color(row), width=1, style=Qt.PenStyle.DashLine),
                label=ch.name,
                labelOpts={"position": 0.95, "color": channel_color(row)},
            )
            self._plot.addItem(line)
            self._markers.append(line)

    def update_from_snapshot(self, spectra: dict[int, tuple[np.ndarray, np.ndarray]]) -> None:
        if self._input_select.count() == 0:
            return
        current = self._input_select.currentData()
        spec = spectra.get(current)
        if spec is None:
            return
        freqs, mag_db = spec
        self._curve.setData(freqs, mag_db)
