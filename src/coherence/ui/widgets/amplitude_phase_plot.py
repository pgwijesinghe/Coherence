"""Scrolling amplitude(t) / phase(t) strip charts, one curve per channel."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

from coherence.config import ChannelConfig
from coherence.ui.data_store import ChannelSeries
from coherence.ui.theme import channel_color


class AmplitudePhasePlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._amp_plot = pg.PlotWidget()
        self._amp_plot.setLabel("left", "Amplitude", units="V")
        self._amp_plot.setLabel("bottom", "Time", units="s")
        self._amp_plot.showGrid(x=True, y=True, alpha=0.25)
        self._amp_plot.addLegend(offset=(10, 10))

        self._phase_plot = pg.PlotWidget()
        self._phase_plot.setLabel("left", "Phase", units="deg")
        self._phase_plot.setLabel("bottom", "Time", units="s")
        self._phase_plot.showGrid(x=True, y=True, alpha=0.25)
        self._phase_plot.setXLink(self._amp_plot)

        layout.addWidget(self._amp_plot, stretch=3)
        layout.addWidget(self._phase_plot, stretch=2)

        self._amp_curves: dict[str, pg.PlotDataItem] = {}
        self._phase_curves: dict[str, pg.PlotDataItem] = {}

    def set_channels(self, channels: list[ChannelConfig]) -> None:
        self._amp_plot.clear()
        self._phase_plot.clear()
        self._amp_plot.addLegend(offset=(10, 10))
        self._amp_curves.clear()
        self._phase_curves.clear()
        for row, ch in enumerate(channels):
            color = channel_color(row)
            # Width-1 pens keep Qt's fast line-drawing path; wider pens fall back to a
            # much slower painter and were a measurable part of GUI-thread load (which,
            # via the GIL, can starve the DAQmx callback thread into overruns).
            pen = pg.mkPen(color=color, width=1)
            for plot, curves in ((self._amp_plot, self._amp_curves), (self._phase_plot, self._phase_curves)):
                curve = plot.plot([], [], pen=pen, name=ch.name)
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
                curves[ch.name] = curve

    def update_from_snapshot(self, series: dict[str, ChannelSeries]) -> None:
        for name, curve in self._amp_curves.items():
            s = series.get(name)
            if s is None or s.t.size == 0:
                continue
            t0 = s.t[-1]
            curve.setData(s.t - t0, s.amplitude)
            self._phase_curves[name].setData(s.t - t0, np.degrees(s.phase_rad))
