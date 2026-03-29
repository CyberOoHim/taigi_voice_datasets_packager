---
name: media-fetcher
description: Professional YouTube, Facebook, and X/Twitter content extraction. Fetch subtitles, transcripts, video, and audio directly into your project folders.
---

# YouTube / Facebook / X (Twitter) Fetcher

This skill provides a suite of professional CLI tools for extracting content from **YouTube, Facebook, and X/Twitter**. All tools are installed as global project commands within the virtual environment.

---

## Setup

Install the package in editable mode within your `.venv` from the **project root**.

### Windows (PowerShell)

```powershell
.\.venv\Scripts\pip install -e .
```

### macOS / Linux

```bash
.venv/bin/pip install -e .
```

### Cross-platform (any OS)

```bash
python -m pip install -e .
```

**Requirements:**

- Python >= 3.10
- `ffmpeg` + `ffprobe` for audio conversions (needed by commands 4, 8, and the YouTube-audio path of command 9).

**Hardware Acceleration:**
If an NVIDIA GPU is present and `torch` with CUDA support is installed in the environment, local video editing commands (`edit_sub` and `media-sync` when forced to re-encode) will automatically utilize the `h264_nvenc` hardware encoder to significantly speed up video rendering.

---

## Platform Support

| Platform | Video | Audio-only | Partial cut | Private/Login content |
|----------|-------|-----------|-------------|----------------------|
| YouTube | ✅ | ✅ | ✅ fragment-level | Needs cookies |
| Facebook | ✅ | ✅ | ✅ output only* | Needs `--cookies-from-browser` |
| X / Twitter | ✅ | ✅ | ✅ output only* | Needs `--cookies-from-browser` |

> **\* Partial cut on Facebook / X:** These platforms serve video as segmented DASH/HLS streams.  
> media-dlp must download **all** segments before ffmpeg can trim the result.  
> The output file is the correct partial clip, but the **full video is transferred over the network**.  
> This is a platform-level limitation — there is no way around it.  
> Clips originating from a URL are prefixed with **`partial_`**.  
> Cuts performed on local files are prefixed with **`local_cut_`**.

> **Audio-only on Facebook / X:** Both platforms expose dedicated audio streams in their DASH  
> manifests. `bestaudio` selects them directly — no post-download conversion step is needed.  
> ffmpeg is still required to convert to mp3/m4a/wav.

---

## Authentication (Cookies)

Private or login-required content on Facebook and X/Twitter requires passing browser cookies.

### Option A — Read directly from your browser (recommended)

Add `--cookies-from-browser <browser>` to any command.  
Supported browsers: `chrome`, `chromium`, `firefox`, `edge`, `brave`, `safari`, `vivaldi`, `opera`.

```bash
python -m media_fetcher.download_video https://www.facebook.com/.../videos/123 \
  --cookies-from-browser chrome
```

### Option B — Export a cookies.txt file

Use a browser extension (e.g. *Get cookies.txt LOCALLY*) to export a Netscape-format file, then:

```bash
python -m media_fetcher.download_video https://www.facebook.com/.../videos/123 \
  --cookies-file /path/to/cookies.txt
```

> **Note:** Public Facebook and X/Twitter videos generally work without any cookies.

---

## Commands

All commands support `--cookies-from-browser` and `--cookies-file` where relevant.  
All commands are shown in the portable `python -m` form.

### 1. Fetching Subtitles (YouTube)

```bash
python -m media_fetcher.fetch_subtitles <url> [--format <srt|csv>] [--output <file_path>] [--lang <language_code>]
```

- Windows: `.\.venv\Scripts\media-fetch-subs ...`
- macOS/Linux: `.venv/bin/media-fetch-subs ...`
- Fetches transcript/subtitles as SRT or CSV.
- Currently YouTube only (Facebook/X do not expose public transcript APIs).
- Default output: `media-subtitles/[title]_[timestamp].[format]`.

### 2. Fetching Subtitles — Facebook (and other media-dlp platforms)

```bash
python -m media_fetcher.fetch_subtitles_fb <url>
    [--format <srt|csv>] [--output <file_path>] [--lang <language_code>]
    [--no-auto] [--list-subs]
    [--cookies-from-browser <browser>] [--cookies-file <file>]
```

- Windows: `.\.venv\Scripts\media-fetch-subs-fb ...`
- macOS/Linux: `.venv/bin/media-fetch-subs-fb ...`
- Downloads captions from Facebook (and any other media-dlp–supported non-YouTube URL).
- Output format is **identical** to command 1 — the same SRT/CSV schema with the full
  metadata header — so all downstream tools (convert, extract-json, merge-translations)
  work unchanged.
- Default output: `media-subtitles/[title]_[timestamp].[format]`.

#### Platform behaviour

