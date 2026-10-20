"""Standalone, authorized single-video retrieval and verified local export.

YouTube metadata lookup never downloads media; downloading needs user approval
in the UI. No cookies, credentials, DRM or access-control bypass.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .model import Entry, Project, ProjectStore, safe_filename
from .media import executable, probe, normalize, run, MediaError

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
WATCH_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
QUALITY_CHOICES = frozenset({"best", "2160", "1440", "1080", "720", "480", "360"})


class SingleVideoError(RuntimeError):
    """Video link, retrieval, or export failure."""


def video_id(url: str) -> str:
    """Strictly accept individual-video YouTube URLs, never arbitrary URLs."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or parsed.port:
        raise SingleVideoError("Enter a standard YouTube video URL.")
    host = (parsed.hostname or "").lower()
    if host in SHORT_HOSTS:
        parts = parsed.path.strip("/").split("/")
        value = parts[0] if len(parts) == 1 else ""
    elif host in WATCH_HOSTS:
        path = parsed.path.rstrip("/")
        if path == "/watch":
            value = parse_qs(parsed.query).get("v", [""])[0]
        elif path.startswith(("/shorts/", "/live/", "/embed/")):
            parts = path.split("/")
            value = parts[2] if len(parts) == 3 else ""
        else:
            value = ""
    else:
        value = ""
    if not VIDEO_ID.fullmatch(value):
        raise SingleVideoError(
            "This is not a supported single-video link. Use a YouTube watch, Shorts, "
            "live-recording, embed or youtu.be URL with an 11-character video ID."
        )
    return value


def canonical_video_url(url: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id(url)}"


def inspect_single(url: str, progress=None) -> Project:
    """Load only publicly accessible metadata; return a persisted one-video project."""
    canonical = canonical_video_url(url)
    if progress:
        progress("Analyzing single video", None, "Retrieving video information")
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise SingleVideoError("yt-dlp is unavailable.") from exc
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "socket_timeout": 25, "retries": 2,
        "ignoreerrors": False,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical, download=False)
    except Exception as exc:
        raise SingleVideoError(f"Video metadata could not be retrieved: {exc}") from exc
    if not isinstance(info, dict) or info.get("_type") in ("playlist", "multi_video"):
        raise SingleVideoError("The supplied URL did not resolve to one video.")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise SingleVideoError("Ongoing and upcoming live streams are not supported.")
    ident = video_id(url)
    if info.get("id") != ident:
        raise SingleVideoError("The retrieved video does not match the requested video ID.")
    title = str(info.get("title") or f"YouTube video {ident}")
    uploader = str(info.get("uploader") or info.get("channel") or "Unknown creator")
    duration = float(info.get("duration") or 0)
    entry = Entry(
        uid="single-00001", index=1, title=title, video_id=ident,
        url=canonical, duration=duration, thumbnail=str(info.get("thumbnail") or ""),
        status="pending", selected=True,
    )
    project = Project(
        kind="single", title=title, filename=title, source_url=canonical,
        entries=[entry], stage=f"Creator: {uploader}",
    )
    return project


def _format_selector(quality: str) -> str:
    if quality not in QUALITY_CHOICES:
        raise SingleVideoError("Unsupported quality selection.")
    if quality == "best":
        return "bv*+ba/b"
    return f"bv*[height<={int(quality)}]+ba/b[height<={int(quality)}]"


