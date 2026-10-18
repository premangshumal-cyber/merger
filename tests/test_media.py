import shutil
import subprocess

import pytest

from playlistfusion.media import (
    can_stream_copy, chapters_text, ffmetadata, merge_files, probe, verify,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="FFmpeg unavailable")


def sample(path, *, size="320x240", fps=24, seconds=1, audio=True):
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc2=size={size}:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-threads", "1",
            "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def test_chapter_math(tmp_path):
    assert chapters_text(["Start", "End"], [62.5, 40]).splitlines()[1].startswith("00:01:02")
    file = tmp_path / "chapters.ffmeta"
    ffmetadata(["a;=b", "c"], [1.5, 2], file)
    assert "START=1500" in file.read_text()


def test_stream_copy_and_chapters(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    sample(a)
    sample(b)
    assert can_stream_copy([probe(a), probe(b)])
    dest = tmp_path / "joined.mp4"
    result, copy = merge_files([a, b], ["A", "B"], dest, True)
    assert copy
    assert result.duration > 1.8
    assert verify(dest, result.duration, expected_chapters=2)
    assert dest.with_suffix(".chapters.txt").exists()


def test_normalizes_mismatched_resolutions_and_missing_audio(tmp_path):
    a, b = tmp_path / "one.mp4", tmp_path / "two.mp4"
    sample(a, size="320x240", fps=24, audio=True)
    sample(b, size="256x144", fps=30, audio=False)
    assert not can_stream_copy([probe(a), probe(b)])
    dest = tmp_path / "normalized.mp4"
    result, copied = merge_files([a, b], ["First", "Second"], dest)
    assert copied is False
    assert result.audio is not None
    assert result.width == 320
    assert result.height == 240
    assert result.duration > 1.8


def test_cancel_avoids_output(tmp_path):
    a = tmp_path / "a.mp4"
    sample(a)
    dest = tmp_path / "cancelled.mp4"
    with pytest.raises(Exception):
        merge_files([a], ["A"], dest, cancelled=lambda: True)
    assert not dest.exists()
