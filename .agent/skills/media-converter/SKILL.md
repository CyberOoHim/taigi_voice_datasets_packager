---
name: media-converter
description: Professional media conversion utility. Converts video to audio and audio to audio with support for normalization, trimming, and custom codecs.
---

# Media Converter

This skill provides the `media-conv` command to convert media files (video to audio, or audio to audio). It is optimized for preparing datasets for ASR (Automatic Speech Recognition) training and general media manipulation.

## Setup

Install the package into your project's virtual environment from the **project root**:

```bash
.\.venv\Scripts\pip install -e .
```

*Requirements:*
- Python >= 3.10
- `ffmpeg` and `ffprobe` must be on your system PATH.
- (Optional) `rich` for formatted console output.

## Commands

### Media Convert (`media-conv`)

Converts, normalizes, and trims audio/video files. 

#### 1. Convert Subcommand
Converts file(s) to a target audio format.

```bash
.\.venv\Scripts\media-conv convert <input_file_or_pattern> [options]
```

**Key Arguments:**
- `-f, --format`: Output format (default: `wav`). Supported: `wav, mp3, flac, ogg, opus, aac, m4a, aiff, webm`.
- `-o, --output`: Output directory (default: same as input).
- `-r, --sample-rate`: Sample rate in Hz (default: `44100`). Use `16000` for ASR.
- `-c, --channels`: Audio channels (default: `2`). Use `1` for ASR.
- `-q, --quality`: Quality preset for lossy formats (`low`, `medium`, `high`, `best`).
- `-b, --bitrate`: Explicit bitrate (e.g., `128k`).
- `--normalize`: Normalize loudness to -23 LUFS / EBU R128 (recommended for ASR).
- `--start`: Start time for trimming (e.g., `00:01:30` or `90`).
- `--duration`: Duration to extract in seconds.
- `--ffmpeg-args`: Pass extra raw ffmpeg args.

#### 2. Info Subcommand
Show media info for one or more files.

```bash
.\.venv\Scripts\media-conv info <file1> <file2> ...
```

#### 3. Formats Subcommand
List all supported input and output formats.

```bash
.\.venv\Scripts\media-conv formats
```

## Examples (from Project Root)

**Convert a single MP4 to 16kHz mono WAV (ideal for ASR):**
```bash
.\.venv\Scripts\media-conv convert video.mp4 -f wav -r 16000 -c 1
```

**Convert all MKV files in a folder to high-quality MP3:**
```bash
.\.venv\Scripts\media-conv convert ./videos/*.mkv -f mp3 -q high -o ./audio/
```

**Convert with loudness normalization:**
```bash
.\.venv\Scripts\media-conv convert podcast.mp3 -f wav -r 16000 -c 1 --normalize
```

**Trim: extract 30s starting at 1m05s:**
```bash
.\.venv\Scripts\media-conv convert interview.mp4 -f wav --start 00:01:05 --duration 30
```
