"""Application entry point."""

from __future__ import annotations

import logging
import sys

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

from coherence.ui.main_window import MainWindow
from coherence.ui.theme import BG_PANEL, DARK_STYLESHEET, TEXT_PRIMARY


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # antialias=False is deliberate: antialiased line drawing at 30 Hz was the single
    # biggest GUI-thread cost, and on Windows the GUI thread competes with the DAQmx
    # callback thread for the GIL -- pretty lines are not worth dropped samples.
    pg.setConfigOptions(antialias=False, background=BG_PANEL, foreground=TEXT_PRIMARY)

    app = QApplication(sys.argv)
    app.setApplicationName("FDM Lock-In")
    app.setOrganizationName("coherence")
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
