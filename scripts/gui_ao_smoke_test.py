"""Verifies the AO control integrated into the main GUI (not the standalone
loopback script) actually works end to end against real hardware: builds the
real MainWindow, configures an AO reference channel + matching AI demod channel,
switches to the NI-DAQmx backend, clicks Start via the real code path, and checks
that the AO status label and recovered amplitude both look right.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from coherence.config import (  # noqa: E402
    AcquisitionConfig,
    AOChannelConfig,
    ChannelConfig,
    LockinConfig,
)
from coherence.daq import discovery  # noqa: E402
from coherence.ui.main_window import MainWindow, _BACKEND_HARDWARE  # noqa: E402
from coherence.ui.theme import DARK_STYLESHEET  # noqa: E402


def main() -> int:
    devices = discovery.list_devices()
    if not devices or not devices[0].ao_channel_names:
        print("No AO-capable NI device detected -- skipping.")
        return 0
    dev = devices[0]

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sample_rate = 51_200.0
    block_size = 2048
    freq = 25 * (sample_rate / block_size)  # 625 Hz, coherent

    window._config = LockinConfig(
        acquisition=AcquisitionConfig(
            sample_rate_hz=sample_rate,
            block_size=block_size,
            overlap_fraction=0.5,
            window="blackmanharris",
            device_name=dev.name,
            ai_channels=("ai0",),
            input_range_v=10.0,
        ),
        channels=[ChannelConfig(name="CH1", frequency_hz=freq, input_channel=0)],
        ao_channels=[AOChannelConfig(name="REF1", frequency_hz=freq, ao_channel="ao0", amplitude_v=1.0)],
    )
    window._apply_channels_to_widgets()

    idx = window._backend_combo.findText(_BACKEND_HARDWARE)
    assert idx >= 0
    window._backend_combo.setCurrentIndex(idx)

    window._on_start()
    assert window._pipeline is not None, "pipeline failed to start"
    assert window._ao_generator is not None, "AO generator failed to start"
    print(f"AO status label: {window._ao_status_label.text()!r}")

    elapsed = 0
    while elapsed < 2500:
        app.processEvents()
        QApplication.instance().thread().msleep(100)
        elapsed += 100

    series, _ = window._data_store.snapshot()
    assert "CH1" in series and series["CH1"].amplitude.size > 0
    amp = series["CH1"].amplitude[-1]
    print(f"CH1 recovered amplitude: {amp:.4f} V (injected 1.0 V)")
    assert 0.85 < amp < 1.15, f"amplitude {amp} not close to injected 1.0 V"

    window._on_stop()
    assert window._pipeline is None
    assert window._ao_generator is None
    print(f"AO status label after stop: {window._ao_status_label.text()!r}")

    window.close()
    print("GUI AO SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
