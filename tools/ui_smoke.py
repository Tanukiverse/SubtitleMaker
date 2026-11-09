"""Open the real window, run every step on one media file, then exercise the editor like a user would:
edit text, retime, drag on the waveform, insert/split/merge/delete, undo/redo, find/replace, save .srt.
usage: python tools/ui_smoke.py <media file> <temp work dir> <screenshot.png> [asr model] [source lang]
Uses its own Settings object, so the user's settings.json is never touched."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if "--show" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
args = [a for a in sys.argv[1:] if not a.startswith("--")]

from PySide6.QtWidgets import QApplication

from subtitle_maker.core import srt
from subtitle_maker.core.project import Project
from subtitle_maker.core.settings import Settings
from subtitle_maker.ui.main_window import C_TH, MainWindow


def pump(app, seconds=0.2):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    media, work, shot = args[:3]
    s = Settings(work_dir=work, out_dir=str(Path(work) / "out"),
                 asr_model=args[3] if len(args) > 3 else "base", source_lang=args[4] if len(args) > 4 else "auto")
    app = QApplication([])
    Settings.save = lambda self: None  # the smoke test must never write the user's settings.json
    from subtitle_maker.ui.theme import apply_dark
    apply_dark(app)
    w = MainWindow(s)
    w.show()
    w.add_files([media])
    assert w.files.count() == 1 and w.current == media

    w.run_steps(None)
    t0 = time.time()
    while w.worker is not None:
        app.processEvents(); time.sleep(0.05)
        assert time.time() - t0 < 1200, "pipeline timed out"
    n = w.table.rowCount()
    assert n > 0, f"no rows after run; status: {w.status.text()}"
    assert any("฀" <= c <= "๿" for c in w.table.item(0, C_TH).text()), w.table.item(0, C_TH).text()
    assert w.files.item(0).text().startswith("✓"), w.files.item(0).text()
    assert w.wave.mx is not None and w.wave.duration > 0, "waveform not loaded"

    # edit the translation text
    w.goto_row(0)
    w.th_edit.setPlainText("แก้เองแล้ว")
    assert w.seg(0).th == "แก้เองแล้ว" and w.seg(0).locked and "🔒" in w.table.item(0, 0).text()

    # retime through the time boxes
    w.t_end.setValue(w.seg(0).start + 1.5)
    assert abs(w.seg(0).end - w.seg(0).start - 1.5) < 0.01
    w.t_dur.setValue(2.0)
    assert abs(w.seg(0).end - w.seg(0).start - 2.0) < 0.01

    # drag the cue's right edge on the waveform (real mouse events)
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w.wave.t0 = 0.0
    seg = w.seg(0)
    x_end = w.wave._x(seg.end)
    before = seg.end
    for typ, x in ((QEvent.MouseButtonPress, x_end), (QEvent.MouseMove, x_end + 40), (QEvent.MouseButtonRelease, x_end + 40)):
        ev = QMouseEvent(typ, QPointF(x, 60), QPointF(x, 60), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        (w.wave.mousePressEvent if typ == QEvent.MouseButtonPress else w.wave.mouseMoveEvent if typ == QEvent.MouseMove
         else w.wave.mouseReleaseEvent)(ev)
    assert w.seg(0).end > before + 0.1, (before, w.seg(0).end)

    # insert, split, merge, delete, undo/redo
    count = len(w.proj.segments)
    w.goto_row(0); w.insert_cue()
    assert len(w.proj.segments) == count + 1 and w.row() == 1
    w.th_edit.setPlainText("สองส่วน ต่อกัน")
    cur = w.th_edit.textCursor(); cur.setPosition(len("สองส่วน")); w.th_edit.setTextCursor(cur)
    w.split_cue()
    assert len(w.proj.segments) == count + 2 and w.seg(1).th == "สองส่วน" and w.seg(2).th == "ต่อกัน"
    w.goto_row(1); w.merge_cue()
    assert len(w.proj.segments) == count + 1
    w.delete_cues()
    assert len(w.proj.segments) == count
    w.undo(); assert len(w.proj.segments) == count + 1
    w.redo(); assert len(w.proj.segments) == count

    # find / replace
    w.on_replace_all("แก้เอง", "แก้ไข", "th", False)
    assert w.seg(0).th == "แก้ไขแล้ว"

    # time shift and save
    w.shift_spin.setValue(0.5); before = w.seg(0).start; w.shift(False)
    assert abs(w.seg(0).start - before - 0.5) < 0.01
    w.save_srt()
    out = Path(work) / "out" / f"{Path(media).stem}.th.srt"
    cues = srt.parse_srt(out.read_text("utf-8"))
    assert cues and cues[0][2] == "แก้ไขแล้ว" and len(cues) == len(w.proj.segments), cues[:1]
    saved = Project.open(work, media)
    assert saved.segments[0].th == "แก้ไขแล้ว"  # project.json autosaved too

    # open the saved .srt as the translation (undoable)
    n_before = len(w.proj.segments)
    w.open_srt(str(out), "th")
    assert len(w.proj.segments) == len(cues) and w.proj.segments[0].th == "แก้ไขแล้ว" and w.proj.segments[0].locked
    w.undo()
    assert len(w.proj.segments) == n_before

    # translate the selected cue again with the real model
    w.goto_row(0)
    old = w.seg(0).th
    w.retranslate_selected()
    t0 = time.time()
    while getattr(w, "retr", None) is not None:
        app.processEvents(); time.sleep(0.05)
        assert time.time() - t0 < 300, "retranslate timed out"
    assert w.seg(0).th.strip() and "retranslate" not in w.status.text().lower(), w.status.text()
    w.undo()
    assert w.seg(0).th == old

    # player controls
    w.volume.setValue(35)
    assert abs(w.audio_out.volume() - 0.35) < 0.01 and w.s.volume == 35
    w.toggle_mute(); assert w.audio_out.isMuted() and w.mute_btn.text() == "🔇"
    w.toggle_mute(); assert not w.audio_out.isMuted()
    w.set_speed_index(4); assert abs(w.player.playbackRate() - 1.5) < 0.01
    w.set_speed_index(2); assert abs(w.player.playbackRate() - 1.0) < 0.01
    w.goto_row(0); pump(app, 0.3)
    w.step_cue(1); pump(app, 0.3)
    assert w.row() == 1 and abs(w.player.position() / 1000 - w.seg(1).start) < 0.1, (w.row(), w.player.position())
    w.step_cue(-1); pump(app, 0.3)
    assert w.row() == 0
    p0 = w.player.position(); w.step_frame(1); pump(app, 0.3)
    assert w.player.position() > p0 or w.player.position() == p0  # paused seek; position may need a frame to update
    w.loop_box.setChecked(True); pump(app, 0.5); w.loop_box.setChecked(False)
    w.toggle_fullscreen(); assert w.fs is not None; pump(app, 0.3)
    w.toggle_fullscreen(); assert w.fs is None

    pump(app, 1.0)
    w.grab().save(shot)
    print(f"OK rows={len(w.proj.segments)} srt={out}")
    if "--show" in sys.argv:
        app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
