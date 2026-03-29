"""
3_quality_filter.py — Acoustic and text quality gating
=======================================================
Filters the manifest to keep only samples that meet minimum quality
thresholds for ASR/TTS training:

  Duration gate
  -------------
  • ASR: 1.0 – 15.0 s  (Whisper's sweet spot; CTC models max ~30 s)
  • TTS: 1.0 – 12.0 s  (longer clips cause TTS attention collapse)

  SNR gate  (Signal-to-Noise Ratio)
  ------------------------------------
  Estimates SNR using the RMS energy difference between the loudest
  100 ms window (assumed speech) and the quietest 100 ms window
  (assumed noise floor).  Fast, no ML required.
  Threshold: > 20 dB (background noise would dominate below this)

  CER gate  (Character Error Rate)  — optional, slow
  ----------------------------------------------------
  Runs a lightweight Whisper-tiny pass on each clip and computes CER
  against the cleaned transcript.  Rejects clips where CER > 0.15
  (text and audio don't match).
  Enable with --cer.  Requires: pip install openai-whisper

  Speaking rate gate
  ------------------
  Computes words-per-second from text length and audio duration.
  Rejects clips < 0.5 WPS (too slow / long pauses) or > 5.0 WPS
  (too fast to be natural speech).

All thresholds are configurable via CLI flags.

Usage
-----
  python 3_quality_filter.py --manifest normalized/_manifest.csv
  python 3_quality_filter.py --manifest normalized/_manifest.csv --tts
  python 3_quality_filter.py --manifest normalized/_manifest.csv --cer
  python 3_quality_filter.py --manifest normalized/_manifest.csv \\
      --min-dur 1.0 --max-dur 10.0 --min-snr 25
  python 3_quality_filter.py --help

Requirements
------------
  pip install soundfile numpy tqdm --break-system-packages
  Optional for --cer: pip install openai-whisper
"""

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    import numpy as np
    import soundfile as sf
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False
    print("WARNING: soundfile/numpy not installed. SNR/speaking-rate checks disabled.")
    print("         pip install soundfile numpy --break-system-packages")

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── defaults ──────────────────────────────────────────────────────────────────
ASR_MIN_DUR   =  1.0    # seconds
ASR_MAX_DUR   = 15.0
TTS_MIN_DUR   =  1.0
TTS_MAX_DUR   = 12.0
MIN_SNR_DB    = 20.0    # dB — below this the clip is too noisy
MAX_CER       =  0.15   # 15% character error rate
MIN_WPS       =  0.5    # words per second
MAX_WPS       =  5.0
SNR_FRAME_S   =  0.1    # frame size for SNR estimation (100 ms)
# ─────────────────────────────────────────────────────────────────────────────


def estimate_snr(audio: "np.ndarray", sr: int,
                 frame_s: float = SNR_FRAME_S) -> float:
    """
    Fast SNR estimate using percentile energy method:
      signal level  = 95th percentile of frame RMS values
      noise floor   = 5th percentile of frame RMS values
      SNR           = 20 * log10(signal / noise)

    This avoids needing a Voice Activity Detector for a rough pass.
    Returns float dB, or 0.0 if audio is too short.
    """
    frame_len = int(frame_s * sr)
    if len(audio) < frame_len * 2:
        return 0.0

    frames = [
        audio[i: i + frame_len]
        for i in range(0, len(audio) - frame_len, frame_len)
    ]
    rms_values = np.array([np.sqrt(np.mean(f ** 2)) for f in frames])
    rms_values = rms_values[rms_values > 0]  # remove silent frames

    if len(rms_values) < 2:
        return 0.0

    signal_rms = np.percentile(rms_values, 95)
    noise_rms  = np.percentile(rms_values, 5)

    if noise_rms < 1e-10:
        return 60.0   # effectively silent noise floor = excellent SNR

    return float(20 * np.log10(signal_rms / noise_rms))


def compute_wps(text: str, duration_s: float) -> float:
    """Words per second from whitespace-split word count."""
    if duration_s <= 0:
        return 0.0
    words = len(text.split())
    return words / duration_s


def compute_cer(hypothesis: str, reference: str) -> float:
    """
    Character Error Rate using dynamic programming edit distance.
    CER = edit_distance(hyp, ref) / len(ref)
    """
    ref = reference.lower().replace(" ", "")
    hyp = hypothesis.lower().replace(" ", "")

    if not ref:
        return 0.0 if not hyp else 1.0

    n, m = len(ref), len(hyp)
    # O(n*m) DP — fine for short strings (<200 chars)
    dp = list(range(n + 1))
    for j in range(1, m + 1):
        prev = dp[0]
        dp[0] = j
        for i in range(1, n + 1):
            temp = dp[i]
            if hyp[j - 1] == ref[i - 1]:
                dp[i] = prev
            else:
                dp[i] = 1 + min(prev, dp[i], dp[i - 1])
            prev = temp
    return dp[n] / n


def load_whisper_model(device: str | None = None):
    """Load whisper-tiny for CER checking. Cached after first call."""
    try:
        import whisper
        import torch
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading whisper-tiny for CER verification on {device}…")
        return whisper.load_model("tiny", device=device)
    except ImportError:
        sys.exit(
            "ERROR: openai-whisper or torch not installed.\n"
            "       pip install openai-whisper torch\n"
            "       Or run without --cer flag."
        )


def transcribe(model, wav_path: str) -> str:
    """Transcribe a WAV file with whisper-tiny. Returns cleaned text."""
    result = model.transcribe(wav_path, language=None, fp16=False)
    return result.get("text", "").strip().lower()


