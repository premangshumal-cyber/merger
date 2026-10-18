# Third-party license notices and redistribution responsibilities

PlaylistFusion application source is MIT licensed (LICENSE). Dependencies have their own licenses:

- PySide6 / Qt: LGPLv3 / GPL / commercial licensing choices. See https://www.qt.io/licensing and https://code.qt.io/
- yt-dlp: Unlicense. Source: https://github.com/yt-dlp/yt-dlp
- FFmpeg and FFprobe: LGPL 2.1+ or GPL depending on build options. Source, licensing and build information: https://ffmpeg.org/legal.html and https://ffmpeg.org/download.html
- PyInstaller: GPL with special exception. Source/license: https://pyinstaller.org/en/stable/license.html
- Inno Setup: Inno Setup license. https://jrsoftware.org/isinfo.php

The Windows build downloads FFmpeg essentials from gyan.dev at build time. Such binaries may contain GPL components. Anyone distributing compiled installers must check the actual binary licensing, include full licenses and corresponding source (including build configuration) as required. The source code being MIT licensed does not relicense the bundled multimedia binaries. Ensure users can replace Qt shared libraries where required by LGPL. These notices are informative and not a substitute for legal review.
