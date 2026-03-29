"""
srt_clipper.py — SRT-based media splitter (video + audio)
==========================================================
Splits any video or audio file into per-subtitle clips and eliminates
preceding residue and trailing bleed through four complementary strategies:

  1. Gap enforcement   — rewrites adjacent cue boundaries in memory so there
                         is always >= GAP_MS of silence before each new cue.

  2. Keyframe snapping — (video only, stream-copy mode) probes ALL I-frame
                         positions once at startup, then uses bisect per clip;
                         prevents macro-block / stale-frame artifacts.
                         Snap-back is capped at --max-snap (default 0.8 s).

  3. Re-encode mode    — decodes and re-encodes to cut at the exact
                         millisecond; eliminates ~50 ms stream-copy rounding.

  4. Mute padding      — the lead-in and tail regions are faded to silence so
                         any lingering speech is completely inaudible.
                         Skips the fade-in when lead_s == 0 (cue at t=0).
                         Implies audio re-encode; video is stream-copied unless
                         --reencode is also given.

Supported input formats
-----------------------
  Video : .mp4 .mkv .mov .avi .webm  (anything ffmpeg can decode)
  Audio : .mp3 .aac .m4a .wav .flac .ogg .opus
"""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
import functools
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pysrt as pysrt_mod

# ── optional dependencies ─────────────────────────────────────────────────────
try:
    import pysrt
except ImportError:
    sys.exit(
        "Missing dependency: pysrt\n"
        "Install with:  pip install pysrt          (inside a virtualenv)\n"
        "           or: pip install --user pysrt   (no virtualenv needed)"
    )

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── logging setup ─────────────────────────────────────────────────────────────
log = logging.getLogger("srt_clipper")

# ── module-level constants ────────────────────────────────────────────────────
LEAD_MS = 150  # ms to start BEFORE cue (residue buffer)
TAIL_MS = 80  # ms to keep AFTER cue   (bleed buffer)
GAP_MS = 120  # minimum gap enforced between adjacent cue boundaries
MIN_DUR_S = 0.4  # skip cues shorter than this
REENCODE = False  # True = exact ms cuts via libx264 + aac
MUTE_PAD = False  # True = fade lead/tail audio to silence
CRF = 18  # video re-encode quality (18–23 visually lossless)
PRESET = "veryfast"
MAX_SNAP_BACK_S = 0.8  # keyframe snap cap: never snap back further than this
AUDIO_BIT_RATE = "192k"  # aac output bitrate
MP3_QUALITY = "2"  # libmp3lame -q:a value (0=best, 9=worst)
FADE_DUR = 0.030  # crossfade duration at mute boundaries (seconds)
PROBE_TIMEOUT = 60  # ffprobe timeout in seconds (covers large 4K files)
CLIP_TIMEOUT = 300  # per-clip ffmpeg timeout in seconds

AUDIO_EXTS = frozenset({".mp3", ".aac", ".m4a", ".wav", ".flac", ".ogg", ".opus"})

# Fallback encodings tried in order when UTF-8 SRT parsing fails
SRT_FALLBACK_ENCODINGS = ("latin-1", "cp1252", "shift_jis", "gb2312")

# _ROOT_MARKERS is defined canonically in root_finder.py; do not duplicate here.
# ─────────────────────────────────────────────────────────────────────────────


# ── startup checks ────────────────────────────────────────────────────────────


def check_dependencies() -> None:
    """Exit with a clear message if ffmpeg or ffprobe are not on PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(
                f"ERROR: '{tool}' not found on PATH.\n"
                f"Install ffmpeg (includes ffprobe): https://ffmpeg.org/download.html"
            )


# ── tiny helpers ──────────────────────────────────────────────────────────────


def sec(t: pysrt_mod.SubRipTime) -> float:
    """pysrt SubRipTime -> float seconds."""
    return t.ordinal / 1000.0


def fmt(seconds: float) -> str:
    """Float seconds -> ffmpeg time string HH:MM:SS.mmm (clamped to >= 0)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def dur_str(seconds: float) -> str:
    """Float seconds -> plain decimal string suitable for ffmpeg -t."""
    return f"{max(0.0, seconds):.3f}"


