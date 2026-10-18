"""FFprobe inspection, conservative copy detection, normalization, chapters and verification."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Progress = Callable[[str, float | None, str], None]


class MediaError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def executable(name: str) -> str:
    bundled = Path(getattr(__import__("sys"), "_MEIPASS", Path(__file__).parent)) / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if bundled.is_file():
        return str(bundled)
    result = shutil.which(name)
    if not result:
        raise MediaError(f"{name} was not found. Install or bundle FFmpeg and FFprobe.")
    return result


def run(args: list[str], stage: str = "", duration: float = 0,
        progress: Progress | None = None, cancelled=None) -> None:
    """Safe argument array; parse FFmpeg's actual out_time_ms progress."""
    ffmpeg = executable("ffmpeg")
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-progress", "pipe:1", "-stats_period", "0.5"] + [str(a) for a in args]
    from tempfile import TemporaryFile
    with TemporaryFile(mode="w+t", encoding="utf-8") as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                              text=True, encoding="utf-8", errors="replace") as proc:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if cancelled and cancelled():
                        proc.terminate()
                        try:
                            proc.wait(timeout=4)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        raise Cancelled("Processing cancelled")
                    key, sep, value = line.strip().partition("=")
                    if progress and sep and key in ("out_time_ms", "out_time_us") and duration > 0:
                        try:
                            pct = min(99.9, float(value) / 1_000_000 / duration * 100)
                            progress(stage, pct, "")
                        except ValueError:
                            pass
                code = proc.wait()
                if code:
                    errors.seek(0)
                    raise MediaError(f"FFmpeg failed ({code}): {errors.read()[-3000:]}")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    video: dict
    audio: dict | None
    path: Path

    @property
    def width(self) -> int:
        return int(self.video.get("width") or 0)

    @property
    def height(self) -> int:
        return int(self.video.get("height") or 0)


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaError(f"File is missing or empty: {path}")
    command = [executable("ffprobe"), "-v", "error", "-show_format",
               "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise MediaError(f"Cannot read {path.name}: {result.stderr[-700:]}")
    try:
        payload = json.loads(result.stdout)
        video = next(s for s in payload.get("streams", []) if s["codec_type"] == "video")
        audio = next((s for s in payload.get("streams", []) if s["codec_type"] == "audio"), None)
        duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
        if duration <= 0 or int(video.get("width") or 0) <= 0:
            raise ValueError("Invalid video duration or dimensions")
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise MediaError(f"Unsupported or damaged media: {path.name}") from exc
    return MediaInfo(duration, video, audio, path)


def can_stream_copy(items: list[MediaInfo], loudness: bool = False) -> bool:
    """Conservative by design: avoid mismatched audio, time bases and codecs."""
    if not items or loudness or any(i.audio is None for i in items):
        return False
    def signature(info):
        v, a = info.video, info.audio or {}
        return (
            v.get("codec_name"), v.get("profile"), v.get("pix_fmt"),
            v.get("width"), v.get("height"), v.get("r_frame_rate"),
            v.get("avg_frame_rate"), v.get("time_base"), v.get("sample_aspect_ratio"),
            a.get("codec_name"), a.get("sample_rate"), a.get("channels"),
            a.get("channel_layout"), a.get("time_base"),
        )
    return (all(i.video.get("codec_name") == "h264" and
                i.audio and i.audio.get("codec_name") == "aac" for i in items)
            and len({signature(i) for i in items}) == 1)


def _even(value: int) -> int:
    return max(2, value + value % 2)


def normalize(info: MediaInfo, output: Path, width: int, height: int,
              progress: Progress | None = None, cancelled=None,
              loudness: bool = False) -> None:
    """Standardize H.264/AAC with aspect-preserving padding and a common CFR."""
    filters = (f"fps=30,scale={width}:{height}:force_original_aspect_ratio=decrease:"
               f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
               "setsar=1,format=yuv420p")
    args = ["-i", str(info.path)]
    if info.audio is None:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-map", "0:v:0", "-map", "0:a:0" if info.audio else "1:a:0",
             "-vf", filters, "-r", "30", "-c:v", "libx264", "-preset", "medium",
             "-crf", "19", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-ac", "2", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" if loudness and info.audio else "anull",
             "-t", f"{info.duration:.6f}", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(output)]
    run(args, "Normalizing", info.duration, progress, cancelled)
    probe(output)


def _concat_file(paths: list[Path], destination: Path) -> None:
    # FFmpeg concat demuxer uses single-quoted paths with backslash escaping.
    def escape(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
    lines = ["ffconcat version 1.0"] + [f"file '{escape(p)}'" for p in paths]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ffmetadata(titles: list[str], durations: list[float], path: Path) -> None:
    pos = 0
    lines = [";FFMETADATA1"]
    for title, seconds in zip(titles, durations):
        length = max(1, round(seconds * 1000))
        safe = re.sub(r"[\r\n]+", " ", title).replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={pos}",
                  f"END={pos + length}", f"title={safe}"]
        pos += length
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def chapters_text(titles: list[str], durations: list[float]) -> str:
    current = 0.0
    lines = []
    for title, seconds in zip(titles, durations):
        minutes, sec = divmod(int(current), 60)
        hours, minute = divmod(minutes, 60)
        lines.append(f"{hours:02}:{minute:02}:{sec:02} — {title}")
        current += seconds
    return "\n".join(lines) + "\n"


def verify(path: Path, expected_duration: float, expect_audio: bool = True,
           expected_chapters: int = 0) -> MediaInfo:
    data = probe(path)
    tolerance = max(1.0, expected_duration * 0.005)
    if abs(data.duration - expected_duration) > tolerance:
        raise MediaError(f"Duration mismatch: expected {expected_duration:.2f}s, got {data.duration:.2f}s")
    if expect_audio and data.audio is None:
        raise MediaError("Output has no audio stream.")
    if expected_chapters:
        p = subprocess.run([executable("ffprobe"), "-v", "error", "-show_chapters",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=90)
        if p.returncode or len(json.loads(p.stdout).get("chapters", [])) != expected_chapters:
            raise MediaError("Output chapters are incomplete.")
    return data


def merge_files(paths: list[Path], titles: list[str], output: Path,
                make_chapters: bool = True, loudness: bool = False,
                progress: Progress | None = None, cancelled=None) -> tuple[MediaInfo, bool]:
    if not paths:
        raise MediaError("No input videos selected.")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / (".playlistfusion-" + os.urandom(6).hex())
    work.mkdir()
    successful = False
    try:
        items = [probe(p) for p in paths]
        copy = can_stream_copy(items, loudness)
        if copy:
            pieces = paths
        else:
            width = _even(max(i.width for i in items))
            height = _even(max(i.height for i in items))
            # Preserve mixed source aspect ratios by padding, never stretching.
            pieces = []
            for i, item in enumerate(items):
                target = work / f"segment-{i:05d}.mp4"
                if progress:
                    progress("Normalizing", None, f"{i + 1}/{len(items)}: {item.path.name}")
                normalize(item, target, width, height, progress, cancelled, loudness)
                pieces.append(target)
        processed = [probe(p) for p in pieces]
        expected = sum(i.duration for i in processed)
        concat = work / "inputs.ffconcat"
        _concat_file(pieces, concat)
        merged = work / ("merged." + output.suffix.lstrip("."))
        if progress:
            progress("Merging", 0, "Joining selected videos in order")
        run(["-f", "concat", "-safe", "0", "-i", str(concat), "-map", "0:v:0",
             "-map", "0:a:0", "-c", "copy", "-avoid_negative_ts", "make_zero",
             str(merged)], "Merging", expected, progress, cancelled)
        final = merged
        if make_chapters:
            meta = work / "chapters.ffmeta"
            ffmetadata(titles, [i.duration for i in processed], meta)
            final = work / ("chaptered." + output.suffix.lstrip("."))
            run(["-i", str(merged), "-i", str(meta), "-map", "0",
                 "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", str(final)],
                "Writing chapters", expected, progress, cancelled)
        if progress:
            progress("Verifying", None, "Checking output streams and duration")
        data = verify(final, expected, expected_chapters=len(pieces) if make_chapters else 0)
        if make_chapters:
            output.with_suffix(".chapters.txt").write_text(
                chapters_text(titles, [i.duration for i in processed]), encoding="utf-8")
        os.replace(final, output)
        successful = True
        return data, copy
    finally:
        shutil.rmtree(work, ignore_errors=True)
