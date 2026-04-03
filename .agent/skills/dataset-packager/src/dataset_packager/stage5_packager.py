"""
5_packager.py — Package into HuggingFace datasets format (Parquet)
===================================================================
Reads the train/val/test CSV splits and produces a HuggingFace-compatible
dataset on disk (and optionally pushes to the Hub).

Output structure
----------------
  packaged/
    train/
      data-00000-of-00004.parquet
      data-00001-of-00004.parquet
      ...
    validation/
      data-00000-of-00001.parquet
    test/
      data-00000-of-00001.parquet
    dataset_info.json
    README.md              (dataset card)

Parquet schema — ASR
---------------------
  audio          : {"bytes": <wav_bytes>, "path": "0001_hello.wav"}
  text           : str   (cleaned ASR transcript, lowercase)
  duration_s     : float
  snr_db         : float (from quality_filter; may be empty)
  wps            : float (words per second)
  original_text  : str   (raw subtitle text before cleaning)

Parquet schema — TTS (--tts)
------------------------------
  audio          : {"bytes": <wav_bytes>, "path": "..."}
  text           : str   (TTS-normalized transcript with punctuation)
  duration_s     : float
  speaker_id     : str   (if --speaker-col provided)
  snr_db         : float
  wps            : float
  original_text  : str

Why Parquet?
------------
  • Audio bytes are embedded — dataset is fully self-contained
  • Columnar format enables partial reads (load text column only)
  • Native support in HuggingFace datasets.load_dataset()
  • Efficient compression of repeated schema overhead
  • Sharding keeps individual files under ~500 MB

Usage
-----
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/ --tts
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/ \\
      --push-to-hub yourname/my-asr-dataset --token hf_xxx
  python 5_packager.py --help

Requirements
------------
  pip install datasets pyarrow soundfile numpy tqdm --break-system-packages
  Optional for Hub upload: pip install huggingface_hub
"""

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_ARROW = True
except ImportError:
    HAS_ARROW = False

try:
    import soundfile as sf
    import numpy as np
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── constants ─────────────────────────────────────────────────────────────────
SHARD_SIZE   = 500      # number of samples per Parquet shard
MAX_WAV_BYTES = 50 * 1024 * 1024   # skip clips > 50 MB (safety guard)
# ─────────────────────────────────────────────────────────────────────────────


def check_deps() -> None:
    missing = []
    if not HAS_ARROW:
        missing.append("pyarrow")
    if not HAS_AUDIO:
        missing.append("soundfile numpy")
    if missing:
        sys.exit(
            f"ERROR: missing dependencies: {', '.join(missing)}\n"
            f"       pip install {' '.join(missing)} --break-system-packages"
        )


def read_wav_bytes(wav_path: str) -> bytes | None:
    """Read a WAV file and return its raw bytes, or None on error."""
    p = Path(wav_path)
    if not p.exists():
        return None
    if p.stat().st_size > MAX_WAV_BYTES:
        return None
    return p.read_bytes()


def build_asr_schema() -> "pa.Schema":
    return pa.schema([
        pa.field("audio",         pa.struct([
            pa.field("bytes", pa.binary()),
            pa.field("path",  pa.string()),
        ])),
        pa.field("text",          pa.string()),
        pa.field("duration_s",    pa.float32()),
        pa.field("snr_db",        pa.float32()),
        pa.field("wps",           pa.float32()),
        pa.field("original_text", pa.string()),
        pa.field("split",         pa.string()),
    ])


def build_tts_schema(has_speaker: bool) -> "pa.Schema":
    fields = [
        pa.field("audio",         pa.struct([
            pa.field("bytes", pa.binary()),
            pa.field("path",  pa.string()),
        ])),
        pa.field("text",          pa.string()),
        pa.field("duration_s",    pa.float32()),
        pa.field("snr_db",        pa.float32()),
        pa.field("wps",           pa.float32()),
        pa.field("original_text", pa.string()),
        pa.field("split",         pa.string()),
    ]
    if has_speaker:
        fields.insert(2, pa.field("speaker_id", pa.string()))
    return pa.schema(fields)


