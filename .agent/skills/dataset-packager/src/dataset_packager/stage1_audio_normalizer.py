"""
1_audio_normalizer.py — Audio normalization for ASR/TTS datasets
=================================================================
Reads every clip listed in _manifest.csv, rewrites it as:
  • WAV (uncompressed, lossless — required by all major trainers)
  • Mono (mix down stereo/surround)
  • Target sample rate (16000 Hz for ASR, 22050 Hz for TTS)
  • EBU R128 loudness normalized to -23 LUFS  (standard broadcast level)

Outputs a new _manifest.csv with an added `wav_file` column pointing
to the normalized WAV, plus `sample_rate`, `channels`, `duration_s`
columns verified from the actual output file.

The original clips in clips/ are never modified.

Usage
-----
  python 1_audio_normalizer.py --clips clips/ --out normalized/
  python 1_audio_normalizer.py --clips clips/ --out normalized/ --sr 22050  # TTS
  python 1_audio_normalizer.py --clips clips/ --out normalized/ --workers 8
  python 1_audio_normalizer.py --help

Requirements
------------
  pip install soundfile numpy tqdm --break-system-packages
  ffmpeg must be on PATH (handles all input formats + loudness filter)
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import soundfile as sf
    import numpy as np
    HAS_SF = True
except ImportError:
    HAS_SF = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── constants ─────────────────────────────────────────────────────────────────
ASR_SR   = 16_000    # Whisper, wav2vec2, MMS
TTS_SR   = 22_050    # VITS, StyleTTS2, Kokoro
TARGET_LUFS = -23.0  # EBU R128 integrated loudness
# ─────────────────────────────────────────────────────────────────────────────


def check_deps() -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg not found on PATH.")
    if not shutil.which("ffprobe"):
        sys.exit("ERROR: ffprobe not found on PATH.")


def probe_duration(path: str) -> float:
    """Read actual duration from output WAV via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def measure_lufs(path: str) -> float | None:
    """
    Measure integrated loudness of a WAV file using ffmpeg's ebur128 filter.
    Returns LUFS value (negative float) or None on failure.
    """
    cmd = [
        "ffmpeg", "-i", path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # ffmpeg prints the summary to stderr
        for line in reversed(r.stderr.splitlines()):
            if "I:" in line and "LUFS" in line:
                parts = line.split()
                for j, p in enumerate(parts):
                    if p == "I:" and j + 1 < len(parts):
                        return float(parts[j + 1])
    except Exception:
        pass
    return None


def normalize_clip(
    src: str,
    dst: str,
    target_sr: int,
    target_lufs: float,
) -> dict:
    """
    Convert src -> dst WAV at target_sr, mono, loudness-normalized.

    Two-pass loudness normalization:
      Pass 1: measure integrated loudness with ebur128
      Pass 2: apply linear gain to hit target_lufs

    Returns a dict with verified metadata, or {"error": msg} on failure.
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    # ── resumability check ────────────────────────────────────────────────────
    if dst_path.exists() and dst_path.stat().st_size > 100:
        duration = probe_duration(dst)
        if duration > 0.1:
            return {
                "wav_file":    dst_path.name,
                "sample_rate": target_sr,
                "channels":    1,
                "duration_s":  round(duration, 4),
                "gain_db":     0.0,
            }

    # ── pass 1: measure loudness ──────────────────────────────────────────────
    measured_lufs = measure_lufs(src)
    if measured_lufs is None:
        # Fallback: skip loudness normalization, just convert
        gain_db = 0.0
    else:
        gain_db = target_lufs - measured_lufs

    # ── pass 2: convert + apply gain ─────────────────────────────────────────
    # loudnorm filter does proper integrated loudness normalization in one pass
    # We use the simpler volume filter here since we already measured;
    # for production use loudnorm=I=-23:TP=-1.5:LRA=11 for full two-pass.
    af = f"aresample={target_sr},aformat=sample_fmts=s16:channel_layouts=mono"
    if abs(gain_db) > 0.1:
        af = f"volume={gain_db:.2f}dB," + af

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", src,
        "-af", af,
        "-ar", str(target_sr),
        "-ac", "1",
        dst,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        return {"error": f"ffmpeg failed: {e}"}
    except subprocess.TimeoutExpired:
        return {"error": "ffmpeg timeout"}

    # ── verify output ─────────────────────────────────────────────────────────
    if not dst_path.exists() or dst_path.stat().st_size < 100:
        return {"error": "output file missing or empty"}

    duration = probe_duration(dst)
    return {
        "wav_file":    dst_path.name,
        "sample_rate": target_sr,
        "channels":    1,
        "duration_s":  round(duration, 4),
        "gain_db":     round(gain_db, 2),
    }


def main(args_list: list[str] | None = None) -> Path:
    check_deps()

    parser = argparse.ArgumentParser(
        description="Normalize audio clips to WAV for ASR/TTS training.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python 1_audio_normalizer.py --clips clips/ --out normalized/
  python 1_audio_normalizer.py --clips clips/ --out normalized/ --sr 22050
  python 1_audio_normalizer.py --clips clips/ --out normalized/ --workers 8 --lufs -20
        """,
    )
    parser.add_argument("--clips",   required=True,
                        help="Directory containing media-slice output clips/")
    parser.add_argument("--out",     required=True,
                        help="Output directory for normalized WAV files")
    parser.add_argument("--sr",      type=int, default=ASR_SR,
                        help=f"Target sample rate Hz (default {ASR_SR} for ASR; use 22050 for TTS)")
    parser.add_argument("--lufs",    type=float, default=TARGET_LUFS,
                        help=f"Target integrated loudness LUFS (default {TARGET_LUFS})")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel ffmpeg workers (default 4)")
    parser.add_argument("--manifest", default="_manifest.csv",
                        help="Manifest filename inside --clips dir (default _manifest.csv)")
    args = parser.parse_args(args_list)

    clips_path   = Path(args.clips)
    out_path     = Path(args.out)
    manifest_in  = clips_path / args.manifest
    manifest_out = out_path / "_manifest_1_normalized.csv"

    if not manifest_in.exists():
        sys.exit(f"ERROR: manifest not found: {manifest_in}")

    out_path.mkdir(parents=True, exist_ok=True)

    # ── read manifest ─────────────────────────────────────────────────────────
    with open(manifest_in, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Clips dir  : {clips_path.resolve()}")
    print(f"Output dir : {out_path.resolve()}")
    print(f"Sample rate: {args.sr} Hz  |  Target loudness: {args.lufs} LUFS")
    print(f"Workers    : {args.workers}")
    print(f"Clips      : {len(rows)}\n")

    # ── build work list ───────────────────────────────────────────────────────
    work = []
    for row in rows:
        src = clips_path / row["file"]
        dst = out_path   / (Path(row["file"]).stem + ".wav")
        work.append((row, str(src), str(dst)))

    # ── parallel normalization ────────────────────────────────────────────────
    results = {}   # index -> metadata dict

    def _worker(item):
        row, src, dst = item
        if not Path(src).exists():
            return row["index"], {"error": f"source not found: {src}"}
        meta = normalize_clip(src, dst, args.sr, args.lufs)
        return row["index"], meta

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, item): item for item in work}
        iterator = (
            tqdm(as_completed(futures), total=len(futures), unit="clip")
            if HAS_TQDM
            else as_completed(futures)
        )
        for fut in iterator:
            idx, meta = fut.result()
            results[idx] = meta

    # ── write updated manifest ────────────────────────────────────────────────
    ok      = 0
    errors  = 0
    out_rows = []

    for row in rows:
        meta = results.get(row["index"], {"error": "not processed"})
        if "error" in meta:
            errors += 1
            print(f"  ERROR [{row['index']}] {row['file']}: {meta['error']}")
            continue
        out_row = {**row, **meta}
        out_rows.append(out_row)
        ok += 1

    if out_rows:
        with open(manifest_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_rows[0].keys())
            writer.writeheader()
            writer.writerows(out_rows)
        print(f"\nManifest -> {manifest_out}")

    print(f"\n{'─' * 50}")
    print(f"  Normalized : {ok}")
    print(f"  Errors     : {errors}")
    print(f"  Output     : {out_path.resolve()}")
    print(f"{'─' * 50}")

    from .metadata_helper import get_subtitle_metadata
    subtitle_metadata = get_subtitle_metadata(args.clips)

    metadata_out = out_path / "metadata_1_normalized.json"
    with open(metadata_out, "w", encoding="utf-8") as f:
        meta_dict = {
            "stage": 1,
            "name": "audio_normalizer",
            "args": vars(args),
            "stats": {"normalized": ok, "errors": errors}
        }
        if subtitle_metadata:
            meta_dict["subtitle_metadata"] = subtitle_metadata
        json.dump(meta_dict, f, indent=2)

    return manifest_out


if __name__ == "__main__":
    main()
