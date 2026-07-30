"""Verifies editing the Outputs table's frequency WHILE RUNNING hot-swaps the AO
generator without disturbing AI acquisition -- against real hardware (AO0 -> AI0).
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


def _pump(app, ms):
    elapsed = 0
    while elapsed < ms:
        app.processEvents()
        QApplication.instance().thread().msleep(100)
        elapsed += 100


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

    sample_rate, block_size = 51_200.0, 2048
    freq_a = 25 * (sample_rate / block_size)  # 625 Hz
    freq_b = 48 * (sample_rate / block_size)  # 1200 Hz

    window._config = LockinConfig(
        acquisition=AcquisitionConfig(
            sample_rate_hz=sample_rate, block_size=block_size, overlap_fraction=0.5,
            window="blackmanharris", ai_channels=(dev.ai_channel_names[0],), input_range_v=10.0,
        ),
        channels=[ChannelConfig(name="CH1", frequency_hz=freq_a, input_channel=0)],
        ao_channels=[AOChannelConfig(name="REF1", frequency_hz=freq_a, ao_channel=dev.ao_channel_names[0], amplitude_v=1.0)],
    )
    window._apply_channels_to_widgets()
    idx = window._backend_combo.findText(_BACKEND_HARDWARE)
    window._backend_combo.setCurrentIndex(idx)

    window._on_start()
    assert window._pipeline is not None and window._ao_generator is not None
    _pump(app, 2000)

    series, _ = window._data_store.snapshot()
    amp_before = series["CH1"].amplitude[-1]
    print(f"Before edit: CH1 amplitude at {freq_a:.0f} Hz = {amp_before:.4f} V (expect ~1.0)")
    assert 0.85 < amp_before < 1.15

    # Live-edit the AO frequency to freq_b while acquisition keeps running.
    print(f"Editing AO output frequency live: {freq_a:.0f} Hz -> {freq_b:.0f} Hz")
    window._outputs_panel.item(0, 2).setText(f"{freq_b:.1f}")
    assert abs(window._config.ao_channels[0].frequency_hz - freq_b) < 1e-6

    _pump(app, 2000)
    window._data_store.reset()  # drop pre-edit history so we only look at post-edit data
    _pump(app, 2000)

    series, _ = window._data_store.snapshot()
    amp_at_old_freq = series["CH1"].amplitude[-1]
    print(f"After edit: CH1 (still demodulating {freq_a:.0f} Hz) amplitude = {amp_at_old_freq:.4f} V "
          f"(expect ~0, since AO moved away from this frequency)")

    window._on_stop()
    assert window._pipeline is None and window._ao_generator is None
    window.close()

    ok = amp_at_old_freq < 0.15
    print("LIVE AO EDIT TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