def safe_name(text: str, max_chars: int = 45) -> str:
    """
    Convert subtitle text to a filesystem-safe string.

    Preserves Unicode word characters (CJK, Cyrillic, etc.) but removes
    characters forbidden on Windows/macOS/Linux.  Falls back to 'clip'
    only if the result is empty after processing.
    """
    text = re.sub(r"<[^>]+>", "", text)  # strip <i>, <b>, etc.
    text = unicodedata.normalize("NFC", text)
    # Remove OS-forbidden characters: < > : " / \ | ? * and control chars
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_chars] or "clip"


def strip_html(text: str) -> str:
    """Remove SRT HTML tags (used for manifest text column)."""
    return re.sub(r"<[^>]+>", "", text)


from .root_finder import get_project_root


def is_audio_only(path: str) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTS


def output_ext(input_path: str) -> str:
    """Audio-only -> keep original extension; video -> .mp4."""
    ext = Path(input_path).suffix.lower()
    return ext if ext in AUDIO_EXTS else ".mp4"


# ── media probing ─────────────────────────────────────────────────────────────


def probe_media_duration(path: str) -> float:
    """
    Return media duration in seconds via ffprobe.

    Returns float('inf') on failure and logs a warning — callers must
    handle inf gracefully (tail clamping will be skipped).
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        log.warning(
            "Could not probe media duration (%s). "
            "Tail clamping at EOF will be skipped.",
            e,
        )
        return float("inf")


def probe_all_keyframes(path: str) -> list[float]:
    """
    Probe the entire video file once and return a sorted list of all
    I-frame timestamps (seconds).

    Returns [] for audio-only files or on any ffprobe error.
    Callers should use snap_to_keyframe() with bisect for O(log n) lookups.
    """
    if is_audio_only(path):
        return []
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "noref",
        "-show_entries",
        "frame=pts_time,pict_type",
        "-of",
        "json",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT
        )
        data = json.loads(result.stdout)
        return sorted(
            float(f["pts_time"])
            for f in data.get("frames", [])
            if f.get("pict_type") == "I"
        )
    except Exception as e:
        log.warning(
            "Keyframe probe failed (%s). Stream-copy cuts will use raw timestamps.",
            e,
        )
        return []


def snap_to_keyframe(
    all_keyframes: list[float], target: float, max_snap: float
) -> float:
    """
    Return the latest keyframe that is <= target AND within max_snap seconds
    of target (snap-back cap prevents silencing actual cue audio).
    """
    if not all_keyframes:
        return target
    idx = bisect.bisect_right(all_keyframes, target) - 1
    if idx < 0:
        return target
    candidate = all_keyframes[idx]
    if (target - candidate) <= max_snap:
        return candidate
    return target


# ── SRT loading ───────────────────────────────────────────────────────────────


def extract_srt_metadata(path: str, encoding_override: str | None = None) -> dict[str, str]:
    """
    Extract metadata headers from the top of an SRT file.
    Headers are typically key-value pairs before the first subtitle (e.g. Title: ..., Video ID: ...).
    """
    encodings = ("utf-8-sig",) + SRT_FALLBACK_ENCODINGS
    if encoding_override:
        encodings = (encoding_override,)
    
    metadata = {"Sync Offset": "0.000s", "Dropped Gaps": "[]"}
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    line = line.strip()
                    # Stop parsing headers if we hit the SRT separator, the first index "1", or CSV headers
                    if line == "----------------------------------------" or line == "1" or line.lower().startswith("index,"):
                        break
                    if ":" in line:
                        key, val = line.split(":", 1)
                        metadata[key.strip()] = val.strip()
            return metadata
        except UnicodeDecodeError:
            continue
    return {}


def load_srt(path: str, encoding_override: str | None = None) -> pysrt_mod.SubRipFile:
    """
    Load an SRT file, trying UTF-8-BOM first, then common fallback encodings.
    """
    if encoding_override:
        try:
            return pysrt.open(path, encoding=encoding_override)
        except UnicodeDecodeError:
            sys.exit(
                f"ERROR: could not decode '{path}' with encoding '{encoding_override}'."
            )

    encodings = ("utf-8-sig",) + SRT_FALLBACK_ENCODINGS
    for enc in encodings:
        try:
            subs = pysrt.open(path, encoding=enc)
            if enc != "utf-8-sig":
                log.info("SRT loaded with fallback encoding '%s'", enc)
            return subs
        except UnicodeDecodeError:
            continue
    sys.exit(
        f"ERROR: could not decode '{path}' with any of: {', '.join(encodings)}\n"
        f"Try re-saving the file as UTF-8, or pass --encoding <name>."
    )


# ── SRT preprocessing ─────────────────────────────────────────────────────────


def enforce_gaps(
    subs: list[pysrt_mod.SubRipItem], gap_ms: int
) -> list[pysrt_mod.SubRipItem]:
    """
    Return a **deep copy** of *subs* where every pair of adjacent cues has
    >= *gap_ms* of silence between them.
    """
    subs = copy.deepcopy(subs)
    for i in range(len(subs) - 1):
        curr = subs[i]
        nxt = subs[i + 1]
        gap = nxt.start.ordinal - curr.end.ordinal
        if gap < gap_ms:
            new_end = nxt.start.ordinal - gap_ms
            if new_end > curr.start.ordinal:
                curr.end = pysrt.SubRipTime.from_ordinal(new_end)
            else:
                # Collapse the cue so min-dur catches and eliminates it securely
                curr.end = pysrt.SubRipTime.from_ordinal(curr.start.ordinal)
    return subs


# ── audio fade filter ─────────────────────────────────────────────────────────


def audio_fade_filter(
    lead_s: float,
    cue_dur: float,
    tail_s: float,
    mute_lead: bool,
    mute_tail: bool,
) -> str:
    """
    Build an ffmpeg afade filter chain that silences the lead-in and/or tail.
    """
    filters: list[str] = []
    total = lead_s + cue_dur + tail_s

    if mute_lead and lead_s > 0:
        # Fade in completes right as the actual cue starts
        fi_dur = lead_s
        filters.append(f"afade=t=in:ss=0:d={fi_dur:.4f}")

    if mute_tail:
        fo_start = max(0.0, lead_s + cue_dur - FADE_DUR)
        fo_dur = total - fo_start
        if fo_dur > 0:
            filters.append(f"afade=t=out:st={fo_start:.4f}:d={fo_dur:.4f}")

    return ",".join(filters)


# ── ffmpeg command builder ────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _get_video_codec() -> tuple[str, list[str]]:
    """Auto-detect if NVIDIA GPU is available via PyTorch to use NVENC and return (codec, [extra_args])."""
    try:
        import torch
        if torch.cuda.is_available():
            log.info("Using NVIDIA GPU (NVENC) for video encoding.")
            # NVENC uses -cq for CRF-like behavior and p6 for slower/high quality
            return "h264_nvenc", ["-cq", "18", "-rc", "vbr", "-preset", "p6"]
    except ImportError:
        pass
    log.info("Using CPU (libx264) for video encoding.")
    return "libx264", []


def build_cmd(
    input_path: str,
    clip_start: float,
    clip_dur: float,
    outfile: str,
    reencode: bool,
    mute_lead: bool,
    mute_tail: bool,
    lead_s: float,
    cue_dur: float,
    tail_s: float,
    crf: int,
    preset: str,
) -> list[str]:
    """Assemble the ffmpeg command for one clip."""
    audio_only = is_audio_only(input_path)
    need_audio_encode = reencode or mute_lead or mute_tail

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        fmt(clip_start),
        "-i",
        input_path,
        "-t",
        dur_str(clip_dur),
    ]

    af = (
        audio_fade_filter(lead_s, cue_dur, tail_s, mute_lead, mute_tail)
        if (mute_lead or mute_tail)
        else None
    )

    if audio_only:
        if need_audio_encode:
            if af:
                cmd += ["-af", af]
            ext = Path(input_path).suffix.lower()
            if ext == ".mp3":
                cmd += ["-c:a", "libmp3lame", "-q:a", MP3_QUALITY]
            elif ext == ".flac":
                cmd += ["-c:a", "flac"]
            elif ext == ".wav":
                cmd += ["-c:a", "pcm_s16le"]
            else:
                cmd += ["-c:a", "aac", "-b:a", AUDIO_BIT_RATE]
        else:
            cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        # ── video file ────────────────────────────────────────────────────
        if reencode:
            vcodec, vargs = _get_video_codec()
            
            # Override crf/preset if libx264 is used
            if vcodec == "libx264":
                vargs = ["-crf", str(crf), "-preset", preset]
                
            cmd += [
                "-c:v",
                vcodec,
                *vargs,
                "-movflags",
                "+faststart",
            ]
        else:
            cmd += ["-c:v", "copy", "-avoid_negative_ts", "make_zero"]

        if need_audio_encode:
            if af:
                cmd += ["-af", af]
            cmd += ["-c:a", "aac", "-b:a", AUDIO_BIT_RATE]
        else:
            cmd += ["-c:a", "copy"]

    cmd.append(outfile)
    return cmd


# ── processing pipeline ───────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split a video or audio file into per-subtitle clips, "
            "eliminating preceding residue and trailing bleed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Input video or audio file")
    parser.add_argument("--srt", required=True, help="Input .srt subtitle file")
    parser.add_argument("--out", default=None, help="Output directory (default: <input_dir>/clips/<input_stem>)")
    parser.add_argument(
        "--lead",
        type=int,
        default=LEAD_MS,
        help=f"Lead-in ms before cue start  (default {LEAD_MS})",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=TAIL_MS,
        help=f"Tail ms after cue end  (default {TAIL_MS})",
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=GAP_MS,
        help=f"Minimum gap ms between adjacent cues  (default {GAP_MS})",
    )
    parser.add_argument(
        "--min-dur",
        type=float,
        default=MIN_DUR_S,
        help=f"Skip cues shorter than N seconds  (default {MIN_DUR_S})",
    )

    # Precision controls
    parser.add_argument(
        "--reencode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Full re-encode for exact millisecond cuts (default: True, use --no-reencode to disable)",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Extract audio-only as WAV (default) or MP3 fallback",
    )

    # Mute controls
    parser.add_argument(
        "--mute-pad",
        action="store_true",
        default=MUTE_PAD,
        help="Fade BOTH lead-in and tail audio to silence",
    )
    parser.add_argument(
        "--mute-lead",
        action="store_true",
        help="Fade ONLY lead-in audio to silence",
    )
    parser.add_argument(
        "--mute-tail",
        action="store_true",
        help="Fade ONLY tail audio to silence",
    )

    parser.add_argument(
        "--crf",
        type=int,
        default=CRF,
        help=f"Video re-encode quality CRF  (default {CRF})",
    )
    parser.add_argument(
        "--preset",
        default=PRESET,
        help=f"Video re-encode speed preset  (default {PRESET})",
    )
    parser.add_argument(
        "--max-snap",
        type=float,
        default=MAX_SNAP_BACK_S,
        help=f"Max seconds to snap back to a keyframe  (default {MAX_SNAP_BACK_S})",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Only export clips whose subtitle text contains this string",
    )
    parser.add_argument(
        "--encoding",
        default=None,
        help="Force a specific encoding when reading the SRT file (e.g. latin-1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print ffmpeg commands without executing",
    )
    parser.add_argument(
        "--no-probe", action="store_true", help="Skip keyframe probing"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose / debug logging",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all informational output (errors still shown)",
    )
    return parser.parse_args()


def configure_logging(verbose: bool, quiet: bool) -> None:
    """Set up the module-level logger based on CLI flags."""
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(level)


def load_and_filter_subs(
    srt_path: str,
    gap_ms: int,
    filter_text: str | None,
    encoding_override: str | None = None,
) -> list[pysrt_mod.SubRipItem]:
    """
    Load SRT, apply text filter FIRST, then enforce gaps on the
    filtered set so adjacency relationships are correct.
    """
    subs = list(load_srt(srt_path, encoding_override=encoding_override))
    log.info("Loaded %d cues", len(subs))

    # Filter before gap enforcement so gaps are based on exported cues only
    if filter_text:
        before = len(subs)
        subs = [s for s in subs if filter_text.lower() in s.text.lower()]
        log.info("Filter '%s': %d -> %d cues", filter_text, before, len(subs))

    subs = enforce_gaps(subs, gap_ms)
    return subs


def write_clip_srt(
    outfile: str, cue_text: str, cue_dur: float, lead_s: float
) -> None:
    """
    Write a single-cue SRT file that shows the subtitle at the correct
    offset within the clip (accounting for lead-in padding).

    The cue spans from lead_s to lead_s + cue_dur — i.e. exactly the
    speech segment, excluding tail padding so the subtitle disappears at
    the natural end of the utterance rather than during the tail.
    """
    srt_path = Path(outfile).with_suffix(".srt")
    cue_start_in_clip = lead_s
    cue_end_in_clip = lead_s + cue_dur  # NOT clip_dur (which includes tail)

    def srt_ts(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(
            f"1\n"
            f"{srt_ts(cue_start_in_clip)} --> {srt_ts(cue_end_in_clip)}\n"
            f"{cue_text}\n\n"
        )


def process_clips(
    args: argparse.Namespace,
    subs: list[pysrt_mod.SubRipItem],
    out_path: Path,
    media_dur: float,
    all_keyframes: list[float],
) -> list[dict]:
    """
    Main loop: build and execute one ffmpeg command per cue.
    Returns a list of manifest row dicts (empty in --dry-run mode).
    """
    if getattr(args, "audio", False):
        ext = ".wav"
        audio_only = True
    else:
        ext = output_ext(args.input)
        audio_only = is_audio_only(args.input)
        
    lead_s = args.lead / 1000.0
    tail_s = args.tail / 1000.0

    # Resolve effective mute flags once
    effective_mute_lead = args.mute_pad or args.mute_lead
    effective_mute_tail = args.mute_pad or args.mute_tail

    manifest_rows: list[dict] = []
    skipped = 0
    processed = 0
    errors = 0

    # Disable tqdm progress bar during a dry-run to prevent console flashing
    use_tqdm = HAS_TQDM and not args.dry_run
    iterator = (
        tqdm(enumerate(subs, 1), total=len(subs), unit="clip")
        if use_tqdm
        else enumerate(subs, 1)
    )

    for i, sub in iterator:
        raw_start = sec(sub.start)
        raw_end = sec(sub.end)
        cue_dur = raw_end - raw_start

        # Filter on raw cue duration (speech length), not clip_dur which is
        # always larger by lead+tail.  A cue of 0.38 s raw speech is still
        # skipped when --min-dur 0.3 because we want a minimum of clean speech,
        # not minimum of padded audio.
        if cue_dur < args.min_dur:
            skipped += 1
            log.debug(
                "[%d/%d] SKIP  (%.2fs < %.2fs)  %r",
                i,
                len(subs),
                cue_dur,
                args.min_dur,
                sub.text[:40],
            )
            continue

        # ── clip window ───────────────────────────────────────────────────
        clip_start = max(0.0, raw_start - lead_s)
        clip_end = min(raw_end + tail_s, media_dur)  # clamp to EOF
        clip_dur = clip_end - clip_start

        actual_lead = raw_start - clip_start  # may be < lead_s if near t=0
        actual_tail = clip_end - raw_end  # may be < tail_s if near EOF

        # ── keyframe snapping ─────────────────────────────────────────────
        if (
            not audio_only
            and not args.reencode
            and not args.no_probe
            and all_keyframes
        ):
            snapped = snap_to_keyframe(all_keyframes, clip_start, args.max_snap)
            if snapped < clip_start:
                extra = clip_start - snapped
                clip_start = snapped
                clip_dur = clip_end - clip_start
                actual_lead += extra

        # ── build and run command ─────────────────────────────────────────
        name = safe_name(sub.text)
        outfile = str(out_path / f"{i:04d}_{name}{ext}")

        cmd = build_cmd(
            input_path=args.input,
            clip_start=clip_start,
            clip_dur=clip_dur,
            outfile=outfile,
            reencode=args.reencode,
            mute_lead=effective_mute_lead,
            mute_tail=effective_mute_tail,
            lead_s=actual_lead,
            cue_dur=cue_dur,
            tail_s=actual_tail,
            crf=args.crf,
            preset=args.preset,
        )

        if args.dry_run:
            log.info("DRY RUN: %s", " ".join(cmd))
            processed += 1
            continue

        log.debug("[%d/%d] %s", i, len(subs), Path(outfile).name)

        try:
            try:
                subprocess.run(cmd, check=True, timeout=CLIP_TIMEOUT)
            except Exception as e:
                if ext == ".wav" and getattr(args, "audio", False):
                    log.warning("Warning: Failed to produce .wav (%s). Falling back to .m4a...", e)
                    outfile = str(out_path / f"{i:04d}_{name}.m4a")
                    cmd = build_cmd(
                        input_path=args.input,
                        clip_start=clip_start,
                        clip_dur=clip_dur,
                        outfile=outfile,
                        reencode=args.reencode,
                        mute_lead=effective_mute_lead,
                        mute_tail=effective_mute_tail,
                        lead_s=actual_lead,
                        cue_dur=cue_dur,
                        tail_s=actual_tail,
                        crf=args.crf,
                        preset=args.preset,
                    )
                    subprocess.run(cmd, check=True, timeout=CLIP_TIMEOUT)
                else:
                    raise e
                    
            processed += 1

            write_clip_srt(
                outfile=outfile,
                cue_text=sub.text,
                cue_dur=cue_dur,
                lead_s=actual_lead,
            )

            manifest_rows.append(
                {
                    "index": i,
                    "file": Path(outfile).name,
                    "cue_start": fmt(raw_start),
                    "cue_end": fmt(raw_end),
                    "clip_start": fmt(clip_start),
                    "clip_end": fmt(clip_end),
                    "cue_dur_s": f"{cue_dur:.3f}",
                    "clip_dur_s": f"{clip_dur:.3f}",
                    "muted_lead_s": (
                        f"{actual_lead:.3f}" if effective_mute_lead else "0"
                    ),
                    "muted_tail_s": (
                        f"{actual_tail:.3f}" if effective_mute_tail else "0"
                    ),
                    "text": strip_html(sub.text).replace("\n", " "),
                }
            )
        except subprocess.TimeoutExpired:
            errors += 1
            log.error("TIMEOUT on clip %d (%s) after %ds", i, name, CLIP_TIMEOUT)
        except subprocess.CalledProcessError as e:
            errors += 1
            log.error("ERROR on clip %d (%s): %s", i, name, e)

    log.info("")
    log.info("─" * 50)
    log.info("  Processed : %d", processed)
    log.info("  Skipped   : %d  (below %.2fs)", skipped, args.min_dur)
    log.info("  Errors    : %d", errors)
    if not args.dry_run:
        log.info("  Output    : %s", out_path.resolve())
    log.info("─" * 50)

    return manifest_rows


def write_review_html(
    manifest_rows: list[dict], html_path: Path, audio_only: bool
) -> None:
    """Write an HTML page for visually reviewing all exported clips."""
    rows_html = ""
    for row in manifest_rows:
        fname = Path(row["file"]).name
        text = html.escape(row["text"])

        if audio_only:
            media_tag = (
                f'<audio controls preload="none">'
                f'<source src="{fname}"></audio>'
            )
        else:
            media_tag = (
                f'<video controls preload="none" width="480">'
                f'<source src="{fname}" type="video/mp4"></video>'
            )

        rows_html += f"""
        <div class="clip">
          <div class="index">#{row['index']}</div>
          {media_tag}
          <div class="meta">
            <p class="text">{text}</p>
            <p class="time">{row['cue_start']} → {row['cue_end']}
               (clip: {row['clip_dur_s']}s)</p>
          </div>
        </div>"""

    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Clip Review</title>
<style>
  body {{ font-family: system-ui; background: #1a1a2e; color: #eee;
         max-width: 1200px; margin: 0 auto; padding: 20px; }}
  .clip {{ display: flex; gap: 16px; align-items: center;
           padding: 12px; margin: 8px 0; background: #16213e;
           border-radius: 8px; }}
  .clip:hover {{ background: #1a1a4e; }}
  .index {{ font-size: 1.4em; font-weight: bold; min-width: 50px;
            text-align: center; color: #e94560; }}
  .text {{ font-size: 1.1em; margin: 0; }}
  .time {{ font-size: 0.85em; color: #888; margin: 4px 0 0; }}
  video, audio {{ border-radius: 6px; flex-shrink: 0; }}
  h1 {{ color: #e94560; }}
</style></head><body>
<h1>🎬 Clip Review ({len(manifest_rows)} clips)</h1>
{rows_html}
</body></html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    log.info("Review page -> %s", html_path)


def write_manifest(manifest_rows: list[dict], manifest_path: Path) -> None:
    """Write a CSV manifest of all exported clips."""
    if not manifest_rows:
        return
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=manifest_rows[0].keys(),
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(manifest_rows)
    log.info("Manifest -> %s", manifest_path)


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    check_dependencies()
    args = parse_args()

    configure_logging(verbose=args.verbose, quiet=args.quiet)

    # Note: the robust get_project_root() function is available here 
    # if you ever wish to expand this script to load a global config.json 
    # root_dir = get_project_root()
    # log.debug("Project Root: %s", root_dir)

    # ── validate ──────────────────────────────────────────────────────────
    if not os.path.isfile(args.input):
        sys.exit(f"ERROR: input file not found: {args.input}")
    if not os.path.isfile(args.srt):
        sys.exit(f"ERROR: SRT file not found: {args.srt}")

    audio_only = getattr(args, "audio", False) or is_audio_only(args.input)
    log.info(
        "Input  : %s  [%s]",
        args.input,
        "audio-only" if audio_only else "video+audio",
    )
    log.info("SRT    : %s", args.srt)

    mode_parts: list[str] = []
    if args.dry_run:
        mode_parts.append("dry-run")
    if args.reencode:
        mode_parts.append("re-encode")
    if args.mute_pad or args.mute_lead or args.mute_tail:
        mode_parts.append("mute-pad")
    if not args.reencode and not args.dry_run:
        mode_parts.append("stream-copy")
    if not args.no_probe and not audio_only and not args.reencode:
        mode_parts.append("keyframe-snapped")
    log.info("Mode   : %s", " + ".join(mode_parts))

    # ── one-time probes ───────────────────────────────────────────────────
    media_dur = probe_media_duration(args.input)

    all_keyframes: list[float] = []
    if not audio_only and not args.reencode and not args.no_probe:
        log.info("Probing keyframes (once for entire file)…")
        all_keyframes = probe_all_keyframes(args.input)
        if all_keyframes:
            log.info("  Found %d I-frames", len(all_keyframes))
        else:
            log.info("  No I-frames found; falling back to raw timestamps")

    # ── load SRT (filter first, then enforce gaps) ────────────────────────
    log.info("")
    subs = load_and_filter_subs(
        args.srt,
        args.gap,
        args.filter,
        encoding_override=args.encoding,
    )
    if not subs:
        sys.exit("No cues to process.")

    # ── output dir + manifest path ────────────────────────────────────────
    stem = Path(args.input).stem
    if args.out is not None:
        out_path = Path(args.out) / stem
    else:
        # Default: put clips next to the input file
        out_path = Path(args.input).resolve().parent / "clips" / stem

    if out_path.is_file():
        sys.exit(f"ERROR: Output path '{out_path}' is an existing file. Must be a directory.")
        
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Prefix outputs with the input filename stem to prevent overwriting
    # when processing multiple videos to the same output directory
    manifest_path = out_path / f"{stem}_manifest.csv"
    html_path = out_path / f"{stem}_review.html"
    meta_path = out_path / f"{stem}_metadata.json"

    # ── process ───────────────────────────────────────────────────────────
    manifest_rows = process_clips(args, subs, out_path, media_dur, all_keyframes)

    if not args.dry_run:
        write_manifest(manifest_rows, manifest_path)
        write_review_html(manifest_rows, html_path, audio_only=audio_only)

        # ── write metadata.json ───────────────────────────────────────────
        meta = vars(args).copy()
        # Ensure JSON serialisability (Path objects, etc.)
        for k, v in meta.items():
            if isinstance(v, Path):
                meta[k] = str(v)
                
        srt_meta = extract_srt_metadata(args.srt, args.encoding)
        if srt_meta:
            meta["subtitle_metadata"] = srt_meta
            
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4)
        log.info("Metadata -> %s", meta_path)


if __name__ == "__main__":
    main()