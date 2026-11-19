"""Light and dark themes (Fusion style + palette + a small stylesheet)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"

_LIGHT = {
    "window": "#f5f6f8", "base": "#ffffff", "alt": "#f0f2f5", "text": "#1f2328", "muted": "#5b6470",
    "button": "#ffffff", "border": "#d0d7de", "canvas": "#e6e8eb", "hover": "#eef2f7",
}
_DARK = {
    "window": "#1e1f22", "base": "#26282c", "alt": "#2b2d31", "text": "#e6e6e6", "muted": "#a0a4ab",
    "button": "#2f3136", "border": "#3d4046", "canvas": "#151618", "hover": "#383b41",
}


def colors(dark: bool) -> dict:
    return dict(_DARK if dark else _LIGHT, accent=ACCENT)


def apply_theme(app: QApplication, dark: bool) -> None:
    c = colors(dark)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["base"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["alt"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["button"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["base"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QWidget {{ font-size: 10pt; }}
        QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 14px;
                     padding: 10px 8px 8px 8px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QPushButton, QToolButton {{ border: 1px solid {c['border']}; border-radius: 6px;
                     padding: 5px 10px; background: {c['button']}; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; }}
        QToolButton:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
        QPushButton[primary="true"] {{ background: {ACCENT}; color: white; border-color: {ACCENT};
                     font-weight: 600; padding: 7px 14px; }}
        QPushButton[primary="true"]:hover {{ background: #1d4ed8; }}
        QPushButton[primary="true"]:disabled {{ background: {c['border']}; color: {c['muted']};
                     border-color: {c['border']}; }}
        QTreeWidget, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{ border: 1px solid {c['border']};
                     border-radius: 6px; padding: 3px; background: {c['base']}; }}
        QProgressBar {{ border: 1px solid {c['border']}; border-radius: 6px; text-align: center;
                     background: {c['base']}; height: 18px; }}
        QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
        QLabel[muted="true"] {{ color: {c['muted']}; }}
        QLabel[banner="ok"] {{ background: #dcfce7; color: #14532d; border-radius: 6px; padding: 6px 10px; }}
        QLabel[banner="warn"] {{ background: #fef3c7; color: #78350f; border-radius: 6px; padding: 6px 10px; }}
        QLabel[banner="error"] {{ background: #fee2e2; color: #7f1d1d; border-radius: 6px; padding: 6px 10px; }}
        QLabel[banner="info"] {{ background: {c['alt']}; color: {c['text']}; border-radius: 6px; padding: 6px 10px; }}
        QGraphicsView {{ border: 1px solid {c['border']}; border-radius: 6px; background: {c['canvas']}; }}
        QStatusBar {{ border-top: 1px solid {c['border']}; }}
    """)


def system_prefers_dark(app: QApplication) -> bool:
    try:
        from PySide6.QtCore import Qt

        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False