| Platform | Subtitles available? | Notes |
|----------|---------------------|-------|
| Facebook page / creator video | ✅ Often | Auto-generated (AI) or manually uploaded by creator |
| Facebook personal post / Reel | ❌ Rarely | Most have no captions; `--list-subs` will confirm |
| X / Twitter | ❌ Never | Command exits with a clear error message |

#### Key flags

| Flag | Purpose |
|------|---------|
| `--list-subs` | Print all available language tracks and exit — use this first to see what exists |
| `--lang en` | Prefer English; also matches `en_US`, `en_GB` via prefix matching |
| `--no-auto` | Only use manually uploaded subtitles, skip auto-generated |
| `--cookies-from-browser chrome` | Required for private / login-gated content |

#### Workflow

```bash
# Step 1 — check what subtitle tracks exist
python -m media_fetcher.fetch_subtitles_fb "https://www.facebook.com/SomePage/videos/123" \
  --list-subs

# Step 2 — download English captions as CSV (default)
python -m media_fetcher.fetch_subtitles_fb "https://www.facebook.com/SomePage/videos/123" \
  --lang en --format csv

# Step 3 — (optional) extract for translation, same as YouTube
python -m media_fetcher.extract_json media-subtitles/Some_Video_20260315_120000.csv \
  --output to_translate.json
```

#### Private video example

```bash
python -m media_fetcher.fetch_subtitles_fb "https://www.facebook.com/SomePage/videos/123" \
  --lang zh-TW --format srt --cookies-from-browser chrome
```

### 2. Downloading Video

```bash
python -m media_fetcher.download_video <url> [--subs <path>] [--output <file_path>]
    [--cookies-from-browser <browser>] [--cookies-file <file>]
```

- Windows: `.\.venv\Scripts\media-download-video ...`
- macOS/Linux: `.venv/bin/media-download-video ...`
- Downloads the video in the best available MP4 quality.
- Works with YouTube, Facebook, and X/Twitter URLs.
- If `--subs` is provided, the script extracts the URL from the subtitle file and does a FULL download.
- Default output: `media-downloads/video/[title]_[timestamp].mp4`.

### 4. Downloading Audio

```bash
python -m media_fetcher.download_audio <url> [--subs <path>] [--format <mp3|m4a|wav>] [--output <file_path>]
    [--cookies-from-browser <browser>] [--cookies-file <file>]
```

- Windows: `.\.venv\Scripts\media-download-audio ...`
- macOS/Linux: `.venv/bin/media-download-audio ...`
- Extracts the audio track and converts it to the specified format.
- Works with YouTube, Facebook, and X/Twitter URLs.
- If `--subs` is provided, the script extracts the URL from the subtitle file and does a FULL download.
- Default output: `media-downloads/audio/[title]_[timestamp].[format]`.
- **Requires ffmpeg** for audio conversion. Falls back to unconverted download if ffmpeg is missing.

### 5. Converting Subtitles

```bash
python -m media_fetcher.convert_subtitles <input_file> [--output <output_file>]
```

- Windows: `.\.venv\Scripts\media-convert-subs ...`
- macOS/Linux: `.venv/bin/media-convert-subs ...`
- Converts between `.srt` and `.csv`.

### 6. Extracting JSON for Translation

```bash
python -m media_fetcher.extract_json <input_file> [--output <output_file.json>]
```

- Windows: `.\.venv\Scripts\media-extract-json ...`
- macOS/Linux: `.venv/bin/media-extract-json ...`
- Extracts text into a simple JSON dictionary mapping indices to text.

### 7. Translating and Merging (AI Workflow)

To translate subtitles into Taigi and merge them:

```bash
# Step 1 – extract text for translation
python -m media_fetcher.extract_json original.srt --output to_translate.json

# Step 2 – perform AI translation on to_translate.json (external step)

# Step 3 – merge translations back into the subtitle file
python -m media_fetcher.merge_translations original.srt translated.json final_taigi.srt
```

- Windows: `.\.venv\Scripts\media-merge-translations ...`
- macOS/Linux: `.venv/bin/media-merge-translations ...`

### 8. Cutting Video or Audio (Edit Sub)

```bash
python -m media_fetcher.edit_sub <source> --start <time> --end <time>
    [--head-pad <sec>] [--tail-pad <sec>] [--audio] [--output <file_path>]
    [--cookies-from-browser <browser>] [--cookies-file <file>]
    [--reencode] [--subs <path>]
```

