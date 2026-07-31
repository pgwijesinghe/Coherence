"""Debug tab: live application log, multi-device synchronization status, and
pipeline/engine telemetry in one place -- the "what is this thing actually doing"
view for diagnosing a run that isn't behaving as expected, instead of hunting
through a terminal.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from coherence.config import LockinConfig
from coherence.core.pipeline import PipelineStats
from coherence.ui.theme import BAD, GOOD, TEXT_SECONDARY

_MAX_LOG_LINES = 2000


class _LogSignal(QObject):
    new_record = Signal(str)


class _QtLogHandler(logging.Handler):
    """Bridges Python logging records onto the Qt event loop via a signal. Log calls
    can happen on the DAQmx callback thread, the acquisition worker thread, or the
    GUI thread -- a queued Qt signal is the safe way to cross those boundaries,
    unlike touching a widget directly from a non-GUI thread."""

    def __init__(self, signal_bridge: _LogSignal):
        super().__init__()
        self._bridge = signal_bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001 - a broken log call must never crash the app
            msg = record.getMessage()
        self._bridge.new_record.emit(msg)


class DebugPanel(QWidget):
    """Everything about *how* the current run is executing, as opposed to what it
    measured: live log output, multi-device sync status (reference clock, DSA sync
    pulse, start trigger -- see docs/hardware-notes.md), and engine/pipeline stats."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = _LogSignal()
        self._handler = _QtLogHandler(self._bridge)
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        self._bridge.new_record.connect(self._append_log)

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter)
        splitter.addWidget(self._build_status_group())
        splitter.addWidget(self._build_log_group())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    # ------------------------------------------------------------------ build
    def _build_status_group(self) -> QWidget:
        group = QGroupBox("Pipeline / Synchronization Status")
        layout = QVBoxLayout(group)

        self._summary_label = QLabel("No acquisition running.")
        self._summary_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        row = QHBoxLayout()
        self._rate_label = QLabel("Update rate: --")
        self._overrun_label = QLabel("Overruns: --")
        self._blocks_label = QLabel("Blocks processed: --")
        for lbl in (self._rate_label, self._overrun_label, self._blocks_label):
            row.addWidget(lbl)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(QLabel("Reference clock / DSA sync pulse / start trigger (multi-device only):"))
        self._sync_text = QPlainTextEdit()
        self._sync_text.setReadOnly(True)
        self._sync_text.setMaximumBlockCount(200)
        self._sync_text.setPlaceholderText(
            "Nothing to report -- single-device acquisition needs no cross-task "
            "synchronization, or acquisition hasn't started yet."
        )
        self._sync_text.setStyleSheet("font-family: Consolas, monospace;")
        self._sync_text.setMaximumHeight(140)
        layout.addWidget(self._sync_text)

        return group

    def _build_log_group(self) -> QWidget:
        group = QGroupBox("Application Log")
        layout = QVBoxLayout(group)

        header = QHBoxLayout()
        header.addStretch(1)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumBlockCount(_MAX_LOG_LINES)
        self._log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self._log_text, stretch=1)

        return group

    # ------------------------------------------------------------------ logging wiring
    def attach_logging(self, level: int = logging.INFO) -> None:
        """Start receiving records from every coherence.* logger. Call once, at
        window construction; pair with detach_logging() on window close so the
        handler doesn't outlive the widget."""
        logger = logging.getLogger("coherence")
        logger.addHandler(self._handler)
        if logger.level == logging.NOTSET or logger.level > level:
            logger.setLevel(level)

    def detach_logging(self) -> None:
        logging.getLogger("coherence").removeHandler(self._handler)

    def _append_log(self, message: str) -> None:
        self._log_text.appendPlainText(message)

    # ------------------------------------------------------------------ updates
    def set_run_info(self, config: LockinConfig, backend_kind: str) -> None:
        acq = config.acquisition
        devices = ", ".join(acq.devices) or "--"
        if acq.engine == "streaming":
            engine_detail = "Streaming (continuous NCO mixer + running filter; per-channel time constant)"
        else:
            engine_detail = (
                f"FFT (block_size={acq.block_size}, overlap={acq.overlap_fraction:.2f}, "
                f"window={acq.window!r}, bin spacing {acq.bin_spacing_hz:,.3f} Hz)"
            )
        self._summary_label.setText(
            f"Backend: {backend_kind}   |   Device(s): {devices}   |   Sample rate: {acq.sample_rate_hz:,.0f} Hz\n"
            f"Engine: {engine_detail}"
        )

    def clear_run_info(self) -> None:
        self._summary_label.setText("No acquisition running.")
        self._rate_label.setText("Update rate: --")
        self._overrun_label.setText("Overruns: --")
        self._blocks_label.setText("Blocks processed: --")
        self._sync_text.clear()

    def update_stats(self, stats: PipelineStats) -> None:
        self._rate_label.setText(f"Update rate: {stats.measured_update_rate_hz:,.1f} Hz")
        self._overrun_label.setText(f"Overruns: {stats.overruns}")
        self._overrun_label.setStyleSheet(f"color: {BAD if stats.overruns else GOOD};")
        self._blocks_label.setText(f"Blocks processed: {stats.blocks_processed:,}")

    def set_sync_report(self, ai_lines: list[str], ao_lines: list[str]) -> None:
        lines: list[str] = []
        if ai_lines:
            lines.append("-- AI acquisition --")
            lines.extend(ai_lines)
        if ao_lines:
            lines.append("-- AO stimulus --")
            lines.extend(ao_lines)
        self._sync_text.setPlainText("\n".join(lines))
