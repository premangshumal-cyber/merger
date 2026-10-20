"""Offline tests for single video URL, metadata, export and recoverability."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from playlistfusion.model import ProjectStore
from playlistfusion.single_video import (
    SingleVideoError, _install_exclusive, download_single, inspect_single, video_id,
)


IDENT = "dQw4w9WgXcQ"
YOUTUBE = f"https://www.youtube.com/watch?v={IDENT}"


@pytest.mark.parametrize("url", [
    YOUTUBE,
    f"https://m.youtube.com/watch?v={IDENT}&list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq",
    f"https://music.youtube.com/watch?v={IDENT}",
    f"https://youtu.be/{IDENT}?si=123",
    f"https://www.youtube.com/shorts/{IDENT}",
    f"https://www.youtube.com/live/{IDENT}",
    f"https://youtube.com/embed/{IDENT}",
])
def test_accept_single_video_urls(url):
    assert video_id(url) == IDENT


@pytest.mark.parametrize("url", [
    f"https://fake-youtube.com/watch?v={IDENT}",
    f"https://www.youtube.com.evil.test/watch?v={IDENT}",
    "https://youtube.com/playlist?list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq",
    "file:///tmp/video.mp4",
    "https://youtu.be/not-video",
    f"https://youtube.com/watch?v={IDENT}extra",
    f"https://user:pass@youtube.com/watch?v={IDENT}",
])
def test_reject_non_single_video_urls(url):
    with pytest.raises(SingleVideoError):
        video_id(url)


class FakeDownloader:
    """Only the external network boundary is mocked; downstream FFmpeg is real."""
    media: Path | None = None
    captured: list[dict] = []

    def __init__(self, options):
        self.opts = options
        self.captured.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        assert download is False
        assert url == YOUTUBE
        return {
            "id": IDENT, "title": "Authorized tutorial", "uploader": "Test creator",
            "duration": 0.8, "is_live": False, "thumbnail": "",
        }

    def download(self, urls):
        assert urls == [YOUTUBE]
        assert self.media is not None
        target = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.media, target)
        self.opts["progress_hooks"][0]({
            "status": "downloading", "downloaded_bytes": 2048, "total_bytes": 4096,
            "speed": 1024,
        })
        self.opts["progress_hooks"][0]({"status": "finished"})


def _install_mock(monkeypatch):
    FakeDownloader.captured = []
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeDownloader))


def test_metadata_only_and_project_kind(monkeypatch):
    _install_mock(monkeypatch)
    project = inspect_single(YOUTUBE)
    assert project.kind == "single"
    assert len(project.entries) == 1
    assert project.entries[0].title == "Authorized tutorial"
    assert project.stage == "Creator: Test creator"
    assert FakeDownloader.captured[-1]["skip_download"]
    assert FakeDownloader.captured[-1]["noplaylist"]


def test_live_stream_rejected(monkeypatch):
    class Live(FakeDownloader):
        def extract_info(self, url, download=False):
            data = super().extract_info(url, download)
            data["is_live"] = True
            return data
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Live))
    with pytest.raises(SingleVideoError, match="live"):
        inspect_single(YOUTUBE)


def test_exclusive_install(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"complete video data")
    output = tmp_path / "result.mp4"
    _install_exclusive(source, output)
    assert output.read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError):
        _install_exclusive(source, output)
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="FFmpeg and FFprobe required for end-to-end export",
)
def test_single_video_export_and_resume_safe(monkeypatch, tmp_path):
    _install_mock(monkeypatch)
    source = tmp_path / "media.mp4"
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "0.8", "-c:v", "libx264", "-threads", "1",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ]
    subprocess.run(command, check=True, capture_output=True)
    FakeDownloader.media = source
    store = ProjectStore(tmp_path / "state")
    project = inspect_single(YOUTUBE)
    project.output_dir = str(tmp_path / "exports")
    project.filename = "My tutorial"
    store.save(project)
    received = []
    dest = download_single(project, store, lambda *x: received.append(x), lambda: False)
    assert dest.is_file() and dest.suffix == ".mp4"
    assert store.load(project.id).status == "completed"
    assert received[-1][0] == "Complete"
    assert received[-1][1] == 100.0
    assert all("cookie" not in str(k) for k in FakeDownloader.captured[-1])
    assert FakeDownloader.captured[-1]["noplaylist"]
    with pytest.raises(FileExistsError):
        download_single(project, store, lambda *x: None, lambda: False)
    assert dest.is_file()


def test_cancelled_before_network(monkeypatch, tmp_path):
    _install_mock(monkeypatch)
    store = ProjectStore(tmp_path / "state")
    project = inspect_single(YOUTUBE)
    project.output_dir = str(tmp_path / "exports")
    with pytest.raises(SingleVideoError, match="Cancelled"):
        download_single(project, store, lambda *x: None, lambda: True)
    assert not list((tmp_path / "exports").glob("*.mp4"))