def _install_exclusive(source: Path, destination: Path) -> None:
    """Cross-volume safe copy; publish atomically without overwriting another file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".playlistfusion-", suffix=destination.suffix, dir=destination.parent)
    os.close(fd)
    staging = Path(name)
    try:
        with source.open("rb") as inp, staging.open("wb") as out:
            shutil.copyfileobj(inp, out, length=2**20)
            out.flush()
            os.fsync(out.fileno())
        # Both staging and destination are in the same directory; hardlink
        # fails rather than replacing a concurrently created destination.
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def download_single(project: Project, store: ProjectStore, progress,
                    cancelled, speed_limit_kib: int = 0) -> Path:
    if project.kind != "single" or len(project.entries) != 1:
        raise SingleVideoError("A single-video project must contain exactly one entry.")
    entry = project.entries[0]
    # Revalidate persisted URLs before passing them to yt-dlp.
    if not entry.video_id or video_id(entry.url) != entry.video_id:
        raise SingleVideoError("Invalid or modified video URL.")
    output_format = project.output_format.lower()
    if output_format not in {"mp4", "mkv"}:
        raise SingleVideoError("Choose MP4 or MKV.")
    _format_selector(project.quality)
    output_dir = Path(project.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / (safe_filename(project.filename) + "." + output_format)
    if output.exists():
        raise FileExistsError(f"{output.name} already exists. Choose a different filename.")
    if cancelled():
        raise SingleVideoError("Cancelled before download started.")
    work = store.temp_dir(project) / "single"
    work.mkdir(parents=True, exist_ok=True)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise SingleVideoError("yt-dlp is unavailable.") from exc
    prefix = f"{entry.video_id}-{project.quality}-{output_format}"
    download_format = _format_selector(project.quality)

    def download_hook(payload):
        if cancelled():
            raise SingleVideoError("Download cancelled. Partial files were kept for retry.")
        if progress and payload.get("status") == "downloading":
            done = payload.get("downloaded_bytes") or 0
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
            fraction = min(100.0, (done / total) * 100) if total else None
            speed = payload.get("speed")
            speed_str = f" · {speed / 1024**2:.2f} MiB/s" if isinstance(speed, (float, int)) else ""
            # Percent is the current transfer's progress, not a fabricated job total.
            progress("Downloading", fraction, f"{entry.title}: {done / 1024**2:.1f} MiB{speed_str}")
        elif progress and payload.get("status") == "finished":
            progress("Combining streams", None, "Finalizing downloaded media")

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "ignoreerrors": False, "socket_timeout": 30,
        "retries": 5, "fragment_retries": 5,
        "continuedl": True, "overwrites": False, "nopart": False,
        "outtmpl": str(work / (prefix + ".%(ext)s")),
        "merge_output_format": output_format,
        "format": download_format, "progress_hooks": [download_hook],
        "ffmpeg_location": str(Path(executable("ffmpeg")).parent),
    }
    if speed_limit_kib > 0:
        opts["ratelimit"] = int(speed_limit_kib) * 1024
    project.status = "processing"
    project.stage = "Downloading"
    project.error = ""
    store.save(project)
    try:
        progress("Downloading", None, f"Retrieving {entry.title}")
        with YoutubeDL(opts) as ydl:
            ydl.download([entry.url])
        if cancelled():
            raise SingleVideoError("Download cancelled before verification.")
        matches = [p for p in work.glob(prefix + ".*")
                   if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
                   and p.stat().st_size > 0]
        if not matches:
            raise SingleVideoError("Retrieval did not produce a complete video file.")
        # Prefer requested container, otherwise inspect and remux/transcode.
        matches.sort(key=lambda p: (p.suffix.lower() != "." + output_format, -p.stat().st_size))
        source = matches[0]
        progress("Verifying video", None, "Inspecting video and audio streams")
        metadata = probe(source)
        publish_source = source
        if output_format == "mp4":
            if metadata.video.get("codec_name") != "h264" or (
                metadata.audio and metadata.audio.get("codec_name") != "aac"
            ) or not metadata.audio:
                progress("Compatibility encoding", None, "Converting to playable H.264/AAC MP4")
                compatible = work / (prefix + "-compatible.mp4")
                normalize(metadata, compatible,
                          max(2, metadata.width + metadata.width % 2),
                          max(2, metadata.height + metadata.height % 2),
                          progress, cancelled)
                publish_source = compatible
            elif source.suffix.lower() != ".mp4":
                progress("Remuxing", None, "Changing container without video re-encoding")
                remuxed = work / (prefix + "-remux.mp4")
                run(["-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
                     "-c", "copy", str(remuxed)], "Remuxing", metadata.duration, progress, cancelled)
                publish_source = remuxed
        elif source.suffix.lower() != ".mkv":
            progress("Remuxing", None, "Changing container without video re-encoding")
            remuxed = work / (prefix + "-remux.mkv")
            maps = ["-map", "0:v:0"] + (["-map", "0:a:0"] if metadata.audio else [])
            run(["-i", str(source), *maps, "-c", "copy", str(remuxed)],
                "Remuxing", metadata.duration, progress, cancelled)
            publish_source = remuxed
        final = probe(publish_source)
        if not final.video:
            raise MediaError("The downloaded file does not contain video.")
        if output_format == "mp4" and (final.video.get("codec_name") != "h264" or
                                       not final.audio or final.audio.get("codec_name") != "aac"):
            raise MediaError("The MP4 output did not pass H.264/AAC validation.")
        if cancelled():
            raise SingleVideoError("Download cancelled before publishing.")
        progress("Saving video", None, str(output))
        if output.exists():
            raise FileExistsError(f"Output {output.name} appeared during processing.")
        _install_exclusive(publish_source, output)
        project.status = "completed"
        project.stage = "Downloaded and verified"
        project.output_path = str(output)
        entry.status = "ready"
        entry.local_path = str(source)
        entry.duration = final.duration
        entry.width, entry.height = final.width, final.height
        store.save(project)
        progress("Complete", 100.0, f"Saved {output.name}")
        return output
    except Exception as exc:
        project.status = "interrupted" if cancelled() else "failed"
        project.stage = "Interrupted" if cancelled() else "Download failed"
        project.error = str(exc)
        store.save(project)
        raise
