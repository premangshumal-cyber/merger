# PlaylistFusion

**One Playlist. One Video.** Windows desktop software for combining authorized playlist media and local video files into a chaptered MP4 or MKV.

> Status: initial implementation. The Windows installer must be built and verified by GitHub Actions before it can be considered a release.

## Capabilities

- PySide6 desktop UI with Dashboard, Playlist Editor, Processing, Completed, Settings.
- Metadata-only YouTube playlist analysis with yt-dlp and retrieval requiring explicit user authorization.
- Offline video import (MP4, MKV, MOV, AVI, WEBM); selection, drag reorder, missing-video replacement.
- FFprobe inspection; lossless concat where H.264/AAC streams are compatible; aspect-preserving H.264/AAC normalization when incompatible.
- Chapter metadata, sidecar chapter list, output stream/duration checks and output no-overwrite protection.
- Persistent manifests and resumable downloads where supported.
- GitHub Actions Windows packaging with PyInstaller, FFmpeg and Inno Setup.

## Single Video Downloader

Open **Single Video Downloader** in the left sidebar (or **Download One Video** on Dashboard), paste a standard YouTube watch/Shorts/youtu.be link, and click **Analyze Video**. Review the title, creator and duration. Choose a filename, destination, best quality or a resolution cap, and MP4/MKV. Click **Download Video** and confirm you have permission to save the video. The download has its own progress, cancellation, settings, verified output and Completed history; **it does not merge with other videos**.

MP4 export preserves compatible H.264/AAC files without re-encoding; sources using other codecs are converted for broad Windows playback. MKV can preserve more codecs without conversion. Partial downloads remain in local job storage to allow resuming where yt-dlp and the server support it. Live broadcasts and access-restricted videos are not bypassed.

## Run from source

Install Python 3.12 or newer and FFmpeg/FFprobe on PATH.

1. Create a virtual environment: python -m venv .venv
2. Activate it and run: python -m pip install -r requirements-dev.txt
3. Launch: python -m playlistfusion
4. Tests: python -m pytest -q

Offline video merging does not require access to YouTube.

## Install on Windows 10/11

Open the repository's **Actions** tab, select a successful **Windows release build**, and download the **PlaylistFusion-Windows** workflow artifact. Extract it and run PlaylistFusion-Setup.exe, or use PlaylistFusion-Portable.zip. Builds are unsigned and subject to Windows SmartScreen warnings.

To reproduce on Windows with Python installed, run PowerShell script packaging/build_windows.ps1. See USER_MANUAL.md.

## Limitations

This is a first functional codebase, not a verified production release. Live YouTube integration is not automatically tested. Download pause applies during download hooks only; FFmpeg processing cannot be paused. Sequential downloading and CPU H.264 encoding are currently used. Thumbnail previews, GPU encoding, concurrent downloading, automatic dependency updates, rich bandwidth reporting, and edge-case boundary-perfect merging are not implemented yet. No DRM, paywall, CAPTCHA or platform restriction bypass.

Only retrieve media when you have the legal rights and platform permission to do so. No browser cookies, passwords, telemetry or remote upload of source files. Third-party licenses must be respected; see THIRD_PARTY_NOTICES.md.
