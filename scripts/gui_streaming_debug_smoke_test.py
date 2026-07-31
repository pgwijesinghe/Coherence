"""Verifies the streaming engine + Debug tab wired into the real MainWindow: picks
the Simulated backend (no hardware needed), switches acquisition.engine to
"streaming", starts via the real _on_start code path, and checks the Debug tab
actually receives log lines and live pipeline stats.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig  # noqa: E402
from coherence.ui.main_window import MainWindow  # noqa: E402
from coherence.ui.theme import DARK_STYLESHEET  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sample_rate = 20_000.0
    window._config = LockinConfig(
        acquisition=AcquisitionConfig(
            sample_rate_hz=sample_rate,
            engine="streaming",
            ai_channels=("Dev1/ai0",),
        ),
        channels=[ChannelConfig(name="CH1", frequency_hz=1_000.0, input_channel=0, time_constant_s=0.02)],
    )
    window._apply_channels_to_widgets()
    assert window._backend_combo.currentText() == "Simulated"

    window._on_start()
    assert window._pipeline is not None, "pipeline failed to start"

    from coherence.dsp.streaming_engine import StreamingLockinEngine

    assert isinstance(window._pipeline._engine, StreamingLockinEngine)

    elapsed = 0
    while elapsed < 1500:
        app.processEvents()
        QApplication.instance().thread().msleep(50)
        elapsed += 50

    window._refresh_ui()

    summary = window._debug_panel._summary_label.text()
    print(f"Debug summary: {summary!r}")
    assert "Streaming" in summary

    rate_text = window._debug_panel._rate_label.text()
    print(f"Debug rate label: {rate_text!r}")
    assert rate_text != "Update rate: --"

    # Simulated single-device acquisition emits nothing at INFO by itself -- fire a
    # marker record through the same "coherence.*" namespace to prove the Qt log
    # handler wiring itself (attach_logging -> the actual bridge -> the widget).
    import logging

    logging.getLogger("coherence.smoketest").warning("streaming debug smoke test marker")
    app.processEvents()

    log_text = window._debug_panel._log_text.toPlainText()
    print(f"Log lines captured: {len(log_text.splitlines())}")
    assert "streaming debug smoke test marker" in log_text, "Debug tab log is empty -- log handler not wired up"

    series, _ = window._data_store.snapshot()
    assert "CH1" in series and series["CH1"].amplitude.size > 0
    print(f"CH1 amplitude sample: {series['CH1'].amplitude[-1]:.4f}")

    window._on_stop()
    assert window._pipeline is None
    assert window._debug_panel._summary_label.text() == "No acquisition running."

    window.close()
    print("GUI STREAMING + DEBUG SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