def rows_to_pyarrow(
    rows: list[dict],
    wav_dir: Path,
    split_name: str,
    tts: bool,
    text_col: str,
    speaker_col: str | None,
    schema: "pa.Schema",
) -> "pa.Table":
    """Convert a list of manifest rows to a PyArrow Table."""
    audio_bytes_col = []
    audio_path_col  = []
    text_col_data   = []
    duration_col    = []
    snr_col         = []
    wps_col         = []
    original_col    = []
    split_col       = []
    speaker_col_data = [] if speaker_col else None

    skipped = 0

    for row in rows:
        wav_path  = wav_dir / row["audio"]
        wav_bytes = read_wav_bytes(str(wav_path))

        if wav_bytes is None:
            skipped += 1
            continue

        text = row.get(text_col, row.get("text", "")).strip()
        if not text:
            skipped += 1
            continue

        audio_bytes_col.append(wav_bytes)
        audio_path_col.append(row["audio"])
        text_col_data.append(text)
        duration_col.append(float(row.get("duration_s", 0) or 0))
        # Preserve None for missing SNR/WPS so they're distinguishable from 0
        raw_snr = row.get("snr_db", "")
        snr_col.append(float(raw_snr) if raw_snr else None)
        raw_wps = row.get("wps", "")
        wps_col.append(float(raw_wps) if raw_wps else None)
        original_col.append(row.get("text", ""))
        split_col.append(split_name)

        if speaker_col_data is not None:
            speaker_col_data.append(row.get(speaker_col or "", ""))

    if skipped:
        print(f"  Skipped {skipped} rows (missing WAV or empty text)")

    # Build struct array for audio column
    audio_struct = pa.StructArray.from_arrays(
        [pa.array(audio_bytes_col, type=pa.binary()),
         pa.array(audio_path_col,  type=pa.string())],
        names=["bytes", "path"],
    )

    arrays = [
        audio_struct,
        pa.array(text_col_data,  type=pa.string()),
        pa.array(duration_col,   type=pa.float32()),
        pa.array(snr_col,        type=pa.float32()),
        pa.array(wps_col,        type=pa.float32()),
        pa.array(original_col,   type=pa.string()),
        pa.array(split_col,      type=pa.string()),
    ]

    if speaker_col_data is not None:
        # Insert speaker_id after text (index 2)
        arrays.insert(2, pa.array(speaker_col_data, type=pa.string()))

    return pa.table(
        {field.name: arr for field, arr in zip(schema, arrays)},
        schema=schema,
    )


def write_shards(
    table: "pa.Table",
    out_dir: Path,
    shard_size: int,
    split_name: str,
) -> list[str]:
    """Write a PyArrow Table as sharded Parquet files. Returns list of paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n      = len(table)
    shards = max(1, (n + shard_size - 1) // shard_size)
    paths  = []

    for shard_idx in range(shards):
        start = shard_idx * shard_size
        end   = min(start + shard_size, n)
        shard = table.slice(start, end - start)
        fname = f"data-{shard_idx:05d}-of-{shards:05d}.parquet"
        fpath = out_dir / fname
        pq.write_table(
            shard, fpath,
            compression="snappy",    # fast decompression, good ratio
            write_statistics=True,
        )
        paths.append(str(fpath))
        print(f"    {fname}  ({end - start} samples, {fpath.stat().st_size / 1e6:.1f} MB)")

    return paths


def build_dataset_card(
    name: str,
    tts: bool,
    splits: dict[str, int],
    total_hours: float,
    text_col: str,
    lang: str = "en",
) -> str:
    task = "text-to-speech" if tts else "automatic-speech-recognition"
    return f"""---
language:
- {lang}
license: other
task_categories:
- {task}
tags:
- speech
- audio
- {'tts' if tts else 'asr'}
---

# {name}

{'TTS' if tts else 'ASR'} dataset packaged from SRT-aligned audio clips.

## Dataset info

| Split      | Samples |
|------------|---------|
| train      | {splits.get('train', 0):,} |
| validation | {splits.get('validation', 0):,} |
| test       | {splits.get('test', 0):,} |

