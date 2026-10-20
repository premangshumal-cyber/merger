"""PlaylistFusion PySide6 desktop application.

All blocking yt-dlp/FFmpeg work runs in a QThread, never on the UI thread.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from .model import Entry, Project, ProjectStore, safe_filename
from .playlist import analyze
from .engine import process
from .media import executable
from .single_video import inspect_single, download_single, video_id

ACCENT = "#24d3c1"
STYLE = """
QWidget { background: #0b111b; color: #edf4f7; font: 10pt 'Segoe UI'; }
QMainWindow { background: #0b111b; }
QFrame#sidebar { background: #101b2a; border-right: 1px solid #223549; }
QFrame#card { background: #152235; border: 1px solid #273b51; border-radius: 16px; }
QLabel#headline { font-size: 26pt; font-weight: 750; color: #ffffff; }
QLabel#section { font-size: 17pt; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #a5b7c8; }
QPushButton { min-height: 34px; border-radius: 9px; border: 1px solid #2e4359;
              background: #1b2b40; padding: 5px 12px; }
QPushButton:hover { background: #274057; }
QPushButton:disabled { color: #798799; background: #111b29; }
QPushButton#primary { color: #041c20; background: #24d3c1; border: none; font-weight: 750; }
QPushButton#primary:hover { background: #5ce8dc; }
QPushButton#nav { text-align: left; padding-left: 18px; min-height: 44px; background: transparent;
                  border: none; color: #bccfe0; }
QPushButton#nav:checked { background: #1b3345; color: #67efe0; border-left: 3px solid #24d3c1; }
QLineEdit, QComboBox, QListWidget, QPlainTextEdit {
    background: #101c2b; border: 1px solid #304459; border-radius: 9px;
    min-height: 33px; padding: 5px 9px; selection-background-color: #197e7a; }
QListWidget { padding: 5px; }
QListWidget::item { padding: 10px 5px; border-bottom: 1px solid #233648; }
QListWidget::item:selected { background: #204459; border-radius: 6px; }
QProgressBar { border: 1px solid #2c495b; background: #132433; border-radius: 9px; height: 20px;
               text-align: center; }
QProgressBar::chunk { background: #24d3c1; border-radius: 8px; }
QCheckBox { spacing: 9px; }
QScrollArea { border: none; }
"""


def button(text: str, clicked=None, primary=False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if clicked:
        b.clicked.connect(clicked)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def column(parent=None) -> QVBoxLayout:
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(14)
    return layout


def card() -> tuple[QFrame, QVBoxLayout]:
    widget = QFrame()
    widget.setObjectName("card")
    layout = column(widget)
    layout.setContentsMargins(20, 20, 20, 20)
    return widget, layout


def heading(text: str, level="section") -> QLabel:
    label = QLabel(text)
    label.setObjectName(level)
    label.setWordWrap(True)
    return label


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def human(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = round(seconds)
    return f"{total // 3600:02}:{total // 60 % 60:02}:{total % 60:02}"


class Worker(QThread):
    progress_changed = Signal(str, object, str)
    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, mode: str, project: Project | None, store: ProjectStore,
                 url: str = "", speed_limit: int = 0):
        super().__init__()
        self.mode = mode
        self.project = project
        self.store = store
        self.url = url
        self.speed_limit = speed_limit
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.stage = ""

    def cancelled(self) -> bool:
        return self._stop.is_set()

    def cancel(self) -> None:
        self._stop.set()
        self._pause.clear()

    def toggle_pause(self) -> bool:
        if self._pause.is_set():
            self._pause.clear()
            return False
        self._pause.set()
        return True

    def report(self, stage: str, pct: float | None, detail: str) -> None:
        self.stage = stage
        if stage == "Downloading":
            while self._pause.is_set() and not self._stop.wait(0.15):
                pass
        if self.cancelled():
            raise RuntimeError("Cancelled by user")
        self.progress_changed.emit(stage, pct, detail)

    def run(self) -> None:
        try:
            if self.mode == "analyze":
                self.result_ready.emit(analyze(self.url, self.report))
            elif self.mode == "single_analyze":
                self.result_ready.emit(inspect_single(self.url, self.report))
            elif self.mode == "single_download":
                assert self.project is not None
                path = download_single(self.project, self.store, self.report,
                                       self.cancelled, self.speed_limit)
                self.result_ready.emit(str(path))
            else:
                assert self.project is not None
                path = process(self.project, self.store, self.report,
                               self.cancelled, self.speed_limit)
                self.result_ready.emit(str(path))
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class EntriesList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store = ProjectStore()
        self.settings = self.store.get_settings()
        self.project: Project | None = None
        self.single_project: Project | None = None
        self.worker: Worker | None = None
        self.setWindowTitle("PlaylistFusion  •  One Playlist. One Video.")
        self.resize(1180, 780)
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)
        self._build()
        self.refresh_history()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 28, 12, 20)
        side.setSpacing(9)
        brand = heading("▶  PlaylistFusion", "section")
        brand.setStyleSheet("color: #24d3c1; font-size: 16pt;")
        side.addWidget(brand)
        side.addWidget(muted("One Playlist. One Video."))
        side.addSpacing(27)

        self.nav = []
        pages = ((0, "Dashboard"), (5, "Single Video Downloader"),
                 (1, "Playlist Editor"), (2, "Processing"),
                 (3, "Completed"), (4, "Settings"))
        self.nav_pages = [index for index, _ in pages]
        for index, title in pages:
            b = button(title, lambda _=False, page=index: self.navigate(page))
            b.setObjectName("nav")
            b.setCheckable(True)
            self.nav.append(b)
            side.addWidget(b)
        side.addStretch()
        side.addWidget(muted("Local-first • No telemetry"))
        outer.addWidget(sidebar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self._dashboard()
        self._editor()
        self._processing()
        self._completed()
        self._settings_page()
        self._single_video_page()
        self.navigate(0)

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for page, b in zip(self.nav_pages, self.nav):
            b.setChecked(page == index)
        if index == 3:
            self.refresh_history()

    def _dashboard(self):
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Your videos, seamlessly combined", "headline"))
        v.addWidget(muted("Paste an authorized YouTube playlist or work fully offline with local videos."))
        area, body = card()
        body.addWidget(heading("Start a new merge"))
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.youtube.com/playlist?list=...")
        self.url.returnPressed.connect(self.start_analysis)
        body.addWidget(self.url)
        actions = QHBoxLayout()
        actions.addWidget(button("Paste from Clipboard", self.paste))
        actions.addWidget(button("Analyze Playlist", self.start_analysis, primary=True))
        actions.addWidget(button("Import Local Videos", self.import_files))
        actions.addWidget(button("Download One Video", lambda: self.navigate(5)))
        body.addLayout(actions)
        v.addWidget(area)
        v.addWidget(heading("Recent projects"))
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(self.load_recent)
        v.addWidget(self.recent, 1)
        self.dash_message = muted("Double-click a recent project to continue where you left off.")
        v.addWidget(self.dash_message)
        self.stack.addWidget(page)

    def _single_video_page(self):
        """A separate download workflow; it never invokes the playlist merger."""
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Single Video Downloader", "headline"))
        v.addWidget(muted(
            "Save one authorized YouTube video as MP4 or MKV. "
            "No playlist merging is required."))
        input_card, body = card()
        body.addWidget(heading("YouTube video link"))
        self.single_url = QLineEdit()
        self.single_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.single_url.returnPressed.connect(self.start_single_analysis)
        body.addWidget(self.single_url)
        row = QHBoxLayout()
        row.addWidget(button("Paste from Clipboard", lambda: self.single_url.setText(
            QApplication.clipboard().text().strip())))
        row.addWidget(button("Analyze Video", self.start_single_analysis, primary=True))
        body.addLayout(row)
        v.addWidget(input_card)

        info_card, info = card()
        info.addWidget(heading("Video information"))
        self.single_details = muted("Paste a single video link, then click Analyze Video.")
        info.addWidget(self.single_details)
        v.addWidget(info_card)

        settings_card, settings = card()
        settings.addWidget(heading("Download settings"))
        form = QFormLayout()
        form.setSpacing(12)
        self.single_filename = QLineEdit()
        self.single_filename.setPlaceholderText("Video title (editable after analysis)")
        form.addRow("Save as", self.single_filename)
        self.single_folder = QLineEdit(self.settings.get("output_dir", str(Path.home() / "Videos")))
        form.addRow("Save to", self.single_folder)
        choose = button("Browse…", self.choose_single_folder)
        form.addRow("", choose)
        self.single_quality = QComboBox()
        self.single_quality.addItems(["best", "2160", "1440", "1080", "720", "480", "360"])
        form.addRow("Maximum resolution", self.single_quality)
        self.single_format = QComboBox()
        self.single_format.addItems(["mp4", "mkv"])
        form.addRow("Output format", self.single_format)
        settings.addLayout(form)
        self.single_download_button = button(
            "Download Video", self.start_single_download, primary=True)
        self.single_download_button.setEnabled(False)
        settings.addWidget(self.single_download_button)
        settings.addWidget(muted(
            "Only download content you own or are expressly authorized to retrieve, "
            "when permitted by platform rules. MP4 may require H.264/AAC encoding "
            "for compatibility. Inaccessible videos cannot be bypassed."))
        v.addWidget(settings_card)
        v.addStretch()
        self.stack.addWidget(page)

    def choose_single_folder(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose download folder", self.single_folder.text())
        if directory:
            self.single_folder.setText(directory)

    def start_single_analysis(self):
        if self._busy():
            return
        url = self.single_url.text().strip()
        try:
            video_id(url)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid single video link", str(exc))
            return
        self.single_project = None
        self.single_download_button.setEnabled(False)
        self._launch(Worker("single_analyze", None, self.store, url),
                     "Analyzing single video")

    def show_single_project(self, project: Project):
        self.single_project = project
        self.single_url.setText(project.source_url)
        self.single_filename.setText(project.filename)
        self.single_folder.setText(project.output_dir)
        self.single_quality.setCurrentText(project.quality)
        self.single_format.setCurrentText(project.output_format)
        entry = project.entries[0]
        resolution = (f"{entry.width} × {entry.height}" if entry.width else
                      "Choose a download quality below")
        self.single_details.setText(
            f"Title: {entry.title}\n{project.stage}\n"
            f"Duration: {human(entry.duration)}\n"
            f"Resolution: {resolution}\nVideo ID: {entry.video_id}\n"
            f"Status: {project.status}" +
            (f"\nOutput: {project.output_path}" if project.output_path else ""))
        self.single_download_button.setEnabled(not self._busy())
        self.navigate(5)

    def start_single_download(self):
        if not self.single_project or self._busy():
            return
        project = self.single_project
        try:
            if video_id(self.single_url.text()) != project.entries[0].video_id:
                raise ValueError("The link changed. Analyze the new video first.")
            speed = max(0, int(self.speed_box.text().strip() or "0"))
        except (ValueError, Exception) as exc:
            QMessageBox.warning(self, "Check download settings", str(exc))
            return
        response = QMessageBox.question(
            self, "Download authorization",
            "Confirm you own this video or have the necessary authorization to "
            "download it, and that downloading is permitted by the platform rules.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if response != QMessageBox.StandardButton.Yes:
            return
        project.filename = self.single_filename.text().strip() or project.title
        project.output_dir = self.single_folder.text().strip() or str(Path.home() / "Videos")
        project.output_format = self.single_format.currentText()
        project.quality = self.single_quality.currentText()
        project.status = "ready"
        self.store.save(project)
        self._launch(Worker("single_download", project, self.store,
                            speed_limit=speed), "Preparing single video download")

    def _editor(self):
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Playlist Editor", "headline"))
        self.project_title = muted("Start by analyzing a playlist or importing local files.")
        v.addWidget(self.project_title)
        splitter = QSplitter()
        left, l = card()
        l.addWidget(heading("Videos & order"))
        self.entries = EntriesList()
        self.entries.itemChanged.connect(self._on_entry_changed)
        self.entries.model().rowsMoved.connect(lambda *args: self._sync_order())
        self.entries.itemSelectionChanged.connect(self._show_selection)
        l.addWidget(self.entries, 1)
        controls = QHBoxLayout()
        for title, handler in [
            ("Select all", lambda: self.set_all(True)),
            ("Deselect all", lambda: self.set_all(False)),
            ("Move up", lambda: self.move_entry(-1)),
            ("Move down", lambda: self.move_entry(1)),
        ]:
            controls.addWidget(button(title, handler))
        l.addLayout(controls)
        l.addWidget(button("Replace selected entry with local file", self.replace_entry))
        self.selected_info = muted("0 videos selected")
        l.addWidget(self.selected_info)
        splitter.addWidget(left)

        right, r = card()
        r.addWidget(heading("Output settings"))
        form = QFormLayout()
        form.setSpacing(14)
        self.name_edit = QLineEdit()
        form.addRow("Filename", self.name_edit)
        self.folder_edit = QLineEdit(self.settings.get("output_dir", str(Path.home() / "Videos")))
        form.addRow("Output folder", self.folder_edit)
        form.addRow("", button("Browse folder", self.choose_folder))
        self.format_box = QComboBox()
        self.format_box.addItems(["mp4", "mkv"])
        form.addRow("Format", self.format_box)
        self.quality_box = QComboBox()
        self.quality_box.addItems(["best", "2160", "1440", "1080", "720", "480", "360"])
        form.addRow("Maximum download height", self.quality_box)
        self.chapter_box = QCheckBox("Embed one chapter per video")
        self.chapter_box.setChecked(True)
        form.addRow("", self.chapter_box)
        self.audio_box = QCheckBox("Normalize audio loudness (re-encodes)")
        form.addRow("", self.audio_box)
        r.addLayout(form)
        self.details = muted("Select an entry to see its media status.")
        r.addWidget(self.details)
        r.addStretch()
        self.start_button = button("Download & Merge Playlist", self.start_merge, primary=True)
        r.addWidget(self.start_button)
        r.addWidget(muted("Download only videos you own or are authorized to retrieve. "
                          "If retrieval fails, you can supply authorized local copies."))
        splitter.addWidget(right)
        splitter.setSizes([660, 400])
        v.addWidget(splitter, 1)
        self.stack.addWidget(page)

    def _processing(self):
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Processing", "headline"))
        v.addWidget(muted("Real progress when FFmpeg or the downloader reports it. "
                          "A moving bar without a percentage means progress is unknown."))
        summary, body = card()
        self.stage_label = heading("No active job")
        self.detail_label = muted("Start a merge from the Playlist Editor.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        body.addWidget(self.stage_label)
        body.addWidget(self.detail_label)
        body.addWidget(self.progress_bar)
        tools = QHBoxLayout()
        self.pause_button = button("Pause download", self.pause_worker)
        self.cancel_button = button("Cancel", self.cancel_worker)
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        tools.addWidget(self.pause_button)
        tools.addWidget(self.cancel_button)
        tools.addStretch()
        body.addLayout(tools)
        v.addWidget(summary)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Processing log")
        v.addWidget(self.logs, 1)
        self.stack.addWidget(page)

    def _completed(self):
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Completed & saved projects", "headline"))
        v.addWidget(muted("Projects are saved locally. Interrupted projects can be reopened."))
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self.open_project_from_history)
        v.addWidget(self.history, 1)
        actions = QHBoxLayout()
        actions.addWidget(button("Open selected output", self.open_selected_output, primary=True))
        actions.addWidget(button("Open output folder", self.open_selected_folder))
        actions.addWidget(button("Edit / retry project", self.edit_selected_project))
        v.addLayout(actions)
        self.stack.addWidget(page)

    def _settings_page(self):
        page = QWidget()
        v = column(page)
        v.addWidget(heading("Settings", "headline"))
        box, l = card()
        form = QFormLayout()
        self.speed_box = QLineEdit(str(self.settings.get("speed_limit_kib", 0)))
        self.speed_box.setPlaceholderText("0 = unlimited")
        form.addRow("Download speed limit (KiB/s)", self.speed_box)
        self.default_folder = QLineEdit(self.settings.get("output_dir", str(Path.home() / "Videos")))
        form.addRow("Default output folder", self.default_folder)
        l.addLayout(form)
        l.addWidget(button("Save settings", self.save_settings, primary=True))
        dependencies = []
        for name in ("ffmpeg", "ffprobe"):
            try:
                dependencies.append(f"{name}: {executable(name)}")
            except Exception as exc:
                dependencies.append(f"{name}: NOT AVAILABLE — {exc}")
        l.addWidget(muted("\n".join(dependencies)))
        l.addWidget(muted("Python runtime and yt-dlp are bundled in Windows releases. "
                          "The app does not read passwords, cookies or send telemetry."))
        v.addWidget(box)
        v.addStretch()
        self.stack.addWidget(page)

    def paste(self):
        self.url.setText(QApplication.clipboard().text().strip())

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start_analysis(self):
        if self._busy():
            return
        url = self.url.text().strip()
        from .playlist import playlist_id
        try:
            playlist_id(url)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid playlist link", str(exc))
            return
        self._launch(Worker("analyze", None, self.store, url), "Analyzing playlist")

    def import_files(self):
        if self._busy():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose videos in desired order", "",
            "Videos (*.mp4 *.mkv *.mov *.avi *.webm);;All files (*)")
        if paths:
            self.add_local(paths)

    def add_local(self, paths):
        if not self.project or self.project.source_url:
            self.project = Project(title="Local video merge", filename="My Merged Video")
        for path in paths:
            p = Path(path)
            if not p.is_file() or p.suffix.lower() not in {".mp4", ".mkv", ".mov", ".avi", ".webm"}:
                continue
            uid = f"local-{len(self.project.entries):05d}"
            self.project.entries.append(Entry(
                uid=uid, title=p.stem, index=len(self.project.entries) + 1,
                local_path=str(p), status="ready"))
        self.store.save(self.project)
        self.show_project(self.project)

    def show_project(self, project):
        self.project = project
        self.url.setText(project.source_url)
        self.project_title.setText(f"{project.title}  •  {len(project.entries)} entries")
        self.name_edit.setText(project.filename)
        self.folder_edit.setText(project.output_dir)
        self.format_box.setCurrentText(project.output_format)
        self.quality_box.setCurrentText(project.quality)
        self.chapter_box.setChecked(project.chapter_markers)
        self.audio_box.setChecked(project.normalize_audio)
        self.render_entries()
        self.navigate(1)

    def render_entries(self):
        self.entries.blockSignals(True)
        self.entries.clear()
        if self.project:
            for entry in self.project.entries:
                tag = f"  {human(entry.duration)}   •   {entry.status}"
                item = QListWidgetItem(f"{entry.index:03}   {entry.title}\n{tag}")
                item.setData(Qt.ItemDataRole.UserRole, entry.uid)
                item.setToolTip(entry.error or entry.url or entry.local_path)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable |
                              Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked if entry.selected else Qt.CheckState.Unchecked)
                self.entries.addItem(item)
        self.entries.blockSignals(False)
        self.update_totals()

    def _sync_order(self):
        if not self.project:
            return
        lookup = {entry.uid: entry for entry in self.project.entries}
        ordered = [lookup[self.entries.item(i).data(Qt.ItemDataRole.UserRole)]
                   for i in range(self.entries.count())]
        self.project.entries = ordered
        self.store.save(self.project)
        self.render_entries()

    def _on_entry_changed(self, item):
        if not self.project:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        for entry in self.project.entries:
            if entry.uid == uid:
                entry.selected = item.checkState() == Qt.CheckState.Checked
                break
        self.store.save(self.project)
        self.update_totals()

    def update_totals(self):
        selected = self.project.selected if self.project else []
        known = sum(e.duration for e in selected)
        unknown = sum(e.duration <= 0 for e in selected)
        message = f"{len(selected)} selected  •  known duration {human(known)}"
        if unknown:
            message += f"  •  {unknown} durations not yet known"
        self.selected_info.setText(message)

    def _show_selection(self):
        item = self.entries.currentItem()
        if not item or not self.project:
            return
        entry = next((e for e in self.project.entries
                      if e.uid == item.data(Qt.ItemDataRole.UserRole)), None)
        if entry:
            self.details.setText(
                f"Video #{entry.index}\n{entry.title}\n\nStatus: {entry.status}"
                f"\nDuration: {human(entry.duration)}\n"
                f"Dimensions: {entry.width or '—'} × {entry.height or '—'}\n"
                f"Source: {entry.local_path or entry.url or 'Not available'}\n"
                f"{entry.error}"
            )

    def set_all(self, selected: bool):
        for i in range(self.entries.count()):
            self.entries.item(i).setCheckState(
                Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)

    def move_entry(self, change: int):
        at = self.entries.currentRow()
        new = at + change
        if at < 0 or not (0 <= new < self.entries.count()):
            return
        item = self.entries.takeItem(at)
        self.entries.insertItem(new, item)
        self.entries.setCurrentRow(new)
        self._sync_order()

    def replace_entry(self):
        if not self.project or not self.entries.currentItem():
            QMessageBox.information(self, "Select a video", "Choose an entry to replace.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select authorized local replacement", "",
            "Videos (*.mp4 *.mkv *.mov *.avi *.webm)")
        if not path:
            return
        uid = self.entries.currentItem().data(Qt.ItemDataRole.UserRole)
        for entry in self.project.entries:
            if entry.uid == uid:
                entry.local_path = path
                entry.status = "ready"
                entry.error = ""
                entry.selected = True
                break
        self.store.save(self.project)
        self.render_entries()

    def choose_folder(self):
        location = QFileDialog.getExistingDirectory(self, "Select output folder", self.folder_edit.text())
        if location:
            self.folder_edit.setText(location)

    def start_merge(self):
        if not self.project or self._busy():
            return
        if not self.project.selected:
            QMessageBox.warning(self, "Nothing selected", "Select at least one video.")
            return
        self.project.filename = self.name_edit.text().strip() or "PlaylistFusion"
        self.project.output_dir = self.folder_edit.text().strip() or str(Path.home() / "Videos")
        self.project.output_format = self.format_box.currentText()
        self.project.quality = self.quality_box.currentText()
        self.project.chapter_markers = self.chapter_box.isChecked()
        self.project.normalize_audio = self.audio_box.isChecked()
        bad = [e for e in self.project.selected
               if e.status == "unavailable" and not (e.local_path and Path(e.local_path).exists())]
        if bad:
            names = "\n".join(f"• {e.index}. {e.title}" for e in bad[:12])
            response = QMessageBox.question(
                self, "Unavailable entries", "These selected videos cannot be retrieved:\n"
                + names + "\n\nProceed with a partial merge, excluding these entries?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
            self.project.allow_partial = True
        if self.project.source_url and any(e.selected and not e.local_path for e in self.project.entries):
            confirm = QMessageBox.question(
                self, "Authorization required",
                "Download only content you own or are expressly authorized to retrieve, "
                "when permitted by platform rules. Do you confirm you have the necessary rights?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirm != QMessageBox.StandardButton.Yes:
                return
        else:
            self.project.allow_partial = bool(bad)
        self.store.save(self.project)
        try:
            speed = max(0, int(self.speed_box.text().strip() or "0"))
        except ValueError:
            speed = 0
        self._launch(Worker("process", self.project, self.store, speed_limit=speed),
                     "Preparing media")

    def _launch(self, worker: Worker, title: str):
        self.worker = worker
        self.stage_label.setText(title)
        self.detail_label.setText("Starting…")
        self.progress_bar.setRange(0, 0)
        self.logs.clear()
        self.cancel_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Pause download")
        self.start_button.setEnabled(False)
        self.single_download_button.setEnabled(False)
        worker.progress_changed.connect(self._on_progress)
        worker.result_ready.connect(self._on_result)
        worker.error_occurred.connect(self._on_failure)
        worker.finished.connect(self._on_finished)
        self.navigate(2)
        worker.start()

    def _on_progress(self, stage: str, percent, detail: str):
        self.stage_label.setText(stage)
        self.detail_label.setText(detail or stage)
        if percent is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(max(0, min(1000, round(float(percent) * 10))))
        if detail:
            self.logs.appendPlainText(f"[{stage}] {detail}")
        self.pause_button.setEnabled(stage == "Downloading" and self._busy())

    def _on_result(self, value):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        if isinstance(value, Project):
            self.store.save(value)
            if value.kind == "single":
                self.show_single_project(value)
            else:
                self.show_project(value)
        else:
            was_single = self.worker is not None and self.worker.mode == "single_download"
            headline = "Video downloaded successfully!" if was_single else "Playlist successfully merged!"
            self.stage_label.setText(headline)
            self.detail_label.setText(value)
            self.logs.appendPlainText(f"Completed: {value}")
            self.refresh_history()
            QMessageBox.information(self, "Completed", f"{headline}\n\n{value}")
            self.navigate(3)

    def _on_failure(self, reason):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stage_label.setText("Operation interrupted")
        self.detail_label.setText(reason[:300])
        self.logs.appendPlainText("ERROR: " + reason)
        if self.worker and self.worker.mode == "process" and self.project and self.project.status == "needs_approval":
            response = QMessageBox.question(
                self, "Missing videos", reason[:1600] +
                "\n\nApprove a partial merge containing only successfully available videos?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if response == QMessageBox.StandardButton.Yes:
                self.project.allow_partial = True
                self.store.save(self.project)
                self.show_project(self.project)
                QMessageBox.information(self, "Retry merge",
                    "Approval saved. Press Download & Merge Playlist again to continue.")
                return
        QMessageBox.warning(self, "PlaylistFusion", reason[:2200])

    def _on_finished(self):
        self.cancel_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.start_button.setEnabled(True)
        if self.single_project:
            self.single_download_button.setEnabled(True)
        if self.worker:
            self.worker.deleteLater()
        self.worker = None

    def pause_worker(self):
        if self.worker and self.worker.stage == "Downloading":
            paused = self.worker.toggle_pause()
            self.pause_button.setText("Resume download" if paused else "Pause download")

    def cancel_worker(self):
        if self.worker:
            self.cancel_button.setEnabled(False)
            self.worker.cancel()
            self.logs.appendPlainText("Cancellation requested. Preserving verified sources.")

    def refresh_history(self):
        projects = self.store.recent(recover=not self._busy())
        for target in (self.recent, self.history):
            target.clear()
            for p in projects:
                kind = "Single video" if p.kind == "single" else "Video merge"
                line = (f"{p.title}\n{kind}  •  {p.status.upper()}  •  {len(p.entries)} videos  •  "
                        f"{p.updated[:16].replace('T', ' ')}")
                item = QListWidgetItem(line)
                item.setData(Qt.ItemDataRole.UserRole, p.id)
                item.setToolTip(p.error or p.output_path)
                target.addItem(item)

    def _load_item(self, item):
        if not item:
            return None
        try:
            return self.store.load(item.data(Qt.ItemDataRole.UserRole))
        except Exception as exc:
            QMessageBox.warning(self, "Project not found", str(exc))
            return None

    def load_recent(self, item):
        p = self._load_item(item)
        if p:
            self.show_single_project(p) if p.kind == "single" else self.show_project(p)

    def open_project_from_history(self, item):
        p = self._load_item(item)
        if p:
            self.show_single_project(p) if p.kind == "single" else self.show_project(p)

    def _history_project(self):
        return self._load_item(self.history.currentItem())

    def open_selected_output(self):
        p = self._history_project()
        if p and Path(p.output_path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p.output_path))
        else:
            QMessageBox.information(self, "File unavailable", "No completed output exists for that project.")

    def open_selected_folder(self):
        p = self._history_project()
        if p:
            path = Path(p.output_path).parent if p.output_path else Path(p.output_dir)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def edit_selected_project(self):
        p = self._history_project()
        if p:
            self.show_single_project(p) if p.kind == "single" else self.show_project(p)

    def save_settings(self):
        try:
            rate = int(self.speed_box.text() or "0")
            if rate < 0:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Invalid setting", "Speed limit must be 0 or a positive integer.")
            return
        self.settings = {"speed_limit_kib": rate, "output_dir": self.default_folder.text().strip()}
        self.store.save_settings(self.settings)
        QMessageBox.information(self, "Settings saved", "Preferences saved locally.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths and not self._busy():
            self.add_local(paths)
            event.acceptProposedAction()

    def closeEvent(self, event):
        if self._busy():
            QMessageBox.warning(self, "Processing active",
                                "Cancel processing first to preserve your project safely.")
            event.ignore()
        else:
            event.accept()


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("PlaylistFusion")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
