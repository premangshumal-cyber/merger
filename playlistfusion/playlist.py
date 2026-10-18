"""Read public playlist metadata; download only when authorized and permitted."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from .model import Entry, Project


class PlaylistError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def playlist_id(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or host not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"
    }:
        raise PlaylistError("Enter a valid youtube.com playlist URL.")
    values = parse_qs(p.query).get("list", [])
    if not values or not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", values[0]):
        raise PlaylistError("This URL has no recognizable YouTube playlist ID.")
    return values[0]


def analyze(url: str, progress=None) -> Project:
    ident = playlist_id(url)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise PlaylistError("yt-dlp is not installed.") from exc
    if progress:
        progress("Analyzing playlist", None, "Retrieving playlist metadata")
    with YoutubeDL({
        "extract_flat": "in_playlist", "skip_download": True, "ignoreerrors": True,
        "quiet": True, "no_warnings": True, "socket_timeout": 20,
        "noplaylist": False, "playlistend": None,
    }) as ydl:
        try:
            data = ydl.extract_info(f"https://www.youtube.com/playlist?list={ident}", download=False)
        except Exception as exc:
            raise PlaylistError(f"Playlist could not be loaded: {exc}") from exc
    if not data or not data.get("entries"):
        raise PlaylistError("No accessible entries were returned for this playlist.")
    title = data.get("title") or "YouTube playlist"
    project = Project(title=title, source_url=url, filename=title)
    for i, item in enumerate(data["entries"]):
        uid = f"{i + 1:05d}"
        if not item:
            project.entries.append(Entry(uid=uid, index=i + 1,
                title=f"Unavailable entry {i + 1}", status="unavailable", selected=False,
                error="Private, deleted, restricted, or inaccessible entry"))
            continue
        vid = str(item.get("id") or "")
        url_part = item.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        if isinstance(url_part, str) and not url_part.startswith("http") and vid:
            url_part = f"https://www.youtube.com/watch?v={vid}"
        available = bool(vid and url_part)
        project.entries.append(Entry(
            uid=uid, index=i + 1, video_id=vid, url=str(url_part),
            title=str(item.get("title") or f"Video {i + 1}"),
            duration=float(item.get("duration") or 0),
            thumbnail=str(item.get("thumbnail") or ""),
            status="pending" if available else "unavailable",
            selected=available,
            error="" if available else "No accessible video URL was returned",
        ))
    return project


def download(entry: Entry, destination: Path, quality: str = "best",
             progress=None, cancelled=None, speed_limit_kib: int = 0) -> Path:
    """yt-dlp downloads into project-private storage. Never read browser cookies."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise PlaylistError("yt-dlp is not installed.") from exc
    if not entry.url or not entry.video_id:
        raise PlaylistError("This entry has no downloadable URL. Import an authorized local file.")
    destination.mkdir(parents=True, exist_ok=True)
    prefix = f"{entry.uid}-{re.sub(r'[^A-Za-z0-9_-]', '_', entry.video_id)}"
    height = int(quality) if quality.isdigit() else 0
    fmt = (f"bv*[height<={height}]+ba/b[height<={height}]/best" if height
           else "bv*+ba/b")
    def hook(state):
        if cancelled and cancelled():
            raise Cancelled("Download cancelled")
        if progress:
            done = state.get("downloaded_bytes", 0)
            total = state.get("total_bytes") or state.get("total_bytes_estimate")
            percent = (done / total * 100) if total else None
            progress("Downloading", percent, entry.title)
    opts = {
        "format": fmt, "outtmpl": str(destination / (prefix + ".%(ext)s")),
        "merge_output_format": "mp4", "continuedl": True,
        "overwrites": False, "noplaylist": True, "quiet": True,
        "no_warnings": True, "retries": 5, "fragment_retries": 5,
        "socket_timeout": 30, "progress_hooks": [hook],
        "restrictfilenames": True, "ignoreerrors": False,
    }
    # yt-dlp needs the bundled FFmpeg folder even when it is not on PATH.
    from .media import executable
    opts["ffmpeg_location"] = str(Path(executable("ffmpeg")).parent)
    if speed_limit_kib > 0:
        opts["ratelimit"] = speed_limit_kib * 1024
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([entry.url])
    except Cancelled:
        raise
    except Exception as exc:
        raise PlaylistError(f"{entry.title}: {exc}") from exc
    candidates = [p for p in destination.glob(prefix + ".*")
                  if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov") and p.stat().st_size > 0]
    if not candidates:
        raise PlaylistError(f"Download completed but no media file was found for {entry.title}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
