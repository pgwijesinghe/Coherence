"""DebugPanel: the Debug tab surfacing live log output, multi-device sync status,
and pipeline telemetry. Covers the widget's own logic directly -- attach/detach of
the Qt logging bridge, and the text it renders for run info / sync report / stats --
independent of whether a real acquisition happens to log anything, which the GUI
smoke test (scripts/gui_streaming_debug_smoke_test.py) exercises end to end.
"""

import logging

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.core.pipeline import PipelineStats
from coherence.ui.widgets.debug_panel import DebugPanel


def test_starts_with_no_run_info(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    assert panel._summary_label.text() == "No acquisition running."
    assert panel._rate_label.text() == "Update rate: --"


def test_set_run_info_shows_fft_engine_details(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    config = LockinConfig(
        acquisition=AcquisitionConfig(sample_rate_hz=51_200.0, block_size=2048, engine="fft"),
        channels=[ChannelConfig(name="CH1", frequency_hz=1000.0)],
    )
    panel.set_run_info(config, "Simulated")
    text = panel._summary_label.text()
    assert "Simulated" in text
    assert "FFT" in text
    assert "block_size=2048" in text


def test_set_run_info_shows_streaming_engine_details(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    config = LockinConfig(
        acquisition=AcquisitionConfig(sample_rate_hz=51_200.0, engine="streaming"),
        channels=[ChannelConfig(name="CH1", frequency_hz=1000.0, time_constant_s=0.03)],
    )
    panel.set_run_info(config, "NI-DAQmx (hardware)")
    text = panel._summary_label.text()
    assert "NI-DAQmx" in text
    assert "Streaming" in text


def test_clear_run_info_resets_everything(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    config = LockinConfig(acquisition=AcquisitionConfig(), channels=[ChannelConfig(name="CH1", frequency_hz=1000.0)])
    panel.set_run_info(config, "Simulated")
    panel.set_sync_report(["Reference clock: PXIe_Clk100"], ["Start trigger routed"])
    panel.update_stats(PipelineStats(blocks_processed=10, overruns=1, measured_update_rate_hz=42.0, running=True))

    panel.clear_run_info()
    assert panel._summary_label.text() == "No acquisition running."
    assert panel._rate_label.text() == "Update rate: --"
    assert panel._overrun_label.text() == "Overruns: --"
    assert panel._blocks_label.text() == "Blocks processed: --"
    assert panel._sync_text.toPlainText() == ""


def test_update_stats_reflects_pipeline_stats(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    panel.update_stats(PipelineStats(blocks_processed=123, overruns=0, measured_update_rate_hz=42.37, running=True))
    assert "42.4" in panel._rate_label.text()
    assert "123" in panel._blocks_label.text()
    assert panel._overrun_label.text() == "Overruns: 0"


def test_sync_report_renders_ai_and_ao_sections_separately(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    panel.set_sync_report(["Master: Dev1 (ai task)", "Reference clock: PXIe_Clk100"], ["Master: Dev1 (ao task)"])
    text = panel._sync_text.toPlainText()
    assert "-- AI acquisition --" in text
    assert "-- AO stimulus --" in text
    assert "Reference clock: PXIe_Clk100" in text


def test_sync_report_empty_when_nothing_to_report(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    panel.set_sync_report([], [])
    assert panel._sync_text.toPlainText() == ""


def test_attach_logging_routes_coherence_records_into_the_log_widget(qtbot):
    panel = DebugPanel()
    qtbot.addWidget(panel)
    panel.attach_logging()
    try:
        logging.getLogger("coherence.test_debug_panel").warning("hello from a test")
        qtbot.wait(50)
        assert "hello from a test" in panel._log_text.toPlainText()
    finally:
        panel.detach_logging()


def test_detach_logging_stops_further_records():
    panel = DebugPanel()
    panel.attach_logging()
    panel.detach_logging()
    logging.getLogger("coherence.test_debug_panel").warning("should not appear")
    assert "should not appear" not in panel._log_text.toPlainText()
