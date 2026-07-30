"""Reproduces the user's exact reported scenario, made deliberately harsher:
launch the real app, autoconfigure against the real connected card, enable EVERY
AI channel it has (not just the default AI0), and cycle through the plot tabs
while running -- the tab-switching is what exposed the GUI-painting bottleneck
that used to starve the DAQmx callback into a continuous overrun storm.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from coherence.daq import discovery  # noqa: E402
from coherence.ui.main_window import MainWindow, _BACKEND_HARDWARE  # noqa: E402
from coherence.ui.theme import DARK_STYLESHEET  # noqa: E402

RUN_SECONDS = 8.0


def main() -> int:
    devices = discovery.list_devices()
    if not devices:
        print("No NI device detected -- skipping.")
        return 0

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    window = MainWindow()
    window.show()

    for ch in window._config.channels:
        ch.enabled = True  # harshest case: demodulate every physical AI channel at once
    window._apply_channels_to_widgets()

    print(f"Autoconfigured: device={window._config.acquisition.device_name} "
          f"ai_channels={window._config.acquisition.ai_channels} "
          f"enabled_channels={[c.name for c in window._config.channels if c.enabled]}")
    assert window._backend_combo.currentText() == _BACKEND_HARDWARE

    window._on_start()
    assert window._pipeline is not None
    print(f"Pipeline actually acquiring: ai_channels={window._pipeline._config.acquisition.ai_channels}")

    tabs = [window._amp_phase_view, window._spectrum_view, window._polar_view, window._hardware_panel]
    elapsed = 0
    step = 250
    tab_idx = 0
    while elapsed < RUN_SECONDS * 1000:
        if elapsed % 1000 == 0:  # switch plot tab every second, like a user poking around
            window._tabs.setCurrentWidget(tabs[tab_idx % len(tabs)])
            tab_idx += 1
        app.processEvents()
        QApplication.instance().thread().msleep(step)
        elapsed += step
        stats = window._pipeline.stats
        print(f"  t={elapsed/1000:.2f}s blocks={stats.blocks_processed} "
              f"overruns={stats.overruns} rate={stats.measured_update_rate_hz:.1f}Hz "
              f"running={stats.running} tab={type(window._tabs.currentWidget()).__name__}")
        if not stats.running:
            print("PIPELINE DIED -- FAIL")
            return 1

    final = window._pipeline.stats
    window._on_stop()
    window.close()

    ok = final.overruns == 0 and final.blocks_processed > 0
    print(f"\nFinal: blocks_processed={final.blocks_processed} overruns={final.overruns}")
    print("LONG-RUN TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
