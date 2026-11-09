from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)


class FindDialog(QDialog):
    """Find / replace over the cue list. Non-modal: it stays open next to the table."""
    findNext = Signal(str, str, bool)  # needle, field, case
    replaceOne = Signal(str, str, str, bool)  # needle, repl, field, case
    replaceAll = Signal(str, str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ค้นหา / แทนที่")
        self.setWindowFlag(Qt.Tool, True)
        self.find = QLineEdit(); self.repl = QLineEdit()
        self.field = QComboBox(); self.field.addItem("คำแปล", "th"); self.field.addItem("ต้นฉบับ", "text")
        self.case = QCheckBox("ตัวพิมพ์เล็ก/ใหญ่ต้องตรง")
        self.info = QLabel("")
        f = QFormLayout()
        f.addRow("ค้นหา", self.find); f.addRow("แทนที่ด้วย", self.repl); f.addRow("ในช่อง", self.field); f.addRow("", self.case)
        b1, b2, b3 = QPushButton("หาถัดไป"), QPushButton("แทนที่"), QPushButton("แทนที่ทั้งหมด")
        b1.setDefault(True)
        b1.clicked.connect(lambda: self.findNext.emit(self.find.text(), self.field.currentData(), self.case.isChecked()))
        b2.clicked.connect(lambda: self.replaceOne.emit(self.find.text(), self.repl.text(), self.field.currentData(), self.case.isChecked()))
        b3.clicked.connect(lambda: self.replaceAll.emit(self.find.text(), self.repl.text(), self.field.currentData(), self.case.isChecked()))
        row = QHBoxLayout()
        for b in (b1, b2, b3):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addLayout(f); lay.addLayout(row); lay.addWidget(self.info)
        self.setMinimumWidth(380)

    def show_find(self, replace: bool = False) -> None:
        self.show(); self.raise_(); self.activateWindow()
        (self.repl if replace else self.find).setFocus()
        self.find.selectAll()
