"""Recoverable orchestration of retrieval and video processing."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from .model import Project, ProjectStore, safe_filename
from .playlist import download
from .media import merge_files, probe, MediaError


class MissingVideos(RuntimeError):
    pass


class DiskSpaceError(RuntimeError):
    pass


def check_disk_space(folder: Path, required: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if free < required:
        raise DiskSpaceError(
            f"Insufficient storage: need approximately {required / 2**30:.2f} GiB, "
            f"available {free / 2**30:.2f} GiB. Choose another output/temporary folder."
        )


def process(project: Project, store: ProjectStore,
            progress: Callable[[str, float | None, str], None],
            cancelled: Callable[[], bool], speed_limit_kib: int = 0) -> Path:
    """Carry a saved manifest across retries; only selected and approved entries enter output."""
    if not project.selected:
        raise ValueError("Select at least one video.")
    work = store.temp_dir(project)
    output_dir = Path(project.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = safe_filename(project.filename)
    fmt = project.output_format if project.output_format in ("mp4", "mkv") else "mp4"
    output = output_dir / f"{basename}.{fmt}"
    if output.exists():
        raise FileExistsError(f"{output.name} already exists. Choose another output name to avoid overwriting it.")
    local_bytes = sum(Path(e.local_path).stat().st_size for e in project.selected
                      if e.local_path and Path(e.local_path).is_file())
    check_disk_space(output_dir, max(256 * 2**20, int(local_bytes * 1.6)))
    check_disk_space(work, max(256 * 2**20, int(local_bytes * 2.5)))
    project.status = "processing"
    project.error = ""
    store.save(project)
    sources: list[Path] = []
    titles: list[str] = []
    missing: list[str] = []
    try:
        for index, entry in enumerate(project.selected):
            if cancelled():
                raise RuntimeError("Cancelled")
            progress("Retrieving files", None, f"{index + 1}/{len(project.selected)} — {entry.title}")
            path = Path(entry.local_path) if entry.local_path else None
            if path is None or not path.is_file():
                if entry.url and entry.video_id and entry.status != "unavailable":
                    try:
                        path = download(entry, work / "downloads", project.quality,
                                        progress, cancelled, speed_limit_kib)
                        entry.local_path = str(path)
                        entry.status = "downloaded"
                        entry.error = ""
                    except Exception as exc:
                        entry.status = "unavailable"
                        entry.error = str(exc)
                        missing.append(f"{entry.index}. {entry.title}: {exc}")
                else:
                    missing.append(f"{entry.index}. {entry.title}: file unavailable")
            if path and path.is_file():
                try:
                    info = probe(path)
                    entry.duration = info.duration
                    entry.width = info.width
                    entry.height = info.height
                    entry.status = "ready"
                    sources.append(path)
                    titles.append(entry.title)
                except Exception as exc:
                    entry.status = "unavailable"
                    entry.error = str(exc)
                    missing.append(f"{entry.index}. {entry.title}: {exc}")
            store.save(project)
        if missing and not project.allow_partial:
            project.status = "needs_approval"
            project.error = "Some selected videos could not be retrieved:\n" + "\n".join(missing)
            store.save(project)
            raise MissingVideos(project.error)
        if not sources:
            raise MissingVideos("No readable source videos. Import local copies and retry.")
        progress("Inspecting media", None, f"Inspecting {len(sources)} sources")
        data, copied = merge_files(sources, titles, output,
                                   project.chapter_markers, project.normalize_audio,
                                   progress, cancelled)
        project.output_path = str(output)
        project.status = "completed"
        project.stage = "Completed without re-encoding" if copied else "Completed with compatibility encoding"
        project.error = ("Omitted entries:\n" + "\n".join(missing)) if missing else ""
        store.save(project)
        progress("Complete", 100.0, str(output))
        return output
    except Exception as exc:
        if project.status != "needs_approval":
            project.status = "interrupted" if cancelled() else "failed"
            project.error = str(exc)
            project.stage = "Cancelled" if cancelled() else "Failed"
            store.save(project)
        raise
