# PlaylistFusion — User Manual

## YouTube playlist

1. On Dashboard paste a YouTube playlist URL that contains a list identifier.
2. Click Analyze Playlist to load its metadata. Private/deleted entries may show as unavailable.
3. In Playlist Editor select and reorder entries. The original order is preserved until changed.
4. Choose MP4/MKV, resolution cap, output filename/folder and optional chapters.
5. Click Download & Merge Playlist and confirm download authorization.
6. Watch Processing for stage, real percentage when available, messages and cancellation.
7. If retrieval fails, supply a permitted local video via Replace selected entry, or explicitly approve a partial merge and retry.
8. Find the merged video in Completed and its matching chapters.txt file beside the output.

## Download one YouTube video (without merging)

1. Select **Single Video Downloader** in the sidebar, or click **Download One Video** from Dashboard.
2. Paste an individual YouTube watch, youtu.be or Shorts URL and choose **Analyze Video**.
3. Confirm the displayed title, creator, duration and video identifier.
4. Choose output filename, destination folder, resolution (Best/2160/1440/1080/720/480/360) and MP4/MKV.
5. Click **Download Video** and affirm you have permission and platform authorization for the download.
6. Check the Processing page. The percentage shown is for the current transfer, not a fictitious overall download percentage; the bar becomes indeterminate for combining streams and verifying.
7. On success, find the file under **Completed** and choose Open selected output or Open output folder.

The single-video workflow never invokes the playlist merger. Selecting MKV may avoid re-encoding some codecs, while MP4 converts incompatible sources to H.264/AAC. Existing destinations are not overwritten; rename and retry instead. Aborted transfer data stays in per-project storage for retry where supported. Ongoing/upcoming live streams are intentionally unsupported.

## Offline video merging

Use Import Local Videos in Dashboard, or drag local videos into the app. Reorder, select, choose output and merge. Local media always stays on your computer.

## Important behavior

When source streams have matching H.264/AAC parameters, PlaylistFusion can stream-copy. Otherwise it uses a common 30 fps H.264/AAC output with black padding to preserve aspect ratio. Silent sources receive a silent audio stream. Normalization requires additional disk space and may reduce quality. Previously downloaded sources are preserved and reused across retries.

A merged output is committed only after FFprobe validation. Existing output files are not overwritten. An indeterminate progress bar means no percentage is known. Cancel processing rather than closing the application.

Project manifests are stored in the per-user PlaylistFusion app-data directory. Output videos are stored in the folder you select.

## Troubleshooting

If FFmpeg is missing, use the bundled Windows installer or place ffmpeg.exe and ffprobe.exe on PATH.

A private, paid, DRM-protected or otherwise inaccessible video cannot be bypassed; import an authorized local copy.

If the merge fails due to insufficient disk space, free space both in the output folder and your per-user project temporary folder.

After interruption, open Completed, select the project, choose Edit/retry project and start again.
