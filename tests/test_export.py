import shutil
import subprocess

import pytest

from subtitle_maker.core import export, srt
from subtitle_maker.core.settings import Settings

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_video(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x224466:s=320x180:r=15:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def streams(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True, check=True)
    return [ln.split(",") for ln in r.stdout.split()]


def test_ass_color_and_style():
    assert export.ass_color("#ff8000") == "&H000080FF&"
    st = export.force_style(Settings(sub_size_pct=6, sub_bottom_pct=5, burn_font="Tahoma"))
    assert "FontName=Tahoma" in st and "FontSize=17.3" in st and "MarginV=14.4" in st


def test_burn_in_and_soft_sub(tmp_path):
    v = tmp_path / "คลิป ทดสอบ [1].mp4"  # spaces, Thai and brackets in the name
    make_video(v)
    sub = srt.write_srt(tmp_path / "s.srt", [(0.2, 2.5, "สวัสดีครับ\nบรรทัดสอง")])
    seen = []
    out = export.burn_in(v, sub, tmp_path / "out" / "burned.mp4", Settings(), progress=seen.append)
    kinds = [k for _, k in [(s[0], s[1]) if len(s) > 1 else (s[0], "") for s in streams(out)]]
    assert out.exists() and out.stat().st_size > 1000 and seen and seen[-1] > 0.9
    # the picture really changed: the burned file differs from a plain re-encode of the same source
    plain = tmp_path / "plain.mp4"
    export.burn_in(v, srt.write_srt(tmp_path / "late.srt", [(50.0, 51.0, "x")]), plain, Settings())
    assert out.read_bytes() != plain.read_bytes()

    mkv = export.soft_sub(v, sub, tmp_path / "soft.mkv")
    assert ["subtitle"] in [[k] for _, k in streams(mkv)] or any("subrip" in s or "ass" in s for s in sum(streams(mkv), []))
    mp4 = export.soft_sub(v, sub, tmp_path / "soft.mp4")
    assert any("mov_text" in s for s in sum(streams(mp4), []))
    with pytest.raises(export.ExportError):
        export.soft_sub(v, sub, tmp_path / "x.avi")
