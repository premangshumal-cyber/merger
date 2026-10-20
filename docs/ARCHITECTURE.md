# PlaylistFusion architecture

Pipeline: YouTube metadata or local import → ordered project manifest → user selection → authorized retrieval → FFprobe inspection → conservative copy compatibility test → optional H.264/AAC normalization → FFmpeg concat demuxer → chapter metadata → output verification → completion.

The model module handles typed entries/projects and atomic JSON storage. The playlist module handles URL validation, flat metadata extraction and yt-dlp downloads. The media module performs FFprobe inspection, FFmpeg commands and chapter verification with structured subprocess argument arrays. The engine module handles resumable jobs, missing-entry approval and disk preflight. The UI module uses Qt worker threads and a five-section desktop application.

Single-video flow: strict host/path/video ID validation → metadata-only yt-dlp inspection → one-video project manifest → explicit UI authorization confirmation → yt-dlp transfer into project work folder → FFprobe inspection → container remux or H.264/AAC compatibility normalization if needed → cross-volume-safe, exclusive final publication → persisted completion. Metadata retrieval does not initiate a download. An existing final file is never overwritten; a failed transfer preserves partial work for bounded retries. No cookies, login, third-party media upload or DRM bypass is used.

Project JSON is stored per user and recovered after interruption. Verified downloaded source files remain inside project work storage. Transient normalization and concat artifacts are cleaned after completion/failure. Output is atomically renamed only after validation.

Offline tests generate synthetic media to verify copy concat, normalization with mixed resolutions and silent sources, chapter counts, cancellation, filenames, URL validation and recovery. CI cannot verify real-platform downloads without authorized test inputs.

Remaining engineering: field-testing hundreds of entries, uncommon codec/time-base behavior, exact frame-boundary continuity, output sizing, richer download progress, concurrent downloads, optional GPU encoding, thumbnails, installer licensing/compliance review, Windows QA and signing.
