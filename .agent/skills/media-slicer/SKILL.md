---
name: media-slicer
description: Professional media splitter for AI training. Splits video/audio into clips paired with individual .srt files and a visual HTML review page. Supports granular control over lead-in/tail-out, custom SRT encodings, and project-aware pathing.
---

# Media Slicer

This skill provides commands to manipulate media files based on subtitle cues. It is optimized for building high-quality AI training datasets with surgical precision and robust handling of various SRT and CSV formats.

## Setup

Install the package into your project's virtual environment from the **project root**:

```bash
.\.venv\Scripts\pip install -e .
```

**Requirements:**
- Python >= 3.10
- `ffmpeg` and `ffprobe` must be on your system PATH.

**Hardware Acceleration:**
If an NVIDIA GPU is present and `torch` with CUDA support is installed (`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124`), the tools will automatically detect it and use the `h264_nvenc` hardware encoder for video re-encoding, drastically improving the speed of `--reencode` commands.

## Commands

### 1. Media Slice (`media-slice`)
Splits media files (video or audio) into individual clips based on SRT cues.

```bash
.\.venv\Scripts\media-slice --input <path_to_media> --srt <path_to_srt> [options]
```

**Key Arguments:**
- `--input`: Input video or audio file.
- `--srt`: Input .srt subtitle file.
- `--out`: Output directory.
- `--lead`: Lead-in ms before cue start (default: `150`).
- `--tail`: Tail ms after cue end (default: `80`).
- `--encoding`: Force a specific SRT encoding.

### 2. Media Compile (`media-compile`)
Takes an edited subtitle file (SRT or CSV) and a source media file, and generates a **single** combined media file containing only the segments present in the subtitle file. It intelligently joins contiguous clips and applies padding to the boundaries of contiguous blocks.

```bash
.\.venv\Scripts\media-compile --input <path_to_media> --subs <path_to_srt_or_csv> [options]
```

**Key Arguments:**
- `--input`: Input video or audio file.
- `--subs`: Input `.srt` or `.csv` subtitle file containing the desired segments.
- `--out`: Optional. Exact file path for the output compiled media. If omitted, defaults to `<input_stem>_compiled.<ext>` in the same directory as the input file. (Video outputs are forced to `.mp4`, audio outputs retain their original extension).
- `--head-pad`: Lead-in padding in seconds for the *first* clip of a contiguous block (default: `0.5`).
- `--tail-pad`: Tail-out padding in seconds for the *last* clip of a contiguous block (default: `0.5`).
- `--merge-gap`: Merge cues into a continuous block if the time gap between them is less than or equal to this many seconds (default: `1.5`).
- `--reencode`: Force re-encoding of extracted segments for perfect, glitch-free concatenation cuts.

## Reviewing Slices (for `media-slice`)
| Feature | Global Flag | Lead-only Flag | Tail-only Flag |
| :--- | :--- | :--- | :--- |
| **Re-encode** (Exact ms cuts) | `--reencode` / `--no-reencode` | - | - |
| **Mute** (Digital silence) | `--mute-pad` | `--mute-lead` | `--mute-tail` |

### Advanced Options:
- `--filter`: Only export clips whose text contains this string.
- `--verbose / -v`: Enable debug logging.
- `--quiet / -q`: Suppress informational output.
- `SRT_CLIPPER_ROOT` (Env Var): Set an absolute project root for all relative paths.

## Reviewing Slices

The skill automatically generates two critical tools for verification in the output folder:

1. **Per-clip .srt Files:** Every generated media clip (e.g., `0001.mp4`) has a matching `.srt` file.
2. **Visual Review Page:** Open **`_review.html`** in the output folder.
3. **Metadata Log:** A `metadata.json` file is saved in the output folder recording all arguments used.

## Examples (from Project Root)

*Handle an old SRT with specific encoding:*
```bash
.\.venv\Scripts\media-slice --input my_video.mp4 --srt my_video.srt --encoding latin-1
```

*Precise lead-in muting with debug output:*
```bash
.\.venv\Scripts\media-slice --input my_video.mp4 --srt my_video.srt --mute-lead --verbose
```
