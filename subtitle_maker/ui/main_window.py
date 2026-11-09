"""Main window laid out like Subtitle Edit: cue list (top left) with the edit box under it, video with tabs
(top right), waveform along the bottom."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QMenu, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..core import audio, segment, srt, thai_lines
from ..core.edit import Editor, cps
from ..core.pipeline import STEP_LABELS, STEPS, StepRunner
from ..core.project import Project
from ..core.settings import Settings
from ..core.translate import OllamaTranslator, TranslatorConfig
from .find_dialog import FindDialog
from .settings_dialog import SettingsDialog
from .widgets import TimeSpin, VideoView, Waveform, fmt_time
from .glossary_dialog import GlossaryDialog
from .worker import ExportWorker, RetranslateWorker, TermsWorker, Worker

MEDIA_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts",
             ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
C_NO, C_START, C_END, C_DUR, C_SRC, C_TH = range(6)
BAD = QColor(220, 60, 60)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.s = settings or Settings.load()
        self.setWindowTitle("Subtitle Maker")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.worker: Worker | None = None
        self.proj: Project | None = None
        self.editor: Editor | None = None
        self.current: str | None = None
        self.failures: list[tuple[str, str]] = []
        self._fill = False  # True while code (not the user) changes a widget
        self._seek_on_select = True
        f = self.font(); f.setPointSize(self.s.ui_font_size); self.setFont(f)

        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self.save_project)
        self.find_dlg = FindDialog(self)
        self.find_dlg.findNext.connect(self.on_find_next)
        self.find_dlg.replaceOne.connect(self.on_replace_one)
        self.find_dlg.replaceAll.connect(self.on_replace_all)

        self._build_table_and_edit()
        self._build_right()
        self._build_waveform()
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.left); top.addWidget(self.right)
        top.setStretchFactor(0, 3); top.setStretchFactor(1, 2)
        main = QSplitter(Qt.Vertical)
        main.addWidget(top); main.addWidget(self.wave_box)
        main.setStretchFactor(0, 4); main.setStretchFactor(1, 1)
        self.setCentralWidget(main)

        self.bar = QProgressBar(); self.bar.setMaximumWidth(260); self.bar.setRange(0, 100)
        self.status = QLabel("พร้อม — ลากวิดีโอมาวางในหน้าต่างนี้")
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.bar)
        self._build_menus()
        self.apply_look()
        self._set_busy(False)
        self._update_actions()

    def apply_look(self) -> None:
        """Fonts, alternating row colours and the subtitle style on the video, from settings."""
        tf = self.table.font(); tf.setPointSize(self.s.table_font_size); self.table.setFont(tf)
        for w in (self.src_edit, self.th_edit):
            ef = w.font(); ef.setPointSize(self.s.edit_font_size); w.setFont(ef)
        self.table.verticalHeader().setDefaultSectionSize(int(self.s.table_font_size * 2.1) + 6)
        self.view.sub.set_style(self.s.sub_size_pct, self.s.sub_bottom_pct, self.s.sub_color, self.s.sub_outline)

    # =============================================================== layout
    def _build_table_and_edit(self) -> None:
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["#", "เริ่ม", "จบ", "ยาว", "ต้นฉบับ", "คำแปล"])
        h = self.table.horizontalHeader()
        for c in (C_NO, C_START, C_END, C_DUR):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(C_SRC, QHeaderView.Stretch)
        h.setSectionResizeMode(C_TH, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.currentCellChanged.connect(self.on_row_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_table_menu)

        # ---- edit box (under the list)
        self.src_edit = QPlainTextEdit(); self.src_edit.setPlaceholderText("ต้นฉบับ")
        self.th_edit = QPlainTextEdit(); self.th_edit.setPlaceholderText("คำแปล (พิมพ์แก้ได้ แถวที่แก้จะถูก 🔒)")
        self.src_edit.setFixedHeight(54)
        self.th_edit.setMinimumHeight(70)
        self.src_edit.textChanged.connect(lambda: self.on_text_edited("text"))
        self.th_edit.textChanged.connect(lambda: self.on_text_edited("th"))
        self.info = QLabel("")

        self.t_start, self.t_end, self.t_dur = TimeSpin(), TimeSpin(), TimeSpin()
        self.t_start.valueChanged.connect(lambda _v: self.on_times_edited("start"))
        self.t_end.valueChanged.connect(lambda _v: self.on_times_edited("end"))
        self.t_dur.valueChanged.connect(lambda _v: self.on_times_edited("dur"))
        form = QFormLayout()
        form.addRow("เริ่ม", self.t_start); form.addRow("จบ", self.t_end); form.addRow("ความยาว", self.t_dur)
        b_set_s = QPushButton("เริ่ม = ตำแหน่งเล่น"); b_set_s.clicked.connect(lambda: self.set_time_from_player("start"))
        b_set_e = QPushButton("จบ = ตำแหน่งเล่น"); b_set_e.clicked.connect(lambda: self.set_time_from_player("end"))
        b_prev = QPushButton("◀ ก่อนหน้า"); b_prev.clicked.connect(lambda: self.goto_row(self.row() - 1))
        b_next = QPushButton("ถัดไป ▶"); b_next.clicked.connect(lambda: self.goto_row(self.row() + 1))
        b_ins = QPushButton("＋ เพิ่ม"); b_ins.clicked.connect(self.insert_cue)
        b_del = QPushButton("－ ลบ"); b_del.clicked.connect(self.delete_cues)
        b_split = QPushButton("✂ แยกที่เคอร์เซอร์"); b_split.clicked.connect(self.split_cue)
        b_merge = QPushButton("⛓ รวมกับถัดไป"); b_merge.clicked.connect(self.merge_cue)
        b_br = QPushButton("↵ ตัดบรรทัดอัตโนมัติ"); b_br.clicked.connect(self.auto_break)
        self.b_retr = QPushButton("🔄 แปลใหม่ที่เลือก"); self.b_retr.clicked.connect(self.retranslate_selected)
        right_col = QVBoxLayout()
        right_col.addLayout(form)
        for a, b in ((b_set_s, b_set_e), (b_prev, b_next)):
            r = QHBoxLayout(); r.addWidget(a); r.addWidget(b); right_col.addLayout(r)
        left_col = QVBoxLayout()
        left_col.addWidget(self.src_edit); left_col.addWidget(self.th_edit); left_col.addWidget(self.info)
        r1 = QHBoxLayout()
        for b in (b_ins, b_del, b_split, b_merge, b_br, self.b_retr):
            r1.addWidget(b)
        left_col.addLayout(r1)
        edit_row = QHBoxLayout(); edit_row.addLayout(left_col, 3); edit_row.addLayout(right_col, 1)
        self.edit_panel = QWidget(); self.edit_panel.setLayout(edit_row)

        self.left = QWidget()
        lv = QVBoxLayout(self.left); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.table, 1); lv.addWidget(self.edit_panel)

    def _build_right(self) -> None:
        self.view = VideoView()
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.view.video)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(self.on_player_error)
        self.play_btn = QPushButton("▶"); self.play_btn.setFixedWidth(44)
        self.play_btn.clicked.connect(self.toggle_play)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00:00.000")
        self.sync_box = QCheckBox("ตารางตามวิดีโอ"); self.sync_box.setChecked(True)
        ctl = QHBoxLayout()
        for w in (self.play_btn, self.seek, self.time_label, self.sync_box):
            ctl.addWidget(w)

        # ---- tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_auto(), "อัตโนมัติ")
        self.tabs.addTab(self._tab_queue(), "คิวไฟล์")
        self.tabs.addTab(self._tab_adjust(), "ปรับเวลา")

        self.right = QWidget()
        rv = QVBoxLayout(self.right); rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.view, 3); rv.addLayout(ctl); rv.addWidget(self.tabs, 2)

    def _tab_auto(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        self.step_btns: list[QPushButton] = []
        for st in STEPS:
            b = QPushButton(STEP_LABELS[st]); b.clicked.connect(lambda _=False, st=st: self.run_steps([st]))
            v.addWidget(b); self.step_btns.append(b)
        self.btn_all = QPushButton("▶ ทำทั้งหมด (F5)"); self.btn_all.clicked.connect(lambda: self.run_steps(None))
        self.btn_queue = QPushButton("▶▶ ทำทุกไฟล์ในคิว"); self.btn_queue.clicked.connect(lambda: self.run_steps(None, whole_queue=True))
        self.btn_stop = QPushButton("■ หยุด"); self.btn_stop.clicked.connect(self.stop)
        row = QHBoxLayout(); row.addWidget(self.btn_all); row.addWidget(self.btn_queue); row.addWidget(self.btn_stop)
        v.addLayout(row)
        self.btn_terms = QPushButton("🔎 หาชื่อตัวละคร/ศัพท์เฉพาะเข้าอภิธานศัพท์")
        self.btn_terms.clicked.connect(self.find_terms)
        v.addWidget(self.btn_terms)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("โมเดลแปล"))
        self.model_box = QComboBox()
        self.model_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model_box.setMinimumContentsLength(20)
        self.model_box.activated.connect(self.on_model_chosen)
        refresh = QPushButton("↻"); refresh.setFixedWidth(34); refresh.setToolTip("โหลดรายชื่อโมเดลจาก Ollama ใหม่")
        refresh.clicked.connect(self.refresh_models)
        mrow.addWidget(self.model_box, 1); mrow.addWidget(refresh)
        v.addLayout(mrow)
        self.refresh_models()
        hint = QLabel("ถอดเสียงใหม่จะแทนที่ซับทั้งหมดของไฟล์นี้ (รวมแถวที่แก้เอง) ส่วนขั้นแปลจะข้ามแถวที่ 🔒")
        hint.setWordWrap(True); v.addWidget(hint); v.addStretch(1)
        return w

    def _tab_queue(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.currentItemChanged.connect(self.on_file_changed)
        v.addWidget(QLabel("ลากไฟล์มาวางได้ (✓ ทำครบ · ⚠ พัง)")); v.addWidget(self.files)
        return w

    def _tab_adjust(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        g = QGroupBox("เลื่อนเวลา"); f = QFormLayout(g)
        self.shift_spin = QDoubleSpinBox(); self.shift_spin.setRange(-600, 600); self.shift_spin.setDecimals(3)
        self.shift_spin.setSingleStep(0.1); self.shift_spin.setSuffix(" วินาที"); self.shift_spin.setMinimumWidth(130)
        b1 = QPushButton("เลื่อนทุกบรรทัด"); b1.clicked.connect(lambda: self.shift(False))
        b2 = QPushButton("เลื่อนตั้งแต่บรรทัดที่เลือก"); b2.clicked.connect(lambda: self.shift(True))
        f.addRow(self.shift_spin); f.addRow(b1); f.addRow(b2)
        v.addWidget(g)
        b3 = QPushButton("ปรับเวลาให้อ่านทัน (ความยาวขั้นต่ำ + ความเร็วอ่าน)"); b3.clicked.connect(self.fix_timing_all)
        b4 = QPushButton("ตัดบรรทัดไทยอัตโนมัติทุกบรรทัด (ยกเว้นแถวที่แก้เอง 🔒)"); b4.clicked.connect(self.wrap_all)
        v.addWidget(b3); v.addWidget(b4); v.addStretch(1)
        return w

    def _build_waveform(self) -> None:
        self.wave = Waveform()
        self.wave.seekRequested.connect(self.seek_to)
        self.wave.cueClicked.connect(self.goto_row)
        self.wave.dragStarted.connect(self.on_wave_drag_started)
        self.wave.cueDragged.connect(self.on_wave_dragged)
        self.wave.dragFinished.connect(self.on_wave_drag_finished)
        zin, zout, zall = QPushButton("＋"), QPushButton("－"), QPushButton("⇔")
        zin.setToolTip("ซูมเข้า (Ctrl+ล้อเมาส์)"); zout.setToolTip("ซูมออก"); zall.setToolTip("ดูทั้งหมด")
        zin.clicked.connect(lambda: self.wave.zoom(1.5)); zout.clicked.connect(lambda: self.wave.zoom(1 / 1.5))
        zall.clicked.connect(self.wave.zoom_all)
        col = QVBoxLayout()
        for b in (zin, zout, zall):
            b.setFixedWidth(34); col.addWidget(b)
        col.addStretch(1)
        self.wave_box = QWidget()
        row = QHBoxLayout(self.wave_box); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.wave, 1); row.addLayout(col)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        def act(menu, text, slot, key=None):
            a = QAction(text, self); a.triggered.connect(lambda *_: slot())
            if key:
                a.setShortcut(QKeySequence(key))
            menu.addAction(a)
            return a

        m = mb.addMenu("ไฟล์")
        act(m, "เปิดวิดีโอ/เสียง…", self.open_files, "Ctrl+O")
        act(m, "เปิดไฟล์ซับ .srt…", self.open_srt, "Ctrl+Shift+O")
        self.act_save = act(m, "บันทึก .srt", self.save_srt, "Ctrl+S")
        m.addSeparator()
        m.addSeparator()
        self.export_acts = [
            act(m, "ส่งออก MP4 ฝังซับลงภาพ (burn-in)…", lambda: self.export_video("burn")),
            act(m, "ส่งออก MKV ใส่ซับเป็นแทร็ก…", lambda: self.export_video("mkv")),
            act(m, "ส่งออก MP4 ใส่ซับเป็นแทร็ก…", lambda: self.export_video("mp4")),
        ]
        m.addSeparator()
        act(m, "ออก", self.close, "Ctrl+Q")
        m = mb.addMenu("แก้ไข")
        self.act_undo = act(m, "ย้อนกลับ", self.undo, "Ctrl+Z")
        self.act_redo = act(m, "ทำซ้ำ", self.redo, "Ctrl+Y")
        m.addSeparator()
        act(m, "ค้นหา…", lambda: self.find_dlg.show_find(False), "Ctrl+F")
        act(m, "แทนที่…", lambda: self.find_dlg.show_find(True), "Ctrl+H")
        m.addSeparator()
        act(m, "เพิ่มบรรทัดหลังบรรทัดนี้", self.insert_cue, "Ctrl+Insert")
        act(m, "ลบบรรทัดที่เลือก", self.delete_cues, "Ctrl+Delete")
        act(m, "แยกบรรทัดที่เคอร์เซอร์", self.split_cue, "Ctrl+Alt+S")
        act(m, "รวมกับบรรทัดถัดไป", self.merge_cue, "Ctrl+M")
        m = mb.addMenu("วิดีโอ")
        act(m, "เล่น / หยุด", self.toggle_play, "Ctrl+Space")
        act(m, "ถอยหลัง 5 วินาที", lambda: self.seek_to(self.player.position() / 1000 - 5), "Ctrl+Left")
        act(m, "ไปหน้า 5 วินาที", lambda: self.seek_to(self.player.position() / 1000 + 5), "Ctrl+Right")
        m = mb.addMenu("อัตโนมัติ")
        act(m, "ทำทั้งหมด", lambda: self.run_steps(None), "F5")
        for st in STEPS:
            act(m, STEP_LABELS[st], lambda st=st: self.run_steps([st]))
        m.addSeparator()
        self.act_stop = act(m, "หยุด", self.stop, "Esc")
        m = mb.addMenu("ตั้งค่า")
        act(m, "ตั้งค่า…", self.open_settings, "Ctrl+,")

        tb = QToolBar("main"); tb.setMovable(False); self.addToolBar(tb)
        for text, slot in (("📂 เปิด", self.open_files), ("💾 บันทึก .srt", self.save_srt)):
            a = QAction(text, self); a.triggered.connect(lambda *_, s=slot: s()); tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_undo); tb.addAction(self.act_redo)
        tb.addSeparator()
        a = QAction("🔍 ค้นหา", self); a.triggered.connect(lambda *_: self.find_dlg.show_find(False)); tb.addAction(a)
        a = QAction("▶ ทำทั้งหมด", self); a.triggered.connect(lambda *_: self.run_steps(None)); tb.addAction(a)
        a = QAction("⚙ ตั้งค่า", self); a.triggered.connect(lambda *_: self.open_settings()); tb.addAction(a)

    # =============================================================== translation tools
    def refresh_models(self) -> None:
        names: list[str] = []
        try:
            names = OllamaTranslator(TranslatorConfig(url=self.s.ollama_url)).list_models()
        except Exception:
            pass  # Ollama not running: keep showing the current model
        if self.s.ollama_model not in names:
            names.insert(0, self.s.ollama_model)
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems(names)
        self.model_box.setCurrentText(self.s.ollama_model)
        self.model_box.blockSignals(False)

    def on_model_chosen(self, _i: int) -> None:
        self.s.ollama_model = self.model_box.currentText()
        self.s.save()
        self.status.setText(f"ใช้โมเดลแปล: {self.s.ollama_model}")

    def find_terms(self) -> None:
        if not self.proj or not self.proj.segments or self.worker or getattr(self, "terms_worker", None):
            self.status.setText("ต้องถอดเสียงก่อน (ขั้น 2) จึงจะหาชื่อได้")
            return
        self.terms_worker = TermsWorker(self.s, [x.text for x in self.proj.segments], self.proj.lang or "", self)
        self.terms_worker.progress.connect(self.on_terms_progress)
        self.terms_worker.done.connect(self.on_terms_found)
        self.terms_worker.failed.connect(self.on_terms_failed)
        self.terms_worker.finished.connect(self.on_terms_finished)
        self.btn_terms.setEnabled(False)
        self.status.setText("กำลังหาชื่อและศัพท์เฉพาะ…")
        self.terms_worker.start()

    def on_terms_progress(self, f: float) -> None:
        self.bar.setValue(int(f * 100))

    def on_terms_found(self, terms: list) -> None:
        if not terms:
            self.status.setText("ไม่พบชื่อหรือศัพท์เฉพาะใหม่")
            return
        dlg = GlossaryDialog(terms, self)
        if dlg.exec():
            chosen = dlg.chosen()
            self.s.glossary.update(chosen)
            self.s.save()
            self.status.setText(f"เพิ่ม {len(chosen)} คำเข้าอภิธานศัพท์ — กด 4. แปล เพื่อใช้ (ดู/แก้ได้ใน ⚙ ตั้งค่า)")

    def on_terms_failed(self, err: str) -> None:
        QMessageBox.warning(self, "หาชื่อไม่ได้", err)

    def on_terms_finished(self) -> None:
        self.terms_worker.deleteLater()
        self.terms_worker = None
        self.btn_terms.setEnabled(True)
        self.bar.setValue(0)

    def on_table_menu(self, pos) -> None:
        if not self.proj or self.worker:
            return
        m = QMenu(self)
        m.addAction("🔄 แปลใหม่ที่เลือก", self.retranslate_selected)
        m.addAction("เพิ่มบรรทัดหลังบรรทัดนี้", self.insert_cue)
        m.addAction("ลบที่เลือก", self.delete_cues)
        m.addAction("รวมกับบรรทัดถัดไป", self.merge_cue)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def retranslate_selected(self) -> None:
        if not self.proj or self.worker or getattr(self, "retr", None):
            return
        rows = sorted({ix.row() for ix in self.table.selectionModel().selectedRows()}) or [self.row()]
        rows = [r for r in rows if r >= 0]
        if not rows:
            return
        self.save_project()
        self.retr = RetranslateWorker(self.s, [x.text for x in self.proj.segments], [x.th for x in self.proj.segments],
                                      rows, self.proj.lang or "", self)
        self.retr.done.connect(self.on_retranslated)
        self.retr.failed.connect(self.on_retranslate_failed)
        self.retr.finished.connect(self.on_retranslate_finished)
        self.b_retr.setEnabled(False)
        self.status.setText(f"กำลังแปลใหม่ {len(rows)} บรรทัด…")
        self.retr.start()

    def on_retranslated(self, result: dict) -> None:
        if not result or not self.editor:
            self.status.setText("โมเดลไม่ได้ตอบกลับคำแปล")
            return
        self.editor.push()
        for i, t in result.items():
            if 0 <= i < len(self.proj.segments):
                self.proj.segments[i].th = thai_lines.wrap(t.replace("\n", " "), self.s.max_chars_per_line, self.s.max_lines)
        self.refresh_table()
        self.mark_dirty()
        self.status.setText(f"แปลใหม่แล้ว {len(result)} บรรทัด (ย้อนกลับได้ด้วย Ctrl+Z)")

    def on_retranslate_failed(self, err: str) -> None:
        QMessageBox.warning(self, "แปลใหม่ไม่ได้", err)

    def on_retranslate_finished(self) -> None:
        self.retr.deleteLater()
        self.retr = None
        self.b_retr.setEnabled(True)

    # =============================================================== helpers
    def row(self) -> int:
        return self.table.currentRow()

    def seg(self, i: int | None = None):
        i = self.row() if i is None else i
        return self.proj.segments[i] if self.proj and 0 <= i < len(self.proj.segments) else None

    def _update_actions(self) -> None:
        self.act_undo.setEnabled(bool(self.editor and self.editor.can_undo()) and not self.worker)
        self.act_redo.setEnabled(bool(self.editor and self.editor.can_redo()) and not self.worker)

    def _set_busy(self, busy: bool) -> None:
        for b in (*self.step_btns, self.btn_all, self.btn_queue):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.edit_panel.setEnabled(not busy)
        self.wave.interactive = not busy
        self.table.setEnabled(not busy)

    def mark_dirty(self) -> None:
        self.save_timer.start()
        self._update_actions()

    def save_project(self) -> None:
        self.save_timer.stop()
        if self.proj:
            self.proj.save(self.s.work_dir)

    # =============================================================== files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths: list[str] = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                paths += [str(x) for x in sorted(p.iterdir()) if x.suffix.lower() in MEDIA_EXT]
            elif p.suffix.lower() in MEDIA_EXT:
                paths.append(str(p))
            elif p.suffix.lower() == ".srt":
                self.open_srt(str(p))
        self.add_files(paths)

    def open_files(self) -> None:
        exts = " ".join(f"*{x}" for x in sorted(MEDIA_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "เลือกวิดีโอ/เสียง", "", f"Media ({exts});;All (*)")
        self.add_files(files)

    def open_srt(self, path: str | None = None, field: str | None = None) -> None:
        """Load a .srt into the current video's cue list (undoable)."""
        if not self.proj or not self.editor or self.worker:
            QMessageBox.information(self, "ยังไม่มีวิดีโอ", "เปิดวิดีโอก่อน แล้วค่อยเปิดไฟล์ซับ")
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "เปิดไฟล์ซับ", str(Path(self.current).parent), "Subtitles (*.srt);;All (*)")
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
            for enc in ("utf-8-sig", "utf-16", "cp874"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            cues = srt.parse_srt(text)
        except Exception as e:
            QMessageBox.warning(self, "เปิดไฟล์ซับไม่ได้", str(e))
            return
        if not cues:
            QMessageBox.warning(self, "เปิดไฟล์ซับไม่ได้", "ไม่พบบรรทัดซับในไฟล์นี้")
            return
        if field is None:
            box = QMessageBox(self)
            box.setWindowTitle("ใส่ซับนี้เป็น?")
            box.setText(f"{Path(path).name}: {len(cues)} บรรทัด\nจะใช้ไฟล์นี้เป็นอะไร? (แทนที่รายการซับปัจจุบันทั้งหมด ย้อนกลับได้ด้วย Ctrl+Z)")
            b_th = box.addButton("คำแปล", QMessageBox.AcceptRole)
            b_src = box.addButton("ต้นฉบับ", QMessageBox.AcceptRole)
            box.addButton("ยกเลิก", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_th:
                field = "th"
            elif box.clickedButton() is b_src:
                field = "text"
            else:
                return
        self.editor.import_cues(cues, field, keep_other=bool(self.proj.segments))
        self.proj.done["translate"] = True if field == "th" else self.proj.done.get("translate", False)
        self.refresh_table(select=0)
        self.mark_dirty()
        self.status.setText(f"เปิด {Path(path).name}: {len(cues)} บรรทัด")

    def add_files(self, paths: list[str]) -> None:
        have = {self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())}
        first = None
        for p in paths:
            if p in have:
                continue
            it = QListWidgetItem(); it.setData(Qt.UserRole, p)
            self.files.addItem(it)
            self._refresh_item(it)
            first = first or it
        if first is not None and self.files.currentItem() is None:
            self.files.setCurrentItem(first)
        elif first is not None:
            self.files.setCurrentItem(first)

    def _item_for(self, video: str):
        return next((self.files.item(i) for i in range(self.files.count()) if self.files.item(i).data(Qt.UserRole) == video), None)

    def _refresh_item(self, it: QListWidgetItem, mark: str | None = None) -> None:
        v = it.data(Qt.UserRole)
        p = Project.open(self.s.work_dir, v)
        done = all(p.done.get(s) for s in STEPS)
        it.setText(f"{mark or ('✓ ' if done else '')}{Path(v).name}")
        it.setToolTip(v)

    def on_file_changed(self, cur, _prev) -> None:
        if cur is None or self.worker:
            return
        self.save_project()
        self.load(cur.data(Qt.UserRole))

    def load(self, video: str) -> None:
        self.current = video
        self.proj = Project.open(self.s.work_dir, video)
        self.editor = Editor(self.proj.segments)
        self.setWindowTitle(f"Subtitle Maker — {Path(video).name}")
        self.player.setSource(QUrl.fromLocalFile(video))
        self._load_wave(video)
        self.wave.segs = self.proj.segments
        self.refresh_table(select=0 if self.proj.segments else -1)
        self.status.setText(f"{Path(video).name} — {len(self.proj.segments)} บรรทัด" + (f" · ภาษา {self.proj.lang}" if self.proj.lang else ""))
        self._update_actions()

    def _load_wave(self, video: str) -> None:
        wav = Project.dir_for(self.s.work_dir, video) / "audio.wav"
        try:
            if not wav.exists():
                audio.extract(video, wav)
            self.wave.load_wav(wav)
            self.wave.zoom_all() if self.wave.duration < 60 else None
        except Exception as e:
            self.wave.load_wav(None)
            self.status.setText(f"ทำคลื่นเสียงไม่ได้: {e}")

    # =============================================================== table
    def refresh_table(self, select: int | None = None) -> None:
        keep = self.row() if select is None else select
        segs = self.proj.segments if self.proj else []
        self._fill = True
        try:
            self.table.setRowCount(len(segs))
            for r in range(len(segs)):
                self._fill_row(r)
        finally:
            self._fill = False
        if segs:
            keep = max(0, min(keep, len(segs) - 1))
            self._seek_on_select = False
            self.table.setCurrentCell(keep, C_TH)
            self.table.selectRow(keep)
            self._seek_on_select = True
            self.load_edit_panel()
        else:
            self.load_edit_panel()
        self.wave.set_selected(self.row())
        self.wave.update()

    def _fill_row(self, r: int) -> None:
        s = self.proj.segments[r]

        def put(col, text, color=None):
            it = self.table.item(r, col)
            if it is None:
                it = QTableWidgetItem(); it.setFlags(it.flags() & ~Qt.ItemIsEditable); self.table.setItem(r, col, it)
            it.setText(text)
            it.setForeground(color or self.table.palette().text().color())

        too_fast = cps(s) > self.s.max_cps
        put(C_NO, f"{r + 1}{' 🔒' if s.locked else ''}")
        put(C_START, fmt_time(s.start)); put(C_END, fmt_time(s.end))
        put(C_DUR, f"{s.end - s.start:.2f}", BAD if too_fast else None)
        put(C_SRC, s.text.replace("\n", " ↵ "))
        put(C_TH, s.th.replace("\n", " ↵ "), BAD if any(len(x) > self.s.max_chars_per_line + 8 for x in s.th.split("\n")) else None)

    def on_row_changed(self, row, _c, _pr, _pc) -> None:
        if self._fill or row < 0 or not self.proj:
            return
        self.load_edit_panel()
        self.wave.set_selected(row)
        if self._seek_on_select and (s := self.seg(row)):
            self.seek_to(s.start, from_table=True)

    def goto_row(self, i: int) -> None:
        if self.proj and 0 <= i < len(self.proj.segments):
            self.table.setCurrentCell(i, C_TH)
            self.table.selectRow(i)

    # =============================================================== edit panel
    def load_edit_panel(self) -> None:
        s = self.seg()
        self._fill = True
        try:
            for w in (self.src_edit, self.th_edit, self.t_start, self.t_end, self.t_dur):
                w.setEnabled(s is not None)
            if s is None:
                self.src_edit.setPlainText(""); self.th_edit.setPlainText(""); self.info.setText("")
                self.t_start.setValue(0); self.t_end.setValue(0); self.t_dur.setValue(0)
            else:
                self.src_edit.setPlainText(s.text); self.th_edit.setPlainText(s.th)
                self.t_start.setValue(s.start); self.t_end.setValue(s.end); self.t_dur.setValue(s.end - s.start)
                self._update_info(s)
        finally:
            self._fill = False

    def _update_info(self, s) -> None:
        lines = s.th.split("\n") if s.th else [""]
        speed = cps(s)
        warn = speed > self.s.max_cps
        self.info.setText(f"บรรทัด {len(lines)} · ตัวอักษร {'/'.join(str(len(x)) for x in lines)} · "
                          f"ความเร็วอ่าน {speed:.1f} ตัวอักษร/วินาที" + ("  ⚠ เร็วเกินไป" if warn else ""))
        self.info.setStyleSheet("color:#dc3c3c;" if warn else "")

    def on_text_edited(self, field: str) -> None:
        if self._fill or not self.editor:
            return
        i = self.row()
        if self.seg(i) is None:
            return
        w = self.th_edit if field == "th" else self.src_edit
        self.editor.set_text(i, field, w.toPlainText())
        self._fill_row(i)
        self._update_info(self.seg(i))
        self.wave.update()
        self.mark_dirty()

    def on_times_edited(self, which: str) -> None:
        if self._fill or not self.editor:
            return
        i = self.row()
        s = self.seg(i)
        if s is None:
            return
        start, end = self.t_start.value(), self.t_end.value()
        if which == "dur":
            end = start + self.t_dur.value()
        elif which == "start":
            end = max(end, start + 0.1) if end <= start else end
        self._apply_times(i, start, end)

    def _apply_times(self, i: int, start: float, end: float, push: bool = True) -> int:
        new_i = self.editor.set_times(i, start, end, push)
        self.refresh_table(select=new_i)
        self.mark_dirty()
        return new_i

    def set_time_from_player(self, which: str) -> None:
        s = self.seg()
        if s is None:
            return
        t = self.player.position() / 1000
        self._apply_times(self.row(), t if which == "start" else s.start, t if which == "end" else s.end)

    # =============================================================== editing commands
    def insert_cue(self) -> None:
        if not self.editor or self.worker:
            return
        i = self.editor.insert_after(self.row() if self.row() >= 0 else len(self.proj.segments) - 1)
        self.refresh_table(select=i)
        self.th_edit.setFocus()
        self.mark_dirty()

    def delete_cues(self) -> None:
        if not self.editor or self.worker:
            return
        rows = sorted({ix.row() for ix in self.table.selectionModel().selectedRows()}) or [self.row()]
        self.editor.delete(rows)
        self.refresh_table(select=min(rows[0], len(self.proj.segments) - 1))
        self.mark_dirty()

    def merge_cue(self) -> None:
        if self.editor and not self.worker and self.editor.merge_next(self.row()):
            self.refresh_table(select=self.row())
            self.mark_dirty()

    def split_cue(self) -> None:
        if not self.editor or self.worker or self.seg() is None:
            return
        field = "th" if self.seg().th else "text"
        w = self.th_edit if field == "th" else self.src_edit
        j = self.editor.split(self.row(), w.textCursor().position(), field)
        if j < 0:
            self.status.setText("วางเคอร์เซอร์ในช่องข้อความตรงจุดที่จะแยกก่อน")
            return
        self.refresh_table(select=j)
        self.mark_dirty()

    def auto_break(self) -> None:
        s = self.seg()
        if s is None or not s.th:
            return
        text = thai_lines.wrap(s.th.replace("\n", " "), self.s.max_chars_per_line, self.s.max_lines)
        self.th_edit.setPlainText(text)  # goes through on_text_edited: undoable and 🔒

    def shift(self, from_selected: bool) -> None:
        if self.editor and self.shift_spin.value():
            self.editor.shift(self.shift_spin.value(), self.row() if from_selected else 0)
            self.refresh_table(); self.mark_dirty()

    def fix_timing_all(self) -> None:
        if not self.editor:
            return
        self.editor.push()
        segment.fix_timing(self.proj.segments, self.s.min_duration, self.s.max_cps)
        self.refresh_table(); self.mark_dirty()

    def wrap_all(self) -> None:
        if not self.editor:
            return
        self.editor.push()
        for s in self.proj.segments:
            if s.th and not s.locked:
                s.th = thai_lines.wrap(s.th.replace("\n", " "), self.s.max_chars_per_line, self.s.max_lines)
        self.refresh_table(); self.mark_dirty()

    def undo(self) -> None:
        if self.editor and not self.worker and self.editor.undo():
            self.refresh_table(); self.mark_dirty()

    def redo(self) -> None:
        if self.editor and not self.worker and self.editor.redo():
            self.refresh_table(); self.mark_dirty()

    # ---- find / replace
    def on_find_next(self, needle: str, field: str, case: bool) -> None:
        if not self.editor:
            return
        i = self.editor.find(needle, field, self.row() + 1, case)
        self.find_dlg.info.setText("" if i >= 0 else "ไม่พบ")
        if i >= 0:
            self.goto_row(i)

    def on_replace_one(self, needle: str, repl: str, field: str, case: bool) -> None:
        if not self.editor:
            return
        i = self.row()
        if self.seg(i) is not None and self.editor.replace_in(i, needle, repl, field, case):
            self.refresh_table(select=i); self.mark_dirty()
        self.on_find_next(needle, field, case)

    def on_replace_all(self, needle: str, repl: str, field: str, case: bool) -> None:
        if not self.editor:
            return
        n = self.editor.replace_all(needle, repl, field, case)
        self.find_dlg.info.setText(f"แทนที่ {n} จุด")
        if n:
            self.refresh_table(); self.mark_dirty()

    # =============================================================== waveform drag
    def on_wave_drag_started(self, i: int) -> None:
        self.editor.push()

    def on_wave_dragged(self, i: int, start: float, end: float) -> None:
        self.editor.set_times(i, start, end, push=False)
        self._fill = True
        try:
            self._fill_row(i)
            self.t_start.setValue(start); self.t_end.setValue(end); self.t_dur.setValue(end - start)
        finally:
            self._fill = False

    def on_wave_drag_finished(self, i: int) -> None:
        s = self.seg(i)
        new_i = self.editor.sort(s)
        self.refresh_table(select=new_i)
        self.mark_dirty()

    # =============================================================== player
    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause(); self.play_btn.setText("▶")
        else:
            self.player.play(); self.play_btn.setText("⏸")

    def seek_to(self, t: float, from_table: bool = False) -> None:
        self.player.setPosition(max(0, int(t * 1000)))
        self.wave.set_playhead(max(0.0, t))

    def on_duration(self, ms: int) -> None:
        self.seek.setRange(0, ms)

    def on_media_status(self, status) -> None:
        # Qt draws no frame until playback has started once: start, then pause, so the box is not black
        if status == QMediaPlayer.LoadedMedia and self.player.playbackState() == QMediaPlayer.StoppedState:
            self.player.play()
            QTimer.singleShot(150, self._pause_after_nudge)

    def _pause_after_nudge(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            s = self.seg()
            self.player.setPosition(int((s.start if s else 0) * 1000))
            self.play_btn.setText("▶")

    def on_player_error(self, _err, msg: str) -> None:
        self.status.setText(f"เล่นวิดีโอไม่ได้: {msg}")

    def on_position(self, ms: int) -> None:
        t = ms / 1000
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        self.time_label.setText(fmt_time(t))
        self.wave.set_playhead(t)
        cue = next(((k, s) for k, s in enumerate(self.proj.segments) if s.start <= t <= s.end), None) if self.proj else None
        self.view.sub.set_text((cue[1].th or cue[1].text) if cue else "")
        if cue and self.sync_box.isChecked() and cue[0] != self.row() and self.player.playbackState() == QMediaPlayer.PlayingState:
            self._seek_on_select = False
            self.goto_row(cue[0])
            self._seek_on_select = True

    # =============================================================== running the automatic steps
    def run_steps(self, steps: list[str] | None, whole_queue: bool = False) -> None:
        if self.worker:
            return
        if whole_queue:
            videos = [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]
        else:
            videos = [self.current] if self.current else []
        if not videos:
            QMessageBox.information(self, "ยังไม่มีไฟล์", "ลากวิดีโอมาวาง หรือเลือก ไฟล์ → เปิดวิดีโอ ก่อน")
            return
        self.save_project()  # the worker rewrites project.json: flush hand edits first
        self.worker = Worker(self.s, videos, steps, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.file_failed.connect(self.on_file_failed)
        self.worker.finished.connect(self.on_finished)
        self._set_busy(True)
        self._update_actions()
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.status.setText("กำลังหยุด…")

    def on_progress(self, step: str, f: float) -> None:
        self.bar.setValue(int(f * 100))
        self.status.setText(f"{STEP_LABELS.get(step, step)}  {int(f * 100)}%")

    def on_file_started(self, video: str) -> None:
        if it := self._item_for(video):
            self._refresh_item(it, "⏳ ")

    def on_file_done(self, video: str, msg: str) -> None:
        if it := self._item_for(video):
            self._refresh_item(it)
        self.status.setText(f"{Path(video).name}: {msg}")

    def on_file_failed(self, video: str, err: str) -> None:
        if it := self._item_for(video):
            self._refresh_item(it, "⚠ "); it.setToolTip(err)
        self.status.setText(f"{Path(video).name}: ล้มเหลว — {err[:160]}")
        self.failures.append((video, err))

    def on_finished(self) -> None:
        self.worker.deleteLater()
        self.worker = None
        self.bar.setValue(0)
        self._set_busy(False)
        if self.current:  # the steps rewrote project.json
            self.load(self.current)
        fails, self.failures = self.failures, []
        if fails:
            QMessageBox.warning(self, "บางไฟล์ไม่สำเร็จ", "\n\n".join(f"{Path(v).name}\n{e[:300]}" for v, e in fails))

    # =============================================================== misc
    def save_srt(self) -> None:
        if not self.proj:
            return
        try:
            self.save_project()
            written = StepRunner(self.s).write_as_is(self.proj)
            self.status.setText("บันทึกแล้ว: " + ", ".join(str(w) for w in written))
        except Exception as e:
            QMessageBox.warning(self, "บันทึกไม่ได้", str(e))

    def export_video(self, mode: str) -> None:
        if not self.proj or not self.current or self.worker or getattr(self, "export_worker", None):
            return
        if not any(x.th or x.text for x in self.proj.segments):
            QMessageBox.information(self, "ยังไม่มีซับ", "ทำซับหรือเปิดไฟล์ .srt ก่อน")
            return
        video = Path(self.current)
        suffix = {"burn": ".mp4", "mkv": ".mkv", "mp4": ".mp4"}[mode]
        tag = ".burned" if mode == "burn" else ".soft"
        folder = Path(self.s.out_dir) if self.s.out_dir else video.parent
        default = str(folder / f"{video.stem}.{self.s.target_lang}{tag}{suffix}")
        out, _ = QFileDialog.getSaveFileName(self, "บันทึกวิดีโอ", default, f"Video (*{suffix})")
        if not out:
            return
        if Path(out).resolve() == video.resolve():
            QMessageBox.warning(self, "ชื่อซ้ำกับต้นฉบับ", "เลือกชื่อไฟล์อื่น จะไม่เขียนทับวิดีโอต้นฉบับ")
            return
        self.save_project()
        tmp = Project.dir_for(self.s.work_dir, self.current) / "export.srt"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        cues = [(x.start, x.end, x.th or x.text) for x in self.proj.segments]
        srt.write_srt(tmp, cues)
        self.export_worker = ExportWorker(mode, self.s, self.current, str(tmp), out, self)
        self.export_worker.progress.connect(self.on_export_progress)
        self.export_worker.done.connect(self.on_export_done)
        self.export_worker.failed.connect(self.on_export_failed)
        self.export_worker.finished.connect(self.on_export_finished)
        for a in self.export_acts:
            a.setEnabled(False)
        self.status.setText("กำลังส่งออกวิดีโอ…")
        self.export_worker.start()

    def on_export_progress(self, f: float) -> None:
        self.bar.setValue(int(f * 100))
        self.status.setText(f"กำลังส่งออกวิดีโอ {int(f * 100)}%")

    def on_export_done(self, path: str) -> None:
        self.status.setText(f"ส่งออกแล้ว: {path}")

    def on_export_failed(self, err: str) -> None:
        QMessageBox.warning(self, "ส่งออกไม่ได้", err)
        self.status.setText("ส่งออกไม่สำเร็จ")

    def on_export_finished(self) -> None:
        self.export_worker.deleteLater()
        self.export_worker = None
        self.bar.setValue(0)
        for a in self.export_acts:
            a.setEnabled(True)

    def open_settings(self) -> None:
        models: list[str] = []
        try:
            models = OllamaTranslator(TranslatorConfig(url=self.s.ollama_url)).list_models()
        except Exception:
            pass  # Ollama not running: the box stays editable
        dlg = SettingsDialog(self.s, models, self)
        if dlg.exec():
            self.s = dlg.apply()
            self.s.save()
            self.apply_look()
            self.refresh_table()

    def closeEvent(self, e):
        self.save_project()
        if getattr(self, "export_worker", None):
            self.export_worker.stop(); self.export_worker.wait(5000)
        if self.worker:
            self.worker.stop(); self.worker.wait(5000)
        super().closeEvent(e)
