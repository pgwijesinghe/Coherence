"""Dark instrumentation theme, in the vein of NI/Zurich-Instruments style lab software."""

from __future__ import annotations

# Okabe-Ito colorblind-safe qualitative palette; cycled across channels/plot series.
CHANNEL_COLORS: list[str] = [
    "#56B4E9",  # sky blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#F0E442",  # yellow
    "#D55E00",  # vermillion
    "#0072B2",  # blue
    "#999999",  # gray
]

BG_DARKEST = "#14161a"
BG_DARK = "#1b1e24"
BG_PANEL = "#20242c"
BG_ELEVATED = "#282d37"
BORDER = "#343a46"
TEXT_PRIMARY = "#e6e8ec"
TEXT_SECONDARY = "#9aa2b1"
ACCENT = "#56B4E9"
ACCENT_HOVER = "#79c6f2"
GOOD = "#2ecc71"
BAD = "#e74c3c"
WARN = "#f39c12"


def channel_color(index: int) -> str:
    return CHANNEL_COLORS[index % len(CHANNEL_COLORS)]


DARK_STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    color: {TEXT_PRIMARY};
}}
QMainWindow, QDialog {{
    background-color: {BG_DARKEST};
}}
QWidget {{
    background-color: transparent;
}}
QToolBar {{
    background-color: {BG_DARK};
    border-bottom: 1px solid {BORDER};
    padding: 4px;
    spacing: 6px;
}}
QToolBar QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 5px 10px;
}}
QToolBar QToolButton:hover {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
}}
QToolBar QToolButton:checked {{
    background-color: {ACCENT};
    color: #0b0d10;
}}
QStatusBar {{
    background-color: {BG_DARK};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background-color: {BG_PANEL};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {BG_DARK};
    color: {TEXT_SECONDARY};
    padding: 7px 16px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}
QTableWidget {{
    background-color: {BG_PANEL};
    alternate-background-color: {BG_DARK};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #0b0d10;
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {TEXT_SECONDARY};
    padding: 5px;
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: 600;
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QPushButton {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: #323844;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {BG_DARK};
}}
QPushButton:default {{
    background-color: {ACCENT};
    color: #0b0d10;
    font-weight: 600;
    border: none;
}}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {ACCENT};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #0b0d10;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {BG_ELEVATED};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QScrollBar:vertical {{
    background: {BG_DARK};
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BG_ELEVATED};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QLabel#StatusPill {{
    border-radius: 8px;
    padding: 2px 10px;
    font-weight: 600;
}}
"""
