"""Phasor (X-Y / vector) diagram: each channel's (X, Y) as a point, with a fading trail."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from coherence.config import ChannelConfig
from coherence.ui.data_store import ChannelSeries
from coherence.ui.theme import BORDER, channel_color

_TRAIL_LEN = 40


class PolarView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._plot = pg.PlotWidget()
        self._plot.setLabel("left", "Y  (quadrature)", units="V")
        self._plot.setLabel("bottom", "X  (in-phase)", units="V")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setAspectLocked(True)
        self._plot.addLegend(offset=(10, 10))
        layout.addWidget(self._plot)

        self._draw_reference_grid()

        self._trails: dict[str, pg.ScatterPlotItem] = {}
        self._heads: dict[str, pg.ScatterPlotItem] = {}

    def _draw_reference_grid(self) -> None:
        theta = np.linspace(0, 2 * np.pi, 200)
        pen = pg.mkPen(color=BORDER, width=1, style=Qt.PenStyle.DotLine)
        for radius in (0.25, 0.5, 0.75, 1.0):
            self._plot.plot(radius * np.cos(theta), radius * np.sin(theta), pen=pen)
        axis_pen = pg.mkPen(color=BORDER, width=1)
        self._plot.plot([-1.1, 1.1], [0, 0], pen=axis_pen)
        self._plot.plot([0, 0], [-1.1, 1.1], pen=axis_pen)

    def set_channels(self, channels: list[ChannelConfig]) -> None:
        for item in list(self._trails.values()) + list(self._heads.values()):
            self._plot.removeItem(item)
        self._trails.clear()
        self._heads.clear()
        for row, ch in enumerate(channels):
            color = QColor(channel_color(row))
            trail = pg.ScatterPlotItem(size=5, brush=pg.mkBrush(color.red(), color.green(), color.blue(), 90))
            head = pg.ScatterPlotItem(size=12, brush=pg.mkBrush(color), pen=pg.mkPen("#ffffff", width=1), name=ch.name)
            self._plot.addItem(trail)
            self._plot.addItem(head)
            self._trails[ch.name] = trail
            self._heads[ch.name] = head

    def update_from_snapshot(self, series: dict[str, ChannelSeries]) -> None:
        max_extent = 1e-9
        for name, trail in self._trails.items():
            s = series.get(name)
            if s is None or s.x.size == 0:
                continue
            xs = s.x[-_TRAIL_LEN:]
            ys = s.y[-_TRAIL_LEN:]
            trail.setData(xs[:-1], ys[:-1])
            self._heads[name].setData([xs[-1]], [ys[-1]])
            max_extent = max(max_extent, float(np.max(np.abs(xs))), float(np.max(np.abs(ys))))

        limit = max_extent * 1.3
        self._plot.setRange(xRange=(-limit, limit), yRange=(-limit, limit), padding=0)