- Windows: `.\.venv\Scripts\media-edit-sub ...`
- macOS/Linux: `.venv/bin/media-edit-sub ...`
- Cuts a specific segment from a YouTube/Facebook/X URL or local file.
- Time formats: seconds (e.g., `120.5`), `MM:SS` (e.g., `02:00.5`), or `HH:MM:SS`.
- Default padding: 0.5 s head and 0.5 s tail.
- Use `--audio` to extract audio only (as mp3/wav/m4a).
- **`--reencode`**: Force full re-encoding of the media. This now defaults to **True** for all video cuts to ensure frame-accurate millisecond precision and prevent stream-copy errors. Use `--no-reencode` to disable.
- **`--subs`**: Pass a companion SRT/CSV file. A `resync_<name>` subtitle file will be generated perfectly aligned with the cut media, including updated traceability headers (`Sync Offset` and `Dropped Gaps`).
- **Requires ffmpeg** for all cutting and audio extraction.
- Default output folders:
    - Video: `media-downloads/video/`
    - Audio: `media-downloads/audio/`
- See the platform partial-cut note in the Platform Support table above.

### 9. Syncing Media from Subtitle Bounds (`media-sync`)

```bash
python -m media_fetcher.sync_media --subs <path_to_srt_or_csv> [--input <local_file>] [--audio] [--head-pad 0.5] [--tail-pad 0.5] [--reencode]
```

- Windows: `.\.venv\Scripts\media-sync ...`
- macOS/Linux: `.venv/bin/media-sync ...`

This command is a high-level wrapper around `edit_sub`. It reads a subtitle file, extracts the source URL (or uses a local `--input` file if provided), finds the exact start time of the first text cue and the end time of the last text cue, and runs a surgically padded download/extraction. 

**Local Video:**
When `--input` is provided, as well as for remote URLs, the command defaults to using `--reencode` for video files to ensure a frame-accurate cut and to prevent stream-copy errors (use `--no-reencode` to bypass).

**Resynced Subtitles:**
It automatically outputs a `resync_[subtitle_name]` file in the same directory. The original timestamps are shifted backward so they perfectly align with the new media clip (starting at `00:00:00` + your `--head-pad`). It injects a `Sync Offset: X.XXXs` metadata item into the header.

### 10. Patching Metadata

```bash
python -m media_fetcher.patch_metadata <file> [--url <url>]
```

- Windows: `.\.venv\Scripts\media-patch-meta ...`
- macOS/Linux: `.venv/bin/media-patch-meta ...`
- Adds or updates the metadata header in an existing `.srt` or `.csv` file.
- Works with YouTube, Facebook, and any other yt-dlp supported URL to fetch Title, Channel, and Duration.
- **Requires network access**.
- **Requires network access**.

---

## Examples (from Project Root)

### YouTube (unchanged)

```bash
python -m media_fetcher.download_video https://www.youtube.com/watch?v=F4zSxfBe5R0
python -m media_fetcher.fetch_subtitles https://www.youtube.com/watch?v=F4zSxfBe5R0 --format srt
python -m media_fetcher.download_audio https://www.youtube.com/watch?v=F4zSxfBe5R0 --format mp3
```

### Facebook — public video

```bash
python -m media_fetcher.download_video "https://www.facebook.com/SomePage/videos/123456789"
python -m media_fetcher.download_audio "https://www.facebook.com/SomePage/videos/123456789" --format mp3
```

### Facebook — private / friends-only video

```bash
python -m media_fetcher.download_video "https://www.facebook.com/SomePage/videos/123456789" \
  --cookies-from-browser chrome
```

### Facebook — cut a 30-second clip

```bash
python -m media_fetcher.edit_sub "https://www.facebook.com/SomePage/videos/123456789" \
  --start 01:20 --end 01:50 --audio
# ⚠️ Full video is downloaded; ffmpeg then cuts the segment.
```

### X / Twitter — public video

```bash
python -m media_fetcher.download_video "https://x.com/username/status/1234567890123456789"
python -m media_fetcher.download_audio "https://x.com/username/status/1234567890123456789" --format m4a
```

### X / Twitter — cut a clip (audio only)

```bash
python -m media_fetcher.edit_sub "https://twitter.com/username/status/1234567890123456789" \
  --start 00:05 --end 00:30 --audio
```

---

## New Utility Functions (utils.py)

| Function | Description |
|----------|-------------|
| `detect_platform(url)` | Returns `"youtube"`, `"facebook"`, `"twitter"`, or `"unknown"` |
| `is_supported_url(url)` | Returns `True` for any recognised or generic http(s) URL |
| `build_cookies_opts(browser, file)` | Returns media-dlp option dict for cookie auth |

---

## Known Limitations

| Limitation | Details |
|------------|---------|
| No FB/X subtitles | Neither platform exposes a public transcript/caption API accessible to media-dlp |
| FB DASH parse errors | Occasionally `[facebook] Cannot parse data` on newer videos; usually fixed by passing cookies even for public videos, or by updating media-dlp (`pip install -U media-dlp`) |
| X/Twitter combined streams | Some older tweets have a single video+audio stream with no separate audio track; media-dlp handles this automatically |
| Reel / Short URLs | FB Reels (`facebook.com/reel/…`) and Twitter video-only posts are supported; Shorts-style URLs are detected |
