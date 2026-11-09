"""Dark theme (Fusion style + dark palette)."""
from PySide6.QtCore import QLocale
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_dark(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    base, alt, text = QColor(30, 31, 34), QColor(38, 40, 44), QColor(225, 227, 230)
    for role, color in (
        (QPalette.Window, QColor(45, 47, 51)), (QPalette.WindowText, text), (QPalette.Base, base),
        (QPalette.AlternateBase, alt), (QPalette.ToolTipBase, QColor(60, 62, 66)), (QPalette.ToolTipText, text),
        (QPalette.Text, text), (QPalette.Button, QColor(58, 60, 65)), (QPalette.ButtonText, text),
        (QPalette.BrightText, QColor(255, 90, 90)), (QPalette.Link, QColor(110, 170, 255)),
        (QPalette.Highlight, QColor(52, 112, 200)), (QPalette.HighlightedText, QColor(255, 255, 255)),
        (QPalette.PlaceholderText, QColor(130, 133, 138)),
    ):
        p.setColor(role, color)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(120, 122, 126))
    app.setPalette(p)


def use_arabic_digits() -> None:
    """With a Thai Windows locale Qt writes numbers in Thai digits (spin boxes show ๔๐ instead of 40).
    Use a locale with Arabic digits for the whole program; call before any widget is created."""
    QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
