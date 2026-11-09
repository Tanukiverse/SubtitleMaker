from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)


class GlossaryDialog(QDialog):
    """Review the names/terms the model found: untick the wrong ones, fix the translations, then add the rest
    to the glossary."""

    def __init__(self, terms: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("ชื่อและศัพท์เฉพาะที่พบ")
        self.table = QTableWidget(len(terms), 2)
        self.table.setHorizontalHeaderLabels(["ต้นฉบับ (ติ๊ก = ใช้)", "คำแปล (แก้ได้)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r, (a, b) in enumerate(terms):
            it = QTableWidgetItem(a)
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            it.setCheckState(Qt.Checked)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(b))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("เพิ่มเข้าอภิธานศัพท์")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("โมเดลเดาคำแปลของชื่อ/ศัพท์เหล่านี้ ตรวจและแก้ก่อน แล้วค่อยกดแปลทั้งหมด จะได้ชื่อตรงกันทั้งเรื่อง"))
        lay.addWidget(self.table)
        lay.addWidget(bb)
        self.resize(520, 520)

    def chosen(self) -> dict[str, str]:
        out = {}
        for r in range(self.table.rowCount()):
            a, b = self.table.item(r, 0), self.table.item(r, 1)
            if a.checkState() == Qt.Checked and b.text().strip():
                out[a.text()] = b.text().strip()
        return out
