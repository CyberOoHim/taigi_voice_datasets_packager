"""
4_split_dataset.py — Train / validation / test split
======================================================
Splits the filtered manifest into train, val, and test sets.

Splitting strategy
------------------
  Default (random):
    Randomly shuffles all samples and splits by ratio.

  Speaker-aware (--speaker-col):
    If the manifest has a speaker_id column (added externally, e.g. from
    a diarization pass), splits by speaker — all clips from a given speaker
    go to the SAME split.  This prevents the model from memorising a
    speaker's voice and inflating validation scores.
    Critical for TTS voice cloning datasets.

  Stratified by duration (--stratify-dur):
    Bins samples into short / medium / long buckets and ensures each
    split has a representative mix of all durations.

Outputs
-------
  normalized/
    splits/
      train.csv      — full manifest rows for training
      val.csv        — full manifest rows for validation
      test.csv        — full manifest rows for test
      split_info.json — counts, ratios, seed

Usage
-----
  python 4_split_dataset.py --manifest normalized/_manifest.csv
  python 4_split_dataset.py --manifest normalized/_manifest.csv \\
      --train 0.90 --val 0.05 --test 0.05
  python 4_split_dataset.py --manifest normalized/_manifest.csv \\
      --speaker-col speaker_id
  python 4_split_dataset.py --help
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

# ── defaults ──────────────────────────────────────────────────────────────────
DEFAULT_TRAIN = 0.90
DEFAULT_VAL   = 0.05
DEFAULT_TEST  = 0.05
DEFAULT_SEED  = 42
# ─────────────────────────────────────────────────────────────────────────────


def write_split(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def split_random(rows: list[dict], train_r: float, val_r: float,
                 seed: int) -> tuple[list, list, list]:
    rng = random.Random(seed)
    shuffled = rows[:]
    rng.shuffle(shuffled)
    n      = len(shuffled)
    n_val  = int(n * val_r)
    n_test = int(n * (1 - train_r - val_r))
    n_train = n - n_val - n_test
    return (
        shuffled[:n_train],
        shuffled[n_train: n_train + n_val],
        shuffled[n_train + n_val:],
    )


def split_by_speaker(rows: list[dict], speaker_col: str,
                     train_r: float, val_r: float,
                     seed: int) -> tuple[list, list, list]:
    """
    Group clips by speaker, then assign whole speakers to splits.
    Speakers are sorted by clip count (descending) then greedily assigned
    to maintain roughly correct ratios.
    """
    by_speaker: dict[str, list] = defaultdict(list)
    for row in rows:
        spk = row.get(speaker_col, "unknown")
        by_speaker[spk].append(row)

    # Sort speakers by clip count (descending) for greedy packing:
    # assign the largest speakers first to keep splits balanced.
    speakers = list(by_speaker.keys())
    speakers.sort() # deterministic tie-breaking
    rng = random.Random(seed)
    rng.shuffle(speakers)
    speakers.sort(key=lambda s: -len(by_speaker[s]))

    n_total     = len(rows)
    target_val  = int(n_total * val_r)
    target_test = int(n_total * (1 - train_r - val_r))

    train_clips, val_clips, test_clips = [], [], []
    val_count, test_count = 0, 0

    for spk in speakers:
        clips = by_speaker[spk]
        if val_count < target_val:
            val_clips.extend(clips)
            val_count += len(clips)
        elif test_count < target_test:
            test_clips.extend(clips)
            test_count += len(clips)
        else:
            train_clips.extend(clips)

    return train_clips, val_clips, test_clips


def split_stratified_dur(rows: list[dict], train_r: float, val_r: float,
                         seed: int) -> tuple[list, list, list]:
    """
    Bin by duration into short (<3s), medium (3-8s), long (>8s) and
    split each bin independently, then merge.
    """
    bins: dict[str, list] = {"short": [], "medium": [], "long": []}
    for row in rows:
        dur = float(row.get("duration_s", 0))
        if dur < 3.0:
            bins["short"].append(row)
        elif dur <= 8.0:
            bins["medium"].append(row)
        else:
            bins["long"].append(row)

    train_all, val_all, test_all = [], [], []
    for bucket, bucket_rows in bins.items():
        if not bucket_rows:
            continue
        t, v, te = split_random(bucket_rows, train_r, val_r, seed)
        train_all.extend(t)
        val_all.extend(v)
        test_all.extend(te)

    # Reshuffle merged sets
    rng = random.Random(seed)
    for lst in (train_all, val_all, test_all):
        rng.shuffle(lst)

    return train_all, val_all, test_all


def main(args_list: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(
        description="Split filtered manifest into train/val/test sets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python 4_split_dataset.py --manifest normalized/_manifest.csv
  python 4_split_dataset.py --manifest normalized/_manifest.csv --train 0.95 --val 0.025 --test 0.025
  python 4_split_dataset.py --manifest normalized/_manifest.csv --speaker-col speaker_id
  python 4_split_dataset.py --manifest normalized/_manifest.csv --stratify-dur
        """,
    )
    parser.add_argument("--manifest",     required=True,
                        help="Path to filtered _manifest.csv")
    parser.add_argument("--train",        type=float, default=DEFAULT_TRAIN,
                        help=f"Training set ratio (default {DEFAULT_TRAIN})")
    parser.add_argument("--val",          type=float, default=DEFAULT_VAL,
                        help=f"Validation set ratio (default {DEFAULT_VAL})")
    parser.add_argument("--test",         type=float, default=DEFAULT_TEST,
                        help=f"Test set ratio (default {DEFAULT_TEST})")
    parser.add_argument("--seed",         type=int,   default=DEFAULT_SEED,
                        help=f"Random seed (default {DEFAULT_SEED})")
    parser.add_argument("--speaker-col",  default=None,
                        help="Manifest column name for speaker ID (enables speaker-aware split)")
    parser.add_argument("--stratify-dur", action="store_true",
                        help="Stratify by clip duration to ensure balanced duration distribution")
    parser.add_argument("--out",          default=None,
                        help="Output directory for splits/ (default: same dir as manifest)")
    args = parser.parse_args(args_list)

    # Validate ratios
    total = args.train + args.val + args.test
    if abs(total - 1.0) > 0.001:
        sys.exit(f"ERROR: train + val + test must sum to 1.0, got {total:.3f}")

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        sys.exit(f"ERROR: manifest not found: {manifest_path}")

    out_dir = Path(args.out) if args.out else manifest_path.parent / "splits"
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Input  : {len(rows)} samples")
    print(f"Ratios : train={args.train}  val={args.val}  test={args.test}")
    print(f"Seed   : {args.seed}")

    # ── split ─────────────────────────────────────────────────────────────────
    if args.speaker_col:
        if args.speaker_col not in rows[0]:
            sys.exit(f"ERROR: column '{args.speaker_col}' not in manifest.")
        print(f"Method : speaker-aware  (col: {args.speaker_col})")
        train, val, test = split_by_speaker(
            rows, args.speaker_col, args.train, args.val, args.seed)
    elif args.stratify_dur:
        print("Method : stratified by duration")
        train, val, test = split_stratified_dur(
            rows, args.train, args.val, args.seed)
    else:
        print("Method : random shuffle")
        train, val, test = split_random(rows, args.train, args.val, args.seed)

    # ── write splits ──────────────────────────────────────────────────────────
    write_split(train, out_dir / "train.csv")
    write_split(val,   out_dir / "val.csv")
    write_split(test,  out_dir / "test.csv")

    # ── write split_info.json ─────────────────────────────────────────────────
    info = {
        "total":       len(rows),
        "train":       len(train),
        "val":         len(val),
        "test":        len(test),
        "train_ratio": round(len(train) / len(rows), 4),
        "val_ratio":   round(len(val)   / len(rows), 4),
        "test_ratio":  round(len(test)  / len(rows), 4),
        "seed":        args.seed,
        "method":      (
            f"speaker-aware ({args.speaker_col})" if args.speaker_col
            else "stratified-duration" if args.stratify_dur
            else "random"
        ),
    }
    with open(out_dir / "split_info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)

    metadata_out = out_dir / "metadata_4_split.json"
    with open(metadata_out, "w", encoding="utf-8") as f:
        json.dump({
            "stage": 4,
            "name": "split_dataset",
            "args": vars(args),
            "stats": info
        }, f, indent=2)

    print(f"\n{'─' * 50}")
    print(f"  Train  : {len(train):>6}  -> {out_dir / 'train.csv'}")
    print(f"  Val    : {len(val):>6}  -> {out_dir / 'val.csv'}")
    print(f"  Test   : {len(test):>6}  -> {out_dir / 'test.csv'}")
    print(f"  Info   : {out_dir / 'split_info.json'}")
    print(f"{'─' * 50}")

    return out_dir


if __name__ == "__main__":
    main()
