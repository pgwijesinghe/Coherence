"""Application entry point."""

from __future__ import annotations

import logging
import sys


def _print_detected_hardware() -> int:
    """`coherence --list`: print what the driver can see and exit. Meant for
    diagnosing 'why doesn't the app find my card' without launching the GUI."""
    from coherence.daq import discovery

    if not discovery.nidaqmx_available():
        print("The nidaqmx Python package is not installed in this environment.")
        print('Install the hardware extra:  uv pip install -e ".[hardware]"')
        print("(The NI-DAQmx driver itself must also be installed on this machine.)")
        return 1

    version = discovery.driver_version()
    print(f"NI-DAQmx driver: {version or 'installed, but version query failed'}")

    devices = discovery.list_devices()
    if not devices:
        print("No NI devices detected.")
        print("If hardware is connected: power the chassis BEFORE booting the PC,")
        print("then check the devices appear in NI MAX under Devices and Interfaces.")
        return 1

    print(f"{len(devices)} device(s):")
    for d in devices:
        ai_rate = f", max AI rate {d.ai_max_multi_chan_rate_hz:,.0f} Hz" if d.ai_max_multi_chan_rate_hz else ""
        sim = "  [simulated]" if d.is_simulated else ""
        print(f"  {d.name:14s} {d.product_type:16s} AI={len(d.ai_channel_names)} "
              f"AO={len(d.ao_channel_names)}{ai_rate}{sim}")
    if discovery.first_ai_device(devices) is None:
        print("None of these has AI channels, so there is nothing to acquire from --")
        print("the app will fall back to the simulated backend.")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if "--list" in sys.argv:
        return _print_detected_hardware()

    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    from coherence.ui.main_window import MainWindow
    from coherence.ui.theme import BG_PANEL, DARK_STYLESHEET, TEXT_PRIMARY

    # antialias=False is deliberate: antialiased line drawing at 30 Hz was the single
    # biggest GUI-thread cost, and on Windows the GUI thread competes with the DAQmx
    # callback thread for the GIL -- pretty lines are not worth dropped samples.
    pg.setConfigOptions(antialias=False, background=BG_PANEL, foreground=TEXT_PRIMARY)

    app = QApplication(sys.argv)
    app.setApplicationName("Coherence")
    app.setOrganizationName("coherence")
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
