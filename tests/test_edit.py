from subtitle_maker.core.edit import Editor, cps
from subtitle_maker.core.project import Seg


def mk():
    return [Seg(0, 2, "a", "ก"), Seg(3, 5, "b", "ข"), Seg(6, 8, "c", "ค")]


def test_undo_redo_text_and_lock():
    segs = mk(); e = Editor(segs)
    e.set_text(1, "th", "แก้")
    assert segs[1].th == "แก้" and segs[1].locked
    e.undo()
    assert segs[1].th == "ข" and not segs[1].locked
    e.redo()
    assert segs[1].th == "แก้"
    assert not e.can_redo()


def test_typing_in_one_box_is_one_undo_step():
    segs = mk(); e = Editor(segs)
    for t in ["x", "xy", "xyz"]:
        e.set_text(0, "th", t)
    e.undo()
    assert segs[0].th == "ก" and not e.can_undo()


def test_set_times_sorts_and_clamps():
    segs = mk(); e = Editor(segs)
    i = e.set_times(0, 5.5, 5.55)  # moved past cue 1; end clamped to start + MIN_DUR
    assert i == 1 and segs[1].text == "a" and segs[1].end >= 5.6
    assert e.set_times(0, -3, 1) == 0 and segs[0].start == 0


def test_insert_delete_merge_split():
    segs = mk(); e = Editor(segs)
    i = e.insert_after(0)
    assert i == 1 and 2 < segs[1].start and segs[1].end <= segs[2].start
    e.delete([1])
    assert [s.text for s in segs] == ["a", "b", "c"]
    assert e.merge_next(0) and segs[0].th == "ก ข" and segs[0].end == 5 and len(segs) == 2
    assert not e.merge_next(1)
    j = e.split(0, 1, "th")
    assert j == 1 and segs[0].th == "ก" and segs[1].th == "ข" and segs[0].end < segs[1].start <= 5
    assert e.split(0, 0, "th") == -1
    e.undo(); e.undo(); e.undo()  # split, merge, delete(insert)... back to 4 pieces? ensure no crash and consistent
    assert all(s.end > s.start for s in segs)


def test_shift_and_find_replace():
    segs = mk(); e = Editor(segs)
    e.shift(1.5, from_index=1)
    assert segs[0].start == 0 and segs[1].start == 4.5
    e.shift(-100)
    assert segs[0].start == 0 and segs[0].end > 0
    segs[2].th = "สวัสดี โลก"
    assert e.find("โลก", "th") == 2 and e.find("zzz", "th") == -1
    assert e.find("A", "text") == 0 and e.find("A", "text", case=True) == -1
    assert e.replace_all("โลก", "ดาว", "th") == 1 and segs[2].th == "สวัสดี ดาว" and segs[2].locked


def test_cps():
    assert cps(Seg(0, 2, "abcd", "")) == 2.0
    assert cps(Seg(0, 1, "x", "ab\ncd")) == 4.0


def test_import_cues_as_translation_keeps_matching_source():
    segs = mk(); e = Editor(segs)
    e.import_cues([(0.1, 1.9, "หนึ่ง"), (3.2, 4.8, "สอง"), (20, 21, "ไกลเกิน")], "th", keep_other=True)
    assert [s.th for s in segs] == ["หนึ่ง", "สอง", "ไกลเกิน"]
    assert [s.text for s in segs] == ["a", "b", ""]  # matched by start time within 1 s
    assert all(s.locked for s in segs)
    e.undo()
    assert [s.th for s in segs] == ["ก", "ข", "ค"]
