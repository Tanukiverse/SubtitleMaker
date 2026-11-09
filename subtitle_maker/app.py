"""python -m subtitle_maker.app [video ...]"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.theme import apply_dark, use_arabic_digits


def main() -> int:
    app = QApplication(sys.argv)
    use_arabic_digits()
    apply_dark(app)
    w = MainWindow()
    w.show()
    w.add_files(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
