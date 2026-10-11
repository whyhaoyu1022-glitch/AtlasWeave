"""Central visual tokens from specification section 6."""

import sys
from dataclasses import dataclass

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    background: str = "#F3F5F7"
    panel: str = "#FFFFFF"
    secondary_panel: str = "#F8F9FB"
    canvas_surround: str = "#DDE2E7"
    text: str = "#1F2933"
    secondary_text: str = "#5E6C7A"
    disabled_text: str = "#9AA5B1"
    border: str = "#CDD4DB"
    separator: str = "#E2E7EC"
    primary: str = "#1769E0"
    hover: str = "#2D7AEB"
    pressed: str = "#0F56BE"
    warning: str = "#D97706"
    error: str = "#D92D20"
    success: str = "#14804A"


COLORS = Colors()


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: COLORS.background,
        QPalette.ColorRole.WindowText: COLORS.text,
        QPalette.ColorRole.Base: COLORS.panel,
        QPalette.ColorRole.AlternateBase: COLORS.secondary_panel,
        QPalette.ColorRole.Text: COLORS.text,
        QPalette.ColorRole.Button: COLORS.secondary_panel,
        QPalette.ColorRole.ButtonText: COLORS.text,
        QPalette.ColorRole.Highlight: COLORS.primary,
        QPalette.ColorRole.HighlightedText: COLORS.panel,
        QPalette.ColorRole.ToolTipBase: COLORS.panel,
        QPalette.ColorRole.ToolTipText: COLORS.text,
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS.disabled_text))
    app.setPalette(palette)
    font = QFont(app.font())
    preferred = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
    if sys.platform != "darwin" and preferred in QFontDatabase.families():
        font.setFamily(preferred)
    # 9.75 pt corresponds to 13 px at 96 DPI and follows system scaling (§45).
    font.setPointSizeF(9.75)
    app.setFont(font)
    app.setStyleSheet(f"""
        QMenuBar {{ background: {COLORS.panel}; border-bottom: 1px solid {COLORS.separator}; }}
        QMenuBar::item {{ padding: 6px 12px; }}
        QMenu::item {{ padding: 6px 24px; min-height: 14px; }}
        QStatusBar {{ background: {COLORS.panel}; border-top: 1px solid {COLORS.border}; }}
        QFrame#emptyWorkspace {{ background: {COLORS.canvas_surround}; }}
        QLabel#workspaceTitle {{ color: {COLORS.secondary_text}; }}
        QPushButton {{ min-height: 28px; padding: 0 12px; }}
    """)
