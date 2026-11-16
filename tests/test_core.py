from subtitle_maker.core import segment, srt, thai_lines
from subtitle_maker.core.project import Project, Seg
from subtitle_maker.core.translate import (OllamaTranslator, TranslatorConfig, build_messages, in_target_script,
                                           parse_response, pick_one)


def test_srt_roundtrip():
    cues = [(0.0, 1.5, "สวัสดี"), (3723.456, 3725.0, "สอง\nบรรทัด")]
    text = srt.to_srt(cues)
    assert "00:00:00,000 --> 00:00:01,500" in text
    assert "01:02:03,456 --> 01:02:05,000" in text
    back = srt.parse_srt(text)
    assert [c[2] for c in back] == ["สวัสดี", "สอง\nบรรทัด"]
    assert abs(back[1][0] - 3723.456) < 0.001


def test_srt_drops_empty_and_renumbers():
    text = srt.to_srt([(0, 1, "a"), (1, 2, "  "), (2, 3, "b")])
    assert text.split("\n")[0] == "1" and "\n2\n" in text and "\n3\n" not in text


def test_wrap_never_splits_a_thai_word_and_keeps_all_text():
    text = "วันนี้อากาศดีมากเลยไปเดินเล่นที่สวนสาธารณะกันเถอะนะ"
    out = thai_lines.wrap(text, max_chars=20, max_lines=2)
    assert out.replace("\n", "") == text
    assert out.count("\n") == 1
    first = out.split("\n")[0]
    assert thai_lines.tokenize(text)[0] in first  # first word intact


def test_wrap_short_text_unchanged_and_mixed_spacing():
    assert thai_lines.wrap("ไปกันเถอะ", 40) == "ไปกันเถอะ"
    out = thai_lines.wrap("ฉันใช้ iPhone 15 Pro Max ทุกวันเลยนะ ดีมากจริงๆ เลยล่ะ ไม่เคยมีปัญหาเลย", 25)
    assert "iPhone 15 Pro Max" in out.replace("\n", " ")


def test_tokenize_keeps_punctuation_with_word():
    toks = thai_lines.tokenize("เหรอ...")
    assert "".join(toks) == "เหรอ..."
    assert not any(t.startswith(".") for t in toks)


def test_drop_junk_and_hallucination_loop():
    segs = [Seg(0, 1, "ご視聴ありがとうございました"), Seg(1, 2, "こんにちは")] + [Seg(i, i + 1, "ああ") for i in range(2, 7)]
    out = segment.drop_junk(segs)
    assert [s.text for s in out] == ["こんにちは", "ああ", "ああ"]


def test_merge_short_and_timing():
    segs = [Seg(0, 0.5, "えっ"), Seg(0.6, 1.2, "うそ"), Seg(5, 6, "これは長い文章です、本当に。")]
    out = segment.merge_short(segs)
    assert len(out) == 2 and out[0].text == "えっうそ" and out[0].end == 1.2
    out[0].th = "ห๊ะ"
    segment.fix_timing(out, min_duration=1.0)
    assert out[0].end - out[0].start >= 1.0 and out[0].end < out[1].start


def test_locked_cues_are_not_merged():
    segs = [Seg(0, 0.5, "えっ", locked=True), Seg(0.6, 1.2, "うそ")]
    assert len(segment.merge_short(segs)) == 2


def test_parse_response_variants():
    assert parse_response('{"translations":[{"id":1,"text":"a"},{"id":2,"text":"ข"}]}') == {1: "a", 2: "ข"}
    assert parse_response('```json\n{"1":"x","2":"y"}\n```') == {1: "x", 2: "y"}
    assert pick_one("ไปกันเถอะค่ะ/ครับ") == "ไปกันเถอะค่ะ"
    assert in_target_script("สวัสดี", "th") and not in_target_script("こんにちは", "th")


def test_build_messages_has_glossary_context_and_ids():
    cfg = TranslatorConfig(glossary={"先輩": "รุ่นพี่"}, notes="school romance")
    msgs = build_messages([(3, "おはよう")], [("前", "ก่อน")], "ja", "th", cfg)
    assert "先輩 => รุ่นพี่" in msgs[0]["content"] and "school romance" in msgs[0]["content"]
    assert '"id": 3' in msgs[1]["content"] and "previous_lines_for_context_only" in msgs[1]["content"]


class FakeOllama(OllamaTranslator):
    """Answers every cue as 'T<id>' without a network call; counts requests."""
    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls = 0

    def _ask(self, messages, fmt=None):
        import json
        self.calls += 1
        user = messages[-1]["content"]
        cues = json.loads(user[:user.index("\n\nAnswer in")])["cues"]
        return json.dumps({"translations": [{"id": c["id"], "text": f"ก{c['id']}"} for c in cues]}, ensure_ascii=False)


def test_translate_chunks_and_keeps_locked():
    tr = FakeOllama(TranslatorConfig(chunk=3))
    texts = [f"line{i}" for i in range(7)]
    out = tr.translate(texts, "en", "th", keep={2: "แก้เอง"})
    assert out[2] == "แก้เอง" and out[0] == "ก0" and out[6] == "ก6"
    assert tr.calls == 2  # 6 cues to translate in chunks of 3


def test_bad_reply_is_split_and_skipped_not_fatal():
    import json

    class Bad(FakeOllama):
        def _ask(self, messages, fmt=None):
            user = messages[-1]["content"]
            ids = [c["id"] for c in json.loads(user[:user.index("\n\nAnswer in")])["cues"]]
            if 3 in ids:  # cue 3 always makes the model ramble
                return "ฮั่ ฮั่ ฮั่ ฮั่"
            return super()._ask(messages, fmt)

    tr = Bad(TranslatorConfig(chunk=6))
    out = tr.translate([f"line{i}" for i in range(6)], "en", "th")
    assert out[0] == "ก0" and out[5] == "ก5" and out[3] == "" and tr.failed == [3]


