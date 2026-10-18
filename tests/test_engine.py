"""Offline pipeline tests independent of YouTube or Qt."""
from pathlib import Path
import shutil
import subprocess
import pytest

from playlistfusion.engine import process, MissingVideos
from playlistfusion.model import Entry, Project, ProjectStore
from playlistfusion.media import probe

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="FFmpeg executables required",
)


def make_source(path: Path):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           "testsrc2=size=160x90:rate=25", "-f", "lavfi", "-i",
           "sine=frequency=660:sample_rate=48000", "-t", "0.9",
           "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def test_local_end_to_end_and_no_overwrite(tmp_path):
    source = tmp_path / "original.mp4"
    make_source(source)
    store = ProjectStore(tmp_path / "state")
    p = Project(
        title="Local example", output_dir=str(tmp_path),
        filename="result", entries=[Entry(uid="local0", title="Demo", index=1,
                                         local_path=str(source), status="ready")],
    )
    store.save(p)
    output = process(p, store, lambda *_: None, lambda: False)
    assert output.exists()
    assert probe(output).duration > 0.7
    assert store.load(p.id).status == "completed"
    with pytest.raises(FileExistsError):
        process(p, store, lambda *_: None, lambda: False)


def test_partial_requires_approval(tmp_path):
    source = tmp_path / "valid.mp4"
    make_source(source)
    store = ProjectStore(tmp_path / "state")
    p = Project(
        output_dir=str(tmp_path), filename="approved",
        entries=[
            Entry(uid="one", title="Valid", index=1, local_path=str(source), status="ready"),
            Entry(uid="two", title="Missing", index=2, local_path=str(tmp_path / "not-here.mp4")),
        ],
    )
    with pytest.raises(MissingVideos):
        process(p, store, lambda *_: None, lambda: False)
    assert p.status == "needs_approval"
    assert "Missing" in p.error
    p.allow_partial = True
    output = process(p, store, lambda *_: None, lambda: False)
    assert output.exists()
    assert p.status == "completed"
    assert "Missing" in p.error