Total audio: **{total_hours:.1f} hours**

## Usage

```python
from datasets import load_dataset, Audio

ds = load_dataset("{name}")
ds = ds.cast_column("audio", Audio(sampling_rate={22050 if tts else 16000}))

# Access a sample
sample = ds["train"][0]
print(sample["text"])        # transcript
print(sample["audio"])       # {{"array": ..., "sampling_rate": ...}}
```

## Columns

- `audio` — raw WAV bytes embedded in Parquet
- `text` — {'TTS-normalized transcript (punctuation preserved)' if tts else 'ASR-normalized transcript (lowercase, no punctuation)'}
- `duration_s` — clip duration in seconds
- `snr_db` — estimated signal-to-noise ratio
- `wps` — words per second (speaking rate)
- `original_text` — raw subtitle text before normalization
"""


def push_to_hub(packaged_dir: Path, repo_id: str, token: str) -> None:
    try:
        from datasets import load_dataset
        from huggingface_hub import HfApi
    except ImportError:
        sys.exit(
            "ERROR: huggingface_hub / datasets not installed.\n"
            "       pip install datasets huggingface_hub --break-system-packages"
        )

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
    api.upload_folder(
        folder_path=str(packaged_dir),
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )
    print(f"\nPushed to Hub: https://huggingface.co/datasets/{repo_id}")


def main(args_list: list[str] | None = None) -> Path:
    check_deps()

    parser = argparse.ArgumentParser(
        description="Package ASR/TTS splits into HuggingFace Parquet dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/ --tts
  python 5_packager.py --splits normalized/splits/ --wav-dir normalized/ --out packaged/ \\
      --push-to-hub yourname/my-dataset --token hf_xxx
        """,
    )
    parser.add_argument("--splits",       required=True,
                        help="Directory containing train.csv / val.csv / test.csv")
    parser.add_argument("--wav-dir",      required=True,
                        help="Directory containing normalized WAV files")
    parser.add_argument("--out",          required=True,
                        help="Output directory for packaged dataset")
    parser.add_argument("--tts",          action="store_true",
                        help="TTS mode: use text_tts column and 22050 Hz schema")
    parser.add_argument("--speaker-col",  default=None,
                        help="Manifest column for speaker ID (adds speaker_id to schema)")
    parser.add_argument("--shard-size",   type=int, default=SHARD_SIZE,
                        help=f"Samples per Parquet shard (default {SHARD_SIZE})")
    parser.add_argument("--dataset-name", default="my-asr-dataset",
                        help="Dataset name for the README card")
    parser.add_argument("--push-to-hub",  default=None,
                        help="HuggingFace Hub repo ID to push to (e.g. yourname/dataset)")
    parser.add_argument("--token",        default=None,
                        help="HuggingFace API token (required for --push-to-hub)")
    parser.add_argument("--lang",         default="en",
                        help="Language code for the README card (default: en)")
    args = parser.parse_args(args_list)

    splits_dir = Path(args.splits)
    wav_dir    = Path(args.wav_dir)
    out_dir    = Path(args.out)
    text_col   = "text"
    mode       = "TTS" if args.tts else "ASR"

    print(f"Mode       : {mode}")
    print(f"Text col   : {text_col}")
    print(f"WAV dir    : {wav_dir.resolve()}")
    print(f"Output     : {out_dir.resolve()}")
    print()

    # ── build schema ──────────────────────────────────────────────────────────
    has_speaker = args.speaker_col is not None
    schema = (build_tts_schema(has_speaker) if args.tts
              else build_asr_schema())

    # ── process each split ────────────────────────────────────────────────────
    split_files = {
        "train":      splits_dir / "train.csv",
        "validation": splits_dir / "val.csv",
        "test":       splits_dir / "test.csv",
    }

    split_counts    = {}
    total_duration  = 0.0

    for split_name, csv_path in split_files.items():
        if not csv_path.exists():
            print(f"  SKIP {split_name} (no {csv_path.name})")
            continue

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            print(f"  SKIP {split_name} (empty)")
            continue

        # Check text column exists (rows is guaranteed non-empty here)
        if text_col not in rows[0]:
            available = [k for k in rows[0] if "text" in k.lower()]
            sys.exit(
                f"ERROR: column '{text_col}' not in {csv_path.name}.\n"
                f"       Available text columns: {available}\n"
                f"       Run 2_text_cleaner.py first."
            )

        print(f"  Processing {split_name}: {len(rows)} samples")
        n = len(rows)
        shard_size = args.shard_size
        shards = max(1, (n + shard_size - 1) // shard_size)
        shard_dir = out_dir / split_name
        shard_dir.mkdir(parents=True, exist_ok=True)
        
        split_valid_count = 0
        for shard_idx in range(shards):
            start = shard_idx * shard_size
            end   = min(start + shard_size, n)
            shard_rows = rows[start:end]
            
            table = rows_to_pyarrow(
                shard_rows, wav_dir, split_name, args.tts,
                text_col, args.speaker_col, schema,
            )
            
            split_valid_count += len(table)
            
            if len(table) > 0:
                fname = f"data-{shard_idx:05d}-of-{shards:05d}.parquet"
                fpath = shard_dir / fname
                pq.write_table(
                    table, fpath,
                    compression="snappy",    # fast decompression, good ratio
                    write_statistics=True,
                )
                print(f"    {fname}  ({len(table)} samples, {fpath.stat().st_size / 1e6:.1f} MB)")

        split_counts[split_name] = split_valid_count
        total_duration += sum(
            float(r.get("duration_s", 0) or 0) for r in rows
        )
        print()

    total_hours = total_duration / 3600

    if not split_counts:
        print("\nWARNING: No splits were processed — all CSVs were missing or empty.")
        print("         Check that stage 4 produced output in:", splits_dir.resolve())

    # ── dataset_info.json ─────────────────────────────────────────────────────
    dataset_info = {
        "dataset_name": args.dataset_name,
        "mode":         mode,
        "splits":       split_counts,
        "total_samples": sum(split_counts.values()),
        "total_hours":  round(total_hours, 2),
        "sample_rate":  22050 if args.tts else 16000,
        "schema":       [f.name for f in schema],
    }
    with open(out_dir / "dataset_info.json", "w", encoding="utf-8") as f:
        json.dump(dataset_info, f, indent=2)

    # ── README.md dataset card ────────────────────────────────────────────────
    readme = build_dataset_card(
        args.dataset_name, args.tts, split_counts, total_hours, text_col, lang=args.lang
    )
    with open(out_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"{'─' * 50}")
    print(f"  Total samples : {sum(split_counts.values()):,}")
    print(f"  Total audio   : {total_hours:.2f} hours")
    for split, count in split_counts.items():
        print(f"  {split:<12}  : {count:,} samples")
    print(f"  Output        : {out_dir.resolve()}")
    print(f"{'─' * 50}")

    # ── push to Hub ───────────────────────────────────────────────────────────
    if args.push_to_hub:
        if not args.token:
            sys.exit("ERROR: --token required when using --push-to-hub")
        print(f"\nPushing to HuggingFace Hub: {args.push_to_hub}")
        push_to_hub(out_dir, args.push_to_hub, args.token)

    # ── print usage snippet ───────────────────────────────────────────────────
    sr = 22050 if args.tts else 16000
    print(f"\nLoad your dataset:")
    print(f"\n  from datasets import load_dataset, Audio")
    print(f"\n  ds = load_dataset('{out_dir.resolve().as_posix()}')")
    print(f"  ds = ds.cast_column('audio', Audio(sampling_rate={sr}))")
    print(f"\n  sample = ds['train'][0]")
    print(f"  print(sample['text'])")

    metadata_out = out_dir / "metadata_5_packaged.json"
    with open(metadata_out, "w", encoding="utf-8") as f:
        json.dump({
            "stage": 5,
            "name": "packager",
            "args": vars(args),
            "stats": dataset_info
        }, f, indent=2)

    return out_dir


if __name__ == "__main__":
    main()
