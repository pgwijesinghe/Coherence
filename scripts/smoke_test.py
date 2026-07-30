"""Headless integration smoke test: builds the real UI (offscreen), starts the
simulated backend, pumps the event loop for a couple seconds, and checks that
live data actually reached the widgets before tearing down cleanly.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from coherence.ui.main_window import MainWindow  # noqa: E402
from coherence.ui.theme import DARK_STYLESHEET  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    window._on_start()
    assert window._pipeline is not None, "pipeline failed to start"

    deadline_ms = 3000
    elapsed = 0
    step = 100
    while elapsed < deadline_ms:
        app.processEvents()
        QApplication.instance().thread().msleep(step)
        elapsed += step

    series, spectra = window._data_store.snapshot()
    assert series, "no channel data arrived in data store"
    for name, s in series.items():
        assert s.amplitude.size > 0, f"channel {name} has no samples"
        print(f"{name}: amplitude={s.amplitude[-1]:.4f} phase_deg={__import__('numpy').degrees(s.phase_rad[-1]):.2f}")
    assert spectra, "no spectrum data arrived"
    stats = window._pipeline.stats
    print(f"blocks_processed={stats.blocks_processed} measured_update_rate_hz={stats.measured_update_rate_hz:.1f} overruns={stats.overruns}")
    assert stats.blocks_processed > 0
    assert stats.overruns == 0

    window._on_stop()
    assert window._pipeline is None
    window.close()
    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
