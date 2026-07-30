"""Main application window: toolbar-driven front panel typical of lab instrument software."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from coherence import __version__
from coherence.config import LockinConfig, default_config, effective_ai_config
from coherence.core.pipeline import LockinPipeline
from coherence.daq import discovery
from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec
from coherence.daq.autoconfig import autoconfigure
from coherence.daq.base import AcquisitionBackend
from coherence.daq.discovery import DeviceSummary
from coherence.daq.simulated_backend import SimulatedBackend
from coherence.logging.hdf5_logger import HDF5ResultLogger
from coherence.ui.data_store import LiveDataStore
from coherence.ui.theme import BAD, GOOD, WARN
from coherence.ui.widgets.amplitude_phase_plot import AmplitudePhasePlot
from coherence.ui.widgets.channel_table import ChannelTable
from coherence.ui.widgets.config_dialog import ConfigDialog
from coherence.ui.widgets.hardware_panel import HardwarePanel
from coherence.ui.widgets.outputs_panel import OutputsPanel
from coherence.ui.widgets.polar_view import PolarView
from coherence.ui.widgets.spectrum_view import SpectrumView

logger = logging.getLogger(__name__)

_UI_REFRESH_MS = 33  # ~30 Hz paint rate, independent of the FFT block/update rate
_BACKEND_SIMULATED = "Simulated"
_BACKEND_HARDWARE = "NI-DAQmx (hardware)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Coherence -- FFT Multichannel Lock-In Amplifier")
        self.resize(1360, 840)

        detected = discovery.list_devices()
        has_hardware = discovery.first_ai_device(detected) is not None
        self._config: LockinConfig = autoconfigure(detected) if has_hardware else default_config()
        self._pipeline: LockinPipeline | None = None
        self._hdf5_logger: HDF5ResultLogger | None = None
        self._ao_generator: AOStimulusGenerator | None = None
        self._data_store = LiveDataStore()

        self._build_menu_bar()
        self._build_toolbar()
        self._build_central_widget()
        self._build_status_bar()
        self._apply_channels_to_widgets()

        if has_hardware:
            idx = self._backend_combo.findText(_BACKEND_HARDWARE)
            if idx >= 0:
                self._backend_combo.setCurrentIndex(idx)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(_UI_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_ui)
        self._refresh_timer.start()

        self._set_running_state(False)

    # ------------------------------------------------------------------ UI build
    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About Coherence", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About Coherence",
            f"<h3>Coherence</h3>"
            f"<p>Version {__version__}</p>"
            "<p>FFT-based multichannel lock-in amplifier for NI DAQ hardware.</p>"
            "<p>Demodulates multiple frequency-multiplexed channels from a single "
            "windowed FFT per block, instead of one mixer/filter chain per channel -- "
            "one FFT bin is mathematically equivalent to one synchronous IQ "
            "demodulator whose filter is the FFT window.</p>",
        )

    def _build_toolbar(self) -> None:
        style = self.style()
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        self.addToolBar(toolbar)

        self._start_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Start", self)
        self._start_action.triggered.connect(self._on_start)
        toolbar.addAction(self._start_action)

        self._stop_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), "Stop", self)
        self._stop_action.triggered.connect(self._on_stop)
        toolbar.addAction(self._stop_action)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("  Backend: "))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems([_BACKEND_SIMULATED, _BACKEND_HARDWARE])
        toolbar.addWidget(self._backend_combo)

        toolbar.addSeparator()
        self._config_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView), "Configure...", self
        )
        self._config_action.triggered.connect(self._on_configure)
        toolbar.addAction(self._config_action)

        toolbar.addSeparator()
        self._log_action = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon), "Log to HDF5", self)
        self._log_action.setCheckable(True)
        self._log_action.triggered.connect(self._on_toggle_logging)
        toolbar.addAction(self._log_action)

    def _build_central_widget(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QGroupBox("Live Read-out")
        left_layout = QVBoxLayout(left)
        self._channel_table = ChannelTable()
        self._channel_table.enabled_changed.connect(self._on_ai_channel_enabled_changed)
        left_layout.addWidget(self._channel_table, stretch=3)

        left_layout.addWidget(QLabel("Reference Outputs (AO) -- double-click Freq/Amplitude to edit"))
        self._outputs_panel = OutputsPanel()
        self._outputs_panel.channels_edited.connect(self._on_ao_channels_edited)
        left_layout.addWidget(self._outputs_panel, stretch=2)
        splitter.addWidget(left)

        self._tabs = QTabWidget()
        self._hardware_panel = HardwarePanel()
        self._hardware_panel.device_activated.connect(self._on_device_activated)
        self._amp_phase_view = AmplitudePhasePlot()
        self._spectrum_view = SpectrumView()
        self._polar_view = PolarView()
        self._tabs.addTab(self._hardware_panel, "Hardware")
        self._tabs.addTab(self._amp_phase_view, "Amplitude && Phase")
        self._tabs.addTab(self._spectrum_view, "Spectrum")
        self._tabs.addTab(self._polar_view, "Phasor")
        splitter.addWidget(self._tabs)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self._status_pill = QLabel("STOPPED")
        self._status_pill.setObjectName("StatusPill")
        self._update_status_pill(running=False)
        bar.addWidget(self._status_pill)

        self._rate_label = QLabel("Update rate: --")
        self._overrun_label = QLabel("Overruns: --")
        self._ao_status_label = QLabel("")
        self._coherence_label = QLabel("")
        bar.addPermanentWidget(self._coherence_label)
        bar.addPermanentWidget(self._ao_status_label)
        bar.addPermanentWidget(self._overrun_label)
        bar.addPermanentWidget(self._rate_label)

    def _apply_channels_to_widgets(self) -> None:
        channels = self._config.channels
        self._channel_table.set_channels(channels)
        self._amp_phase_view.set_channels(channels)
        self._spectrum_view.set_channels(channels)
        self._polar_view.set_channels(channels)
        self._outputs_panel.set_channels(self._config.ao_channels)

        # Only channels that will actually run matter here: disabled AI channels are
        # stripped out before acquisition (effective_ai_config) and disabled AO channels
        # are never generated, so a stale placeholder frequency on a disabled channel
        # must not raise a scary off-bin warning about a measurement that isn't happening.
        errs = [(c.name, self._config.coherence_error_hz(c)) for c in channels if c.enabled]
        errs += [
            (a.name, self._config.ao_coherence_error_hz(a))
            for a in self._config.ao_channels
            if a.enabled
        ]
        worst_name, worst_err = max(errs, key=lambda e: e[1], default=("", 0.0))
        spacing = self._config.acquisition.bin_spacing_hz
        if spacing > 0 and worst_err > 0.02 * spacing:
            self._coherence_label.setText(f"⚠ {worst_name} off-bin by {worst_err:.2f} Hz")
            self._coherence_label.setStyleSheet(f"color: {WARN};")
        else:
            self._coherence_label.setText("Coherent ✓")
            self._coherence_label.setStyleSheet(f"color: {GOOD};")

    def _update_status_pill(self, running: bool) -> None:
        if running:
            self._status_pill.setText(" ● RUNNING ")
            self._status_pill.setStyleSheet(f"background-color: {GOOD}; color: #0b0d10;")
        else:
            self._status_pill.setText(" ● STOPPED ")
            self._status_pill.setStyleSheet(f"background-color: {BAD}; color: #0b0d10;")

    # ------------------------------------------------------------------ actions
    def _on_device_activated(self, devices: list[DeviceSummary]) -> None:
        if self._pipeline is not None:
            QMessageBox.information(self, "Stop first", "Stop acquisition before switching devices.")
            return
        names = ", ".join(f"{d.name} ({d.product_type})" for d in devices)
        plural = "s" if len(devices) != 1 else ""
        reply = QMessageBox.question(
            self,
            "Switch device" + plural,
            f"Rebuild the channel list from {len(devices)} device{plural}: {names}?\n\n"
            "This replaces the current AI/AO channel roster (frequencies, enabled state) "
            "with one auto-generated from these devices' actual channels. Channels from "
            "more than one device are acquired together in a single synchronized run.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._config = autoconfigure(devices)
        self._data_store.reset()
        self._apply_channels_to_widgets()
        idx = self._backend_combo.findText(_BACKEND_HARDWARE)
        if idx >= 0:
            self._backend_combo.setCurrentIndex(idx)

    def _on_configure(self) -> None:
        if self._pipeline is not None:
            QMessageBox.information(self, "Stop first", "Stop acquisition before changing configuration.")
            return
        dialog = ConfigDialog(self._config, self)
        if dialog.exec():
            self._config = dialog.result_config
            self._data_store.reset()
            self._apply_channels_to_widgets()

    def _on_toggle_logging(self, checked: bool) -> None:
        if checked:
            path, _ = QFileDialog.getSaveFileName(self, "Log results to HDF5", "lockin_log.h5", "HDF5 files (*.h5)")
            if not path:
                self._log_action.setChecked(False)
                return
            try:
                self._hdf5_logger = HDF5ResultLogger(path, self._config)
                if self._pipeline is not None:
                    self._pipeline.add_result_callback(self._hdf5_logger.append)
            except OSError as exc:
                QMessageBox.critical(self, "Could not open log file", str(exc))
                self._log_action.setChecked(False)
        else:
            self._close_logger()

    def _close_logger(self) -> None:
        if self._hdf5_logger is not None:
            if self._pipeline is not None:
                self._pipeline.remove_result_callback(self._hdf5_logger.append)
            self._hdf5_logger.close()
            self._hdf5_logger = None

    def _on_start(self) -> None:
        if self._pipeline is not None:
            return
        if not any(c.enabled for c in self._config.channels):
            QMessageBox.information(
                self, "No channels enabled",
                "Enable at least one AI channel in the Live Read-out table before starting.",
            )
            return

        # Only acquire the physical AI channels actually referenced by an enabled
        # channel -- acquiring every channel a device has (e.g. all 4 on a 4431) when
        # only one is in use wastes real bandwidth for no benefit, since enabling a
        # channel already requires a restart to take effect anyway.
        effective_config = effective_ai_config(self._config)

        try:
            backend = self._build_backend(effective_config)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Cannot start acquisition", str(exc))
            return

        try:
            self._data_store.reset()
            self._pipeline = LockinPipeline(effective_config, backend)
            self._pipeline.add_result_callback(self._data_store.ingest)
            if self._hdf5_logger is not None:
                self._pipeline.add_result_callback(self._hdf5_logger.append)
            self._pipeline.start()
        except Exception as exc:
            logger.exception("Failed to start pipeline")
            QMessageBox.critical(self, "Failed to start acquisition", str(exc))
            self._pipeline = None
            return

        try:
            self._start_ao_outputs()
        except Exception as exc:
            logger.exception("Failed to start AO outputs")
            QMessageBox.critical(self, "Failed to start reference output", str(exc))
            self._pipeline.stop()
            self._pipeline = None
            return

        self._set_running_state(True)

    def _on_stop(self) -> None:
        if self._pipeline is None:
            return
        self._stop_ao_outputs()
        self._pipeline.stop()
        self._pipeline = None
        self._set_running_state(False)

    def _start_ao_outputs(self) -> None:
        enabled = [a for a in self._config.ao_channels if a.enabled]
        if not enabled:
            self._ao_status_label.setText("")
            return

        if self._backend_combo.currentText() == _BACKEND_SIMULATED:
            self._ao_status_label.setText(f"AO: {len(enabled)} configured (inactive in Simulated mode)")
            self._ao_status_label.setStyleSheet(f"color: {WARN};")
            return

        # Each output's channel is a full "Device/aoN" path, and outputs can span
        # different cards -- look up the voltage range per channel's own device
        # rather than assuming one shared device for all of them.
        specs = []
        for a in enabled:
            device_name = a.ao_channel.split("/", 1)[0]
            device = discovery.find_device(device_name)
            voltage_range = (device.ao_voltage_range if device else None) or (-10.0, 10.0)
            specs.append(
                AOChannelSpec(
                    ao_channel=a.ao_channel,
                    tones=[ToneSpec(frequency_hz=a.frequency_hz, amplitude_v=a.amplitude_v)],
                    voltage_range=voltage_range,
                )
            )
        self._ao_generator = AOStimulusGenerator(
            sample_rate_hz=self._config.acquisition.sample_rate_hz,
            buffer_size=self._config.acquisition.block_size,
            channels=specs,
        )
        self._ao_generator.start()
        self._ao_status_label.setText(f"AO: {len(enabled)} generating")
        self._ao_status_label.setStyleSheet(f"color: {GOOD};")

    def _stop_ao_outputs(self) -> None:
        if self._ao_generator is not None:
            self._ao_generator.stop()
            self._ao_generator = None
        self._ao_status_label.setText("")

    def _build_backend(self, config: LockinConfig) -> AcquisitionBackend:
        choice = self._backend_combo.currentText()
        if choice == _BACKEND_SIMULATED:
            return SimulatedBackend(config.acquisition, config.channels)

        from coherence.daq.nidaq_backend import NIDaqBackend  # local import: optional dependency

        return NIDaqBackend(config.acquisition)

    def _on_ai_channel_enabled_changed(self, row: int, checked: bool) -> None:
        if 0 <= row < len(self._config.channels):
            self._config.channels[row].enabled = checked

    def _on_ao_channels_edited(self) -> None:
        self._config.ao_channels = self._outputs_panel.get_channels()
        if self._ao_generator is not None:
            # Hot-swap just the AO task with the edited waveform -- the AI side is
            # untouched, so this doesn't interrupt acquisition at all.
            try:
                self._stop_ao_outputs()
                self._start_ao_outputs()
            except Exception as exc:
                logger.exception("Failed to apply AO output edit")
                QMessageBox.warning(self, "Invalid output configuration", str(exc))

    def _set_running_state(self, running: bool) -> None:
        self._start_action.setEnabled(not running)
        self._stop_action.setEnabled(running)
        self._backend_combo.setEnabled(not running)
        self._config_action.setEnabled(not running)
        self._channel_table.set_editable(not running)
        self._update_status_pill(running)

    # ------------------------------------------------------------------ refresh loop
    def _refresh_ui(self) -> None:
        if self._pipeline is not None:
            stats = self._pipeline.stats
            if stats.last_error is not None:
                # The FFT worker thread died -- acquisition looked "RUNNING" but was never
                # going to produce another block. Tear down and tell the user why, instead
                # of leaving the UI stuck showing RUNNING with no data forever.
                error_text = stats.last_error
                self._on_stop()
                QMessageBox.critical(
                    self, "Acquisition stopped unexpectedly",
                    f"The FFT processing thread failed and acquisition was stopped:\n\n{error_text}",
                )
                return
            self._rate_label.setText(f"Update rate: {stats.measured_update_rate_hz:,.1f} Hz")
            self._overrun_label.setText(f"Overruns: {stats.overruns}")

        latest = self._data_store.latest()
        if not latest:
            return
        self._channel_table.update_latest(latest)

        # Only repaint the tab the user is actually looking at. Painting every plot
        # widget on every tick -- visible or not -- was the main GUI-thread load, and on
        # Windows the GUI thread competes with the DAQmx callback thread for the GIL,
        # so wasted painting translated directly into acquisition overruns.
        current = self._tabs.currentWidget()
        if current is self._amp_phase_view or current is self._polar_view:
            series, _ = self._data_store.snapshot()
            if series:
                if current is self._amp_phase_view:
                    self._amp_phase_view.update_from_snapshot(series)
                else:
                    self._polar_view.update_from_snapshot(series)
        elif current is self._spectrum_view:
            spectra = self._data_store.latest_spectra()
            if spectra:
                self._spectrum_view.update_from_snapshot(spectra)

    def closeEvent(self, event) -> None:
        self._stop_ao_outputs()
        if self._pipeline is not None:
            self._pipeline.stop()
        self._close_logger()
        super().closeEvent(event)