def test_project_roundtrip(tmp_path):
    p = Project(video=str(tmp_path / "ตอนที่ 1.mp4"), lang="ja", segments=[Seg(0, 1, "a", "ก", True)])
    p.save(tmp_path)
    q = Project.open(tmp_path, p.video)
    assert q.lang == "ja" and q.segments[0].locked and q.segments[0].th == "ก"


class W:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


def test_chunk_words_trims_trailing_silence_and_splits_on_pause():
    from subtitle_maker.core.asr import chunk_words
    words = [W("ラブ", 122.7, 123.2), W("パワー", 123.2, 123.9), W("ってやつ", 123.9, 124.6),
             W("どうしたの", 130.0, 131.0)]  # whisper's segment would have run to 240 s
    cues = chunk_words(words)
    assert [c.text for c in cues] == ["ラブパワーってやつ", "どうしたの"]
    assert cues[0].start == 122.7 and cues[0].end < 125.0


def test_chunk_words_splits_after_sentence_end_and_long_run():
    from subtitle_maker.core.asr import chunk_words
    ws = [W("今日はいい天気です。", 0, 2), W("散歩に行きましょう", 2.1, 4)]
    assert len(chunk_words(ws)) == 2
    ws = [W("あ", i, i + 1) for i in range(10)]  # one unbroken 10 s run
    assert all(c.end - c.start <= 8 for c in chunk_words(ws, max_dur=6.5))
    assert chunk_words([W("a", 0, 1), W("b", 1.1, 2)])[0].end <= 2.2 + 1e-9


def test_translate_some_only_touches_selected_and_gives_context():
    seen = []

    class Fake(FakeOllama):
        def _ask(self, messages, fmt=None):
            seen.append(messages[-1]["content"])
            return super()._ask(messages, fmt)

    tr = Fake(TranslatorConfig(chunk=10, context=2))
    texts = [f"t{i}" for i in range(6)]
    current = [f"เดิม{i}" for i in range(6)]
    out = tr.translate_some(texts, current, [3, 5], "ja", "th")
    assert set(out) == {3, 5} and out[3] == "ก3"
    assert "previous_lines_for_context_only" in seen[0] and "เดิม1" in seen[0] and "เดิม2" in seen[0]


def test_parse_terms_and_extract_dedupes_known():
    from subtitle_maker.core.translate import extract_terms, parse_terms
    reply = '```json\n{"terms":[{"source":"たかし","thai":"ทาคาชิ"},{"source":"ホワ","thai":"โฮวา"},{"source":"x","thai":"x"}]}\n```'
    assert parse_terms(reply) == [("たかし", "ทาคาชิ"), ("ホワ", "โฮวา")]
    assert parse_terms("not json") == []

    class T(OllamaTranslator):
        def _ask(self, messages, fmt=None):
            return reply

    got = extract_terms(T(TranslatorConfig()), ["a"] * 250, "ja", "th", known={"ホワ": "ฮวา"}, per_request=100)
    assert got == [("たかし", "ทาคาชิ")]  # 3 requests, same terms deduped, known term skipped


def test_find_gaps_only_reports_loud_stretches():
    import numpy as np
    from subtitle_maker.core.asr import find_gaps
    rate = 16000
    x = np.zeros(rate * 60, dtype=np.float32)
    x[0:10 * rate] = 0.03   # speech 0-10 s
    x[10 * rate:30 * rate] = 0.03   # loud, but no cue: a song
    x[30 * rate:40 * rate] = 0.0001  # silence, no cue: must be ignored
    x[40 * rate:50 * rate] = 0.03   # speech 40-50 s
    cues = [Seg(0, 10, "a"), Seg(40, 50, "b")]
    assert find_gaps(cues, x, rate) == [(10, 40)] or find_gaps(cues, x, rate)[0][0] == 10
    gaps = find_gaps(cues, x, rate)
    assert all(b - a >= 4 for a, b in gaps) and not any(a >= 30 and b <= 40 and b - a < 4 for a, b in gaps)


def test_junk_phrase_detected_before_cutting():
    assert segment.is_junk("ご視聴ありがとうございました") and not segment.is_junk("先輩 先輩")


def test_strip_id_from_freeform_lines():
    from subtitle_maker.core.translate import strip_id
    assert strip_id("262 รุ่นพี่ รุ่นพี่", 262) == "รุ่นพี่ รุ่นพี่"
    assert strip_id("266.เพราะอยู่กับทุกคน", 266) == "เพราะอยู่กับทุกคน"
    assert strip_id("[7] สวัสดี", 7) == "สวัสดี"
    assert strip_id("7 วัน ต่อมา", 8) == "7 วัน ต่อมา"  # a number that is not the cue's id stays
    assert strip_id("สวัสดี", 1) == "สวัสดี"


def test_thai_digits_become_arabic():
    from subtitle_maker.core.translate import arabic_digits
    assert arabic_digits("เวลา ๑๒:๓๐ น. ราคา ๙๙๐ บาท") == "เวลา 12:30 น. ราคา 990 บาท"
    assert pick_one("อีก ๓ ครั้ง") == "อีก 3 ครั้ง"
    assert parse_response('{"translations":[{"id":1,"text":"ชั้น ๒"}]}') == {1: "ชั้น 2"}
