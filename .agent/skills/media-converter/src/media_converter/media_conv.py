#!/usr/bin/env python3
"""
media_conv.py — Video-to-Audio & Audio-to-Audio Converter CLI
Requires: ffmpeg installed on your system
Install deps: pip install rich
"""

import argparse
import subprocess
import sys
import os
import shutil
import json
import glob
import shlex
from pathlib import Path
from typing import Any

# ─── Optional rich output ──────────────────────────────────────────

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich import print as rprint
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None

# ─── Constants ─────────────────────────────────────────────────────

SUPPORTED_VIDEO = [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpeg"]
SUPPORTED_AUDIO = [".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".m4a", ".wma", ".aiff", ".ac3"]
ALL_SUPPORTED   = SUPPORTED_VIDEO + SUPPORTED_AUDIO

OUTPUT_FORMATS  = ["wav", "mp3", "flac", "ogg", "opus", "aac", "m4a", "aiff", "webm"]

CODEC_MAP = {
    "wav":  "pcm_s16le",
    "mp3":  "libmp3lame",
    "flac": "flac",
    "ogg":  "libvorbis",
    "opus": "libopus",
    "aac":  "aac",
    "m4a":  "aac",
    "aiff": "pcm_s16be",
    "webm": "libopus",   # WebM audio container uses Opus codec
}

QUALITY_PRESETS = {
    "low":    {"mp3": "128k", "ogg": "96k",  "opus": "64k",  "aac": "96k",  "webm": "64k",  "default": "96k"},
    "medium": {"mp3": "192k", "ogg": "160k", "opus": "128k", "aac": "160k", "webm": "128k", "default": "160k"},
    "high":   {"mp3": "256k", "ogg": "224k", "opus": "192k", "aac": "256k", "webm": "192k", "default": "256k"},
    "best":   {"mp3": "320k", "ogg": "320k", "opus": "320k", "aac": "320k", "webm": "320k", "default": "320k"},
}

MAX_ERROR_DISPLAY = 800

# ─── Helpers ───────────────────────────────────────────────────────

def log(msg: str, style: str = "") -> None:
    if RICH and console:
        console.print(msg, style=style)
    else:
        print(msg)

def log_error(msg: str) -> None:
    if RICH and console:
        console.print(f"[bold red]ERROR:[/bold red] {msg}")
    else:
        print(f"ERROR: {msg}", file=sys.stderr)

def log_success(msg: str) -> None:
    if RICH and console:
        console.print(f"[bold green]✔[/bold green] {msg}")
    else:
        print(f"OK: {msg}")

def log_info(msg: str) -> None:
    if RICH and console:
        console.print(f"[cyan]ℹ[/cyan]  {msg}")
    else:
        print(f"INFO: {msg}")

def check_dependency(name: str) -> None:
    if shutil.which(name) is None:
        log_error(f"{name} not found. Please install it.")
        sys.exit(1)

def get_file_info(path: Path) -> dict[str, Any]:
    """Return basic ffprobe info for a file."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", str(path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except Exception:
        return {}

def resolve_bitrate(fmt: str, quality: str, bitrate: str | None) -> str | None:
    """Return bitrate string or None (for lossless formats)."""
    if fmt in ("wav", "flac", "aiff"):
        return None   # lossless — no bitrate needed
    if bitrate:
        return bitrate
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
    return preset.get(fmt, preset["default"])

def measure_loudness(input_path: Path, start: str | None, duration: float | None) -> dict[str, Any]:
    cmd = ["ffmpeg", "-hide_banner"]
    if start:
        cmd += ["-ss", start]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(input_path), "-af", "loudnorm=I=-23:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        lines = res.stderr.splitlines()
        json_lines = []
        in_json = False
        for line in lines:
            if line.strip() == "{":
                in_json = True
            if in_json:
                json_lines.append(line)
            if in_json and line.strip() == "}":
                break
        if json_lines:
            return json.loads("\n".join(json_lines))
    except Exception:
        pass
    return {}

# ─── Core conversion ───────────────────────────────────────────────

def build_ffmpeg_cmd(
    input_path: Path,
    output_path: Path,
    fmt: str,
    sample_rate: int,
    channels: int,
    bitrate: str | None,
    normalize: bool,
    normalize_two_pass: bool,
    loudness_stats: dict[str, Any],
    start: str | None,
    duration: float | None,
    extra_args: list[str],
    overwrite: bool,
) -> list[str]:
    codec = CODEC_MAP[fmt]
    cmd = ["ffmpeg"]
    
    if overwrite:
        cmd += ["-y"]
    else:
        cmd += ["-n"]
        
    if start:
        cmd += ["-ss", start]
    if duration is not None:
        cmd += ["-t", str(duration)]
        
    cmd += ["-i", str(input_path)]

    # Audio stream only
    cmd += ["-vn"]
    cmd += ["-acodec", codec]
    cmd += ["-ar", str(sample_rate)]
    cmd += ["-ac", str(channels)]

    if bitrate:
        cmd += ["-b:a", bitrate]

    # Normalize loudness to -23 LUFS (EBU R128)
    if normalize_two_pass and loudness_stats:
        meas_i = loudness_stats.get("input_i")
        meas_tp = loudness_stats.get("input_tp")
        meas_lra = loudness_stats.get("input_lra")
        meas_thresh = loudness_stats.get("input_thresh")
        
        if meas_i and meas_tp and meas_lra and meas_thresh:
            cmd += ["-af", f"loudnorm=I=-23:TP=-1.5:LRA=11:measured_I={meas_i}:measured_LRA={meas_lra}:measured_TP={meas_tp}:measured_thresh={meas_thresh}"]
        else:
            cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]
    elif normalize or normalize_two_pass:
        cmd += ["-af", "loudnorm=I=-23:TP=-1.5:LRA=11"]

    if extra_args:
        cmd += extra_args

    cmd.append(str(output_path))
    return cmd

def convert_file(
    input_path: Path,
    output_dir: Path,
    fmt: str,
    sample_rate: int,
    channels: int,
    quality: str,
    bitrate: str | None,
    normalize: bool,
    normalize_two_pass: bool,
    start: str | None,
    duration: float | None,
    extra_args: list[str],
    overwrite: bool,
    verbose: bool,
) -> bool:
    suffix = input_path.suffix.lower()
    if suffix not in ALL_SUPPORTED:
        log_error(f"Unsupported input format: {suffix}  ({input_path.name})")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    out_name = input_path.stem + "." + fmt
    output_path = output_dir / out_name

    resolved_bitrate = resolve_bitrate(fmt, quality, bitrate)
    
    loudness_stats = {}
    if normalize_two_pass:
        if verbose:
            log_info(f"Measuring loudness for {input_path.name}...")
        loudness_stats = measure_loudness(input_path, start, duration)

    cmd = build_ffmpeg_cmd(
        input_path, output_path, fmt,
        sample_rate, channels, resolved_bitrate,
        normalize, normalize_two_pass, loudness_stats,
        start, duration, extra_args, overwrite
    )

    if verbose:
        log_info("Command: " + " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True
        )
        if result.returncode != 0:
            log_error(f"ffmpeg failed for {input_path.name}")
            if not verbose and result.stderr:
                err_text = result.stderr[-MAX_ERROR_DISPLAY:]
                if RICH and console:
                    console.print(err_text)
                else:
                    print(err_text)
            return False
        log_success(f"{input_path.name}  →  {output_path}")
        return True
    except Exception as e:
        log_error(str(e))
        return False

# ─── Info subcommand ───────────────────────────────────────────────

def cmd_info(args: argparse.Namespace) -> None:
    check_dependency("ffprobe")
    for f in args.files:
        path = Path(f)
        if not path.exists():
            log_error(f"File not found: {f}")
            continue
        info = get_file_info(path)
        if not info:
            log_error(f"Could not probe: {f}")
            continue

        fmt_info   = info.get("format", {})
        streams    = info.get("streams", [])
        a_streams  = [s for s in streams if s.get("codec_type") == "audio"]
        v_streams  = [s for s in streams if s.get("codec_type") == "video"]

        if RICH and console:
            table = Table(title=f"[bold]{path.name}[/bold]", show_lines=True)
            table.add_column("Property", style="cyan")
            table.add_column("Value")
            table.add_row("Format",    fmt_info.get("format_long_name", "?"))
            table.add_row("Duration",  f"{float(fmt_info.get('duration', 0)):.2f}s")
            table.add_row("Size",      f"{int(fmt_info.get('size', 0)) / 1024 / 1024:.2f} MB")
            table.add_row("Bitrate",   f"{int(fmt_info.get('bit_rate', 0)) // 1000} kbps")
            for i, s in enumerate(a_streams):
                table.add_row(f"Audio [{i}]",
                    f"{s.get('codec_name','?')} | {s.get('sample_rate','?')} Hz | "
                    f"{s.get('channels','?')}ch | {s.get('bit_rate','?')} bps"
                )
            for i, s in enumerate(v_streams):
                table.add_row(f"Video [{i}]",
                    f"{s.get('codec_name','?')} | {s.get('width','?')}×{s.get('height','?')}"
                )
            console.print(table)
        else:
            print(f"\n=== {path.name} ===")
            print(f"  Format  : {fmt_info.get('format_long_name','?')}")
            print(f"  Duration: {float(fmt_info.get('duration',0)):.2f}s")
            print(f"  Size    : {int(fmt_info.get('size',0))/1024/1024:.2f} MB")
            for s in a_streams:
                print(f"  Audio   : {s.get('codec_name')} | {s.get('sample_rate')} Hz | {s.get('channels')}ch")

# ─── Convert subcommand ────────────────────────────────────────────

def cmd_convert(args: argparse.Namespace) -> None:
    check_dependency("ffmpeg")

    inputs = []
    for pattern in args.input:
        if "*" in pattern or "?" in pattern:
            matched = glob.glob(pattern, recursive=True)
            inputs.extend(sorted(Path(m) for m in matched))
        else:
            p = Path(pattern)
            if p.is_dir():
                for ext in ALL_SUPPORTED:
                    inputs.extend(sorted(p.rglob(f"*{ext}")))
            elif p.exists():
                inputs.append(p)
            else:
                log_error(f"Not found: {pattern}")

    if not inputs:
        log_error("No valid input files found.")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else None

    extra = shlex.split(args.ffmpeg_args) if args.ffmpeg_args else []

    ok = fail = 0
    for f in inputs:
        out_dir = output_dir if output_dir else f.parent
        success = convert_file(
            input_path         = f,
            output_dir         = out_dir,
            fmt                = args.format,
            sample_rate        = args.sample_rate,
            channels           = args.channels,
            quality            = args.quality,
            bitrate            = args.bitrate,
            normalize          = args.normalize,
            normalize_two_pass = args.normalize_two_pass,
            start              = args.start,
            duration           = args.duration,
            extra_args         = extra,
            overwrite          = args.overwrite,
            verbose            = args.verbose,
        )
        if success: ok += 1
        else:       fail += 1

    log("")
    if RICH and console:
        log(f"[bold]Done:[/bold] {ok} converted, {fail} failed")
    else:
        log(f"Done: {ok} converted, {fail} failed")
        
    if fail > 0:
        sys.exit(1)

# ─── Formats subcommand ────────────────────────────────────────────

def cmd_formats(_args: argparse.Namespace) -> None:
    if RICH and console:
        t = Table(title="Supported Formats", show_lines=True)
        t.add_column("Type");  t.add_column("Extensions")
        t.add_row("[green]Video Input[/green]",  "  ".join(SUPPORTED_VIDEO))
        t.add_row("[blue]Audio Input[/blue]",    "  ".join(SUPPORTED_AUDIO))
        t.add_row("[yellow]Audio Output[/yellow]","  ".join(OUTPUT_FORMATS))
        console.print(t)
    else:
        print("Video input :", "  ".join(SUPPORTED_VIDEO))
        print("Audio input :", "  ".join(SUPPORTED_AUDIO))
        print("Audio output:", "  ".join(OUTPUT_FORMATS))

# ─── Argument parser ───────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-conv",
        description="🎵  Video-to-Audio & Audio-to-Audio Converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # Convert a single MP4 to 16kHz mono WAV (ideal for ASR)
  media-conv convert video.mp4 -f wav -r 16000 -c 1

  # Convert all MKV files in a folder to high-quality MP3
  media-conv convert ./videos/*.mkv -f mp3 -q high -o ./audio/

  # Convert with loudness normalization (great for ASR training)
  media-conv convert podcast.mp3 -f wav -r 16000 -c 1 --normalize

  # Trim: extract 30s starting at 1m05s
  media-conv convert interview.mp4 -f wav --start 00:01:05 --duration 30

  # Convert FLAC to Opus at custom bitrate
  media-conv convert music.flac -f opus -b 128k

  # Inspect a file
  media-conv info video.mp4 audio.flac

  # List all supported formats
  media-conv formats
"""
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ─── convert ───
    conv = sub.add_parser("convert", help="Convert file(s) to a target audio format")
    conv.add_argument("input", nargs="+",
        help="Input file(s), glob pattern(s), or directories")

    conv.add_argument("-f", "--format", default="wav",
        choices=OUTPUT_FORMATS, metavar="FORMAT",
        help=f"Output format (default: wav). Choices: {', '.join(OUTPUT_FORMATS)}")

    conv.add_argument("-o", "--output", default=None, metavar="DIR",
        help="Output directory (default: same as input)")

    conv.add_argument("-r", "--sample-rate", type=int, default=44100,
        metavar="HZ",
        help="Sample rate in Hz (default: 44100). Use 16000 for ASR, 8000 for telephony")

    conv.add_argument("-c", "--channels", type=int, default=2, choices=[1, 2],
        metavar="N",
        help="Audio channels: 1=mono, 2=stereo (default: 2). Use 1 for ASR")

    conv.add_argument("-q", "--quality", default="high",
        choices=list(QUALITY_PRESETS.keys()),
        help="Quality preset for lossy formats (default: high). Ignored for wav/flac/aiff")

    conv.add_argument("-b", "--bitrate", default=None, metavar="BITRATE",
        help="Explicit bitrate, e.g. 128k, 256k (overrides --quality for lossy formats)")

    conv.add_argument("--normalize", action="store_true",
        help="Normalize loudness to -23 LUFS / EBU R128 (single-pass, recommended for ASR)")

    conv.add_argument("--normalize-two-pass", action="store_true",
        help="Normalize loudness to -23 LUFS using a two-pass approach for EBU R128 compliance")

    conv.add_argument("--start", default=None, metavar="TIME",
        help="Start time for trimming, e.g. 00:01:30 or 90 (seconds)")

    conv.add_argument("--duration", type=float, default=None, metavar="SEC",
        help="Duration to extract in seconds, e.g. 60")

    conv.add_argument("--ffmpeg-args", default=None, metavar="ARGS",
        help='Pass extra raw ffmpeg args as a quoted string, e.g. "--ffmpeg-args \\"-af aecho=0.8:0.88:60:0.4\\""')       

    conv.add_argument("-y", "--overwrite", action="store_true",
        help="Overwrite existing output files")

    conv.add_argument("-v", "--verbose", action="store_true",
        help="Print full ffmpeg output")

    conv.set_defaults(func=cmd_convert)

    # ─── info ───
    info = sub.add_parser("info", help="Show media info for one or more files")
    info.add_argument("files", nargs="+", help="Files to inspect")
    info.set_defaults(func=cmd_info)

    # ─── formats ───
    fmts = sub.add_parser("formats", help="List supported input/output formats")
    fmts.set_defaults(func=cmd_formats)

    return parser


# ─── Entry point ───────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Only show banner if we are not piping and it's a convert command
    if RICH and console and sys.stdout.isatty() and getattr(args, "command", None) == "convert":
        console.print(Panel.fit(
            "[bold cyan]media-conv[/bold cyan] — Audio Conversion CLI",
            border_style="cyan"
        ))

    args.func(args)


if __name__ == "__main__":
    main()
