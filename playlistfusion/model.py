"""Persistent project model and local JSON storage."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(name: str, fallback: str = "PlaylistFusion") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    value = re.sub(r"\s+", " ", value)
    if not value or value.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = fallback
    return value[:140].rstrip(" .") or fallback


@dataclass
class Entry:
    uid: str
    title: str
    index: int
    video_id: str = ""
    url: str = ""
    duration: float = 0.0
    thumbnail: str = ""
    selected: bool = True
    status: str = "pending"
    local_path: str = ""
    error: str = ""
    width: int = 0
    height: int = 0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Entry:
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class Project:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "Untitled project"
    kind: str = "playlist"  # playlist, local, or single
    source_url: str = ""
    entries: list[Entry] = field(default_factory=list)
    output_dir: str = field(default_factory=lambda: str(Path.home() / "Videos"))
    filename: str = "PlaylistFusion"
    output_format: str = "mp4"
    quality: str = "best"
    chapter_markers: bool = True
    normalize_audio: bool = False
    allow_partial: bool = False
    status: str = "ready"
    stage: str = "Ready"
    output_path: str = ""
    error: str = ""
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Project:
        props = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__ and k != "entries"}
        props["entries"] = [Entry.from_json(e) for e in raw.get("entries", [])]
        return cls(**props)

    @property
    def selected(self) -> list[Entry]:
        return [e for e in self.entries if e.selected]


class ProjectStore:
    def __init__(self, root: Path | None = None):
        base = os.getenv("LOCALAPPDATA") if os.name == "nt" else None
        self.root = Path(root or (Path(base) if base else Path.home() / ".local" / "share") / "PlaylistFusion")
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)

    def save(self, project: Project) -> None:
        project.updated = now()
        dest = self.projects / f"{project.id}.json"
        temp = dest.with_suffix(".json.tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(asdict(project), stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, dest)

    def load(self, project_id: str) -> Project:
        if not re.fullmatch(r"[0-9a-f]{32}", project_id):
            raise ValueError("Invalid project ID")
        with (self.projects / f"{project_id}.json").open(encoding="utf-8") as stream:
            return Project.from_json(json.load(stream))

    def recent(self, limit: int = 30) -> list[Project]:
        result = []
        for path in sorted(self.projects.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                project = self.load(path.stem)
                if project.status == "processing":
                    project.status = "interrupted"
                    project.stage = "Interrupted — retry to resume"
                    self.save(project)
                result.append(project)
                if len(result) >= limit:
                    break
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return result

    def temp_dir(self, project: Project) -> Path:
        path = self.root / "work" / project.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_settings(self) -> dict:
        try:
            with (self.root / "settings.json").open(encoding="utf-8") as fd:
                return json.load(fd)
        except (FileNotFoundError, ValueError):
            return {}

    def save_settings(self, settings: dict) -> None:
        path = self.root / "settings.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.replace(temp, path)