def main(args_list: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(
        description="Quality-filter ASR/TTS dataset by duration, SNR, speaking rate, and CER.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python 3_quality_filter.py --manifest normalized/_manifest.csv
  python 3_quality_filter.py --manifest normalized/_manifest.csv --tts
  python 3_quality_filter.py --manifest normalized/_manifest.csv --cer
  python 3_quality_filter.py --manifest normalized/_manifest.csv --min-snr 25 --max-dur 10
        """,
    )
    parser.add_argument("--manifest",  required=True,
                        help="Path to _manifest.csv (after text_cleaner)")
    parser.add_argument("--text-col",  default=None,
                        help="Column name for transcript (defaults to text_tts if --tts, else text_asr)")
    parser.add_argument("--tts",       action="store_true",
                        help="Use TTS duration limits (1–12 s) instead of ASR (1–15 s)")
    parser.add_argument("--cer",       action="store_true",
                        help="Enable CER verification via whisper-tiny (slow but thorough)")
    parser.add_argument("--min-dur",   type=float, default=None,
                        help="Override minimum clip duration in seconds")
    parser.add_argument("--max-dur",   type=float, default=None,
                        help="Override maximum clip duration in seconds")
    parser.add_argument("--min-snr",   type=float, default=MIN_SNR_DB,
                        help=f"Minimum SNR in dB (default {MIN_SNR_DB})")
    parser.add_argument("--max-cer",   type=float, default=MAX_CER,
                        help=f"Maximum CER (default {MAX_CER})")
    parser.add_argument("--min-wps",   type=float, default=MIN_WPS,
                        help=f"Minimum words/second (default {MIN_WPS})")
    parser.add_argument("--max-wps",   type=float, default=MAX_WPS,
                        help=f"Maximum words/second (default {MAX_WPS})")
    parser.add_argument("--filter-audio", action="store_true",
                        help="Enable audio quality filtering (default: do not filter)")
    parser.add_argument("--wav-dir",   default=None,
                        help="Directory containing WAV files (default: same dir as manifest)")
    parser.add_argument("--device",    default=None,
                        help="Device to run whisper model on (e.g., 'cuda', 'cpu'). Defaults to auto-detect.")
    args = parser.parse_args(args_list)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        sys.exit(f"ERROR: manifest not found: {manifest_path}")

    wav_dir  = Path(args.wav_dir) if args.wav_dir else manifest_path.parent
    mode     = "TTS" if args.tts else "ASR"
    text_col = args.text_col or ("text_tts" if args.tts else "text_asr")

    min_dur = args.min_dur if args.min_dur is not None else (TTS_MIN_DUR if args.tts else ASR_MIN_DUR)
    max_dur = args.max_dur if args.max_dur is not None else (TTS_MAX_DUR if args.tts else ASR_MAX_DUR)

    print(f"Mode       : {mode}")
    print(f"Text col   : {text_col}")
    print(f"Duration   : {min_dur} – {max_dur} s")
    print(f"Min SNR    : {args.min_snr} dB")
    print(f"WPS range  : {args.min_wps} – {args.max_wps}")
    print(f"CER check  : {'yes (whisper-tiny)' if args.cer else 'no'}")
    if args.cer:
        print(f"Device     : {args.device or 'auto'}")
    print()

    whisper_model = load_whisper_model(args.device) if args.cer else None

    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit("ERROR: manifest is empty (no rows).")

    if text_col not in rows[0]:
        sys.exit(
            f"ERROR: column '{text_col}' not found in manifest.\n"
            f"       Run 2_text_cleaner.py first, or specify --text-col."
        )

    print(f"Input      : {len(rows)} rows\n")

    kept    = []
    reasons = {}   # reason -> count

    def reject(reason: str):
        reasons[reason] = reasons.get(reason, 0) + 1

    iterator = tqdm(rows, unit="clip") if HAS_TQDM else rows

    for row in iterator:
        wav_file = wav_dir / row["wav_file"]
        text     = row.get(text_col, "").strip()
        dur      = float(row.get("duration_s", 0))

        # ── 1. duration gate ──────────────────────────────────────────────────
        if args.filter_audio and not (min_dur <= dur <= max_dur):
            reject("duration")
            continue

        # ── 2. text present ───────────────────────────────────────────────────
        if not text:
            reject("empty_text")
            continue

        # ── 3. speaking rate ──────────────────────────────────────────────────
        wps = compute_wps(text, dur)
        if args.filter_audio and not (args.min_wps <= wps <= args.max_wps):
            reject("speaking_rate")
            continue

        # ── 4. SNR gate ───────────────────────────────────────────────────────
        out_row = {**row}
        if HAS_AUDIO and wav_file.exists():
            try:
                audio, sr = sf.read(str(wav_file), dtype="float32", always_2d=False)
                snr = estimate_snr(audio, sr)
                if args.filter_audio and snr < args.min_snr:
                    reject("snr")
                    continue
                out_row["snr_db"] = f"{snr:.1f}"
                out_row["wps"]    = f"{wps:.2f}"
            except Exception:
                if args.filter_audio:
                    reject("audio_read_error")
                    continue
                else:
                    out_row["snr_db"] = ""
                    out_row["wps"]    = f"{wps:.2f}"
        else:
            out_row["snr_db"] = ""
            out_row["wps"]    = f"{wps:.2f}"

        # ── 5. CER gate (optional, slow) ──────────────────────────────────────
        if args.cer and whisper_model and wav_file.exists():
            hypothesis = transcribe(whisper_model, str(wav_file))
            cer        = compute_cer(hypothesis, text)
            out_row["cer"] = f"{cer:.3f}"
            if args.filter_audio and cer > args.max_cer:
                reject("cer")
                continue
        else:
            out_row["cer"] = ""

        kept.append(out_row)

    # ── write filtered manifest ───────────────────────────────────────────────
    manifest_out = manifest_path.parent / "_manifest_3_filtered.csv"
    if kept:
        with open(manifest_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=kept[0].keys())
            writer.writeheader()
            writer.writerows(kept)

    total_dropped = len(rows) - len(kept)
    print(f"\n{'─' * 50}")
    print(f"  Input    : {len(rows)}")
    print(f"  Kept     : {len(kept)}")
    print(f"  Dropped  : {total_dropped}")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<20}: {count}")
    print(f"  Manifest : {manifest_out.name}  (written)")
    print(f"{'─' * 50}")

    metadata_out = manifest_path.parent / "metadata_3_filtered.json"
    with open(metadata_out, "w", encoding="utf-8") as f:
        json.dump({
            "stage": 3,
            "name": "quality_filter",
            "args": vars(args),
            "stats": {"kept": len(kept), "dropped": total_dropped, "reasons": reasons, "audio_checks_skipped": not HAS_AUDIO}
        }, f, indent=2)

    return manifest_out


if __name__ == "__main__":
    main()
