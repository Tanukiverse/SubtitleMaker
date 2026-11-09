from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QWidget)

from ..core.settings import ASR_MODELS, Settings

LANGS = [("auto", "ตรวจอัตโนมัติ"), ("ja", "ญี่ปุ่น"), ("en", "อังกฤษ"), ("zh", "จีน"), ("ko", "เกาหลี"), ("th", "ไทย")]


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, ollama_models: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("ตั้งค่า")
        self.s = s
        f = QFormLayout(self)

        self.asr = QComboBox()
        self.asr.addItems(ASR_MODELS)
        self.asr.setCurrentText(s.asr_model)
        f.addRow("โมเดลถอดเสียง (Whisper)", self.asr)

        self.src = QComboBox()
        for code, name in LANGS:
            self.src.addItem(f"{name} ({code})", code)
        self.src.setCurrentIndex(max(0, self.src.findData(s.source_lang)))
        f.addRow("ภาษาในเสียง", self.src)

        self.gpu = QCheckBox("ใช้ GPU")
        self.gpu.setChecked(s.use_gpu)
        self.vad = QCheckBox("ตัดช่วงเงียบด้วย VAD (ลดคำหลอน)")
        self.vad.setChecked(s.vad)
        self.gaps = QCheckBox("ถอดซ้ำช่วงที่ซับหายแต่มีเสียงดัง (เพลง/เสียงเบา)")
        self.gaps.setChecked(s.fill_gaps)
        f.addRow("", self.gpu)
        f.addRow("", self.vad)
        f.addRow("", self.gaps)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(ollama_models)
        self.model.setCurrentText(s.ollama_model)
        f.addRow("โมเดลแปล (Ollama)", self.model)
        self.url = QLineEdit(s.ollama_url)
        f.addRow("Ollama URL", self.url)

        self.chars = QSpinBox(); self.chars.setRange(15, 80); self.chars.setValue(s.max_chars_per_line)
        self.lines = QSpinBox(); self.lines.setRange(1, 3); self.lines.setValue(s.max_lines)
        self.cps = QDoubleSpinBox(); self.cps.setRange(5, 40); self.cps.setValue(s.max_cps)
        self.mind = QDoubleSpinBox(); self.mind.setRange(0.3, 5); self.mind.setSingleStep(0.1); self.mind.setValue(s.min_duration)
        for w in (self.chars, self.lines, self.cps, self.mind):
            w.setMinimumWidth(90)
        f.addRow("ตัวอักษรต่อบรรทัด", self.chars)
        f.addRow("จำนวนบรรทัดสูงสุด", self.lines)
        f.addRow("ความเร็วอ่านสูงสุด (ตัวอักษร/วินาที)", self.cps)
        f.addRow("เวลาแสดงขั้นต่ำ (วินาที)", self.mind)

        self.out = QLineEdit(s.out_dir)
        self.out.setPlaceholderText("ว่าง = ข้างไฟล์วิดีโอ")
        b = QPushButton("เลือก…")
        b.clicked.connect(self._pick_out)
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.addWidget(self.out); h.addWidget(b)
        f.addRow("โฟลเดอร์ส่งออก .srt", row)
        self.src_srt = QCheckBox("เขียน .srt ต้นฉบับ (ก่อนแปล) ด้วย")
        self.src_srt.setChecked(s.write_source_srt)
        f.addRow("", self.src_srt)

        self.gloss = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in s.glossary.items()))
        self.gloss.setPlaceholderText("อภิธานศัพท์ บรรทัดละคำ เช่น\n先輩=รุ่นพี่\nSenpai=รุ่นพี่")
        self.gloss.setFixedHeight(90)
        f.addRow("อภิธานศัพท์", self.gloss)
        self.notes = QLineEdit(s.story_notes)
        self.notes.setPlaceholderText("เช่น อนิเมะโรงเรียน แนวตลก")
        f.addRow("บริบทของเรื่อง", self.notes)

        # ---- look
        self.tfont = QSpinBox(); self.tfont.setRange(8, 24); self.tfont.setValue(s.table_font_size)
        self.efont = QSpinBox(); self.efont.setRange(8, 32); self.efont.setValue(s.edit_font_size)
        self.spct = QDoubleSpinBox(); self.spct.setRange(2, 15); self.spct.setSingleStep(0.5); self.spct.setValue(s.sub_size_pct)
        self.spct.setSuffix(" % ของความสูงภาพ")
        self.bpct = QDoubleSpinBox(); self.bpct.setRange(0, 40); self.bpct.setSingleStep(0.5); self.bpct.setValue(s.sub_bottom_pct)
        self.bpct.setSuffix(" % จากขอบล่าง")
        for w in (self.tfont, self.efont, self.spct, self.bpct):
            w.setMinimumWidth(150)
        self.c_text, self.c_out = s.sub_color, s.sub_outline
        self.b_text, self.b_out = QPushButton(), QPushButton()
        self.b_text.clicked.connect(lambda: self._pick_color("text"))
        self.b_out.clicked.connect(lambda: self._pick_color("out"))
        self._paint_buttons()
        self.burn_font = QLineEdit(s.burn_font)
        f.addRow("ขนาดฟอนต์ตาราง", self.tfont)
        f.addRow("ขนาดฟอนต์กล่องแก้ไข", self.efont)
        f.addRow("ขนาดซับบนวิดีโอ", self.spct)
        f.addRow("ตำแหน่งซับ", self.bpct)
        f.addRow("สีตัวอักษรซับ", self.b_text)
        f.addRow("สีขอบซับ", self.b_out)
        f.addRow("ฟอนต์ซับตอนฝังลงวิดีโอ", self.burn_font)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.setMinimumWidth(520)

    def _paint_buttons(self) -> None:
        for b, c in ((self.b_text, self.c_text), (self.b_out, self.c_out)):
            b.setText(c)
            b.setStyleSheet(f"background:{c}; color:{'#000' if QColor(c).lightness() > 128 else '#fff'};")

    def _pick_color(self, which: str) -> None:
        cur = QColor(self.c_text if which == "text" else self.c_out)
        c = QColorDialog.getColor(cur, self, "เลือกสี")
        if c.isValid():
            if which == "text":
                self.c_text = c.name()
            else:
                self.c_out = c.name()
            self._paint_buttons()

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "โฟลเดอร์ส่งออก", self.out.text())
        if d:
            self.out.setText(d)

    def apply(self) -> Settings:
        s = self.s
        s.asr_model = self.asr.currentText()
        s.source_lang = self.src.currentData()
        s.use_gpu = self.gpu.isChecked()
        s.vad = self.vad.isChecked()
        s.fill_gaps = self.gaps.isChecked()
        s.ollama_model = self.model.currentText().strip()
        s.ollama_url = self.url.text().strip().rstrip("/")
        s.max_chars_per_line = self.chars.value()
        s.max_lines = self.lines.value()
        s.max_cps = self.cps.value()
        s.min_duration = self.mind.value()
        s.out_dir = self.out.text().strip()
        s.write_source_srt = self.src_srt.isChecked()
        s.glossary = {k.strip(): v.strip() for k, _, v in (ln.partition("=") for ln in self.gloss.toPlainText().splitlines())
                      if k.strip() and v.strip()}
        s.story_notes = self.notes.text().strip()
        s.table_font_size, s.edit_font_size = self.tfont.value(), self.efont.value()
        s.sub_size_pct, s.sub_bottom_pct = self.spct.value(), self.bpct.value()
        s.sub_color, s.sub_outline = self.c_text, self.c_out
        s.burn_font = self.burn_font.text().strip() or "Leelawadee UI"
        return s
