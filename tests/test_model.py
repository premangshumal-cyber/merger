from pathlib import Path

import pytest

from playlistfusion.model import Project, Entry, ProjectStore, safe_filename
from playlistfusion.playlist import playlist_id, PlaylistError
from playlistfusion.engine import check_disk_space, DiskSpaceError


@pytest.mark.parametrize("url", [
    "https://youtube.com/playlist?list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq",
    "https://www.youtube.com/watch?v=abc123&list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq",
    "https://m.youtube.com/playlist?list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq&si=x",
])
def test_playlist_urls(url):
    assert playlist_id(url) == "PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq"


@pytest.mark.parametrize("url", [
    "https://evil.com/playlist?list=PLvmlgj6RmBxDW33heEOjWC1cSJq1xeDMq",
    "https://youtube.com/playlist", "file:///bad", "https://youtube.com.evil.org/?list=123456789012",
])
def test_reject_url(url):
    with pytest.raises(PlaylistError):
        playlist_id(url)


def test_filename_windows(tmp_path):
    assert safe_filename('a<>:"/\\|?*b') == "a_________b"
    assert safe_filename("CON") == "PlaylistFusion"
    assert safe_filename("ending. ") == "ending"


def test_project_round_trip_and_order(tmp_path):
    store = ProjectStore(tmp_path)
    entries = [
        Entry(uid="00001", title="One", index=1, video_id="abc"),
        Entry(uid="00002", title="One", index=2, video_id="abc"),
    ]
    p = Project(entries=entries)
    p.entries.reverse()
    p.entries[0].selected = False
    store.save(p)
    loaded = store.load(p.id)
    assert [e.uid for e in loaded.entries] == ["00002", "00001"]
    assert len(loaded.selected) == 1
    assert loaded.entries[1].video_id == "abc"


def test_recovery(tmp_path):
    store = ProjectStore(tmp_path)
    p = Project(status="processing")
    store.save(p)
    recovered = store.recent()[0]
    assert recovered.status == "interrupted"


def test_disk_preflight(tmp_path):
    with pytest.raises(DiskSpaceError):
        check_disk_space(tmp_path, 10**20)
