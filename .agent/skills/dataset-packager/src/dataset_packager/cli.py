import sys
import argparse
import json
import time
from pathlib import Path

from . import (
    stage1_audio_normalizer as stage1,
    stage2_text_cleaner as stage2,
    stage3_quality_filter as stage3,
    stage4_split_dataset as stage4,
    stage5_packager as stage5
)
from .metadata_helper import get_subtitle_metadata
from .root_finder import get_project_root

def _inject_subtitle_metadata(json_path: Path, subtitle_metadata: dict):
    if not subtitle_metadata or not json_path.exists():
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "subtitle_metadata" not in data:
            data["subtitle_metadata"] = subtitle_metadata
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to inject subtitle metadata into {json_path}: {e}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Master pipeline for formatting, cleaning, and packaging machine learning datasets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    
    # Global pipeline options
    group_pipe = parser.add_argument_group("Pipeline options")
    group_pipe.add_argument("--start-stage", type=int, default=1, help="Stage to start from (1-5)")
    group_pipe.add_argument("--end-stage", type=int, default=5, help="Stage to end at (1-5)")
    group_pipe.add_argument("--skip-normalization", action="store_true", help="Skip audio normalization (assumes audio is already 16kHz WAV in norm-dir)")
    group_pipe.add_argument("--clips", default="clips", help="Input directory containing media clips from media-slice")
    group_pipe.add_argument("--manifest", default="clips/_manifest.csv", help="Input manifest file from media-slice")
    group_pipe.add_argument("--dataset-name", default="my-dataset", help="Dataset name, used for output folder and README card")
    group_pipe.add_argument("--norm-dir", default=None, help="Intermediate output directory (default: <root>/datasets/<dataset-name>/normalized)")
    group_pipe.add_argument("--out", default=None, help="Final output directory (default: <root>/datasets/<dataset-name>/packaged)")
    group_pipe.add_argument("--text-col", default=None, help="Explicitly specify the text column to process")

    # Stage 1: Audio normalizer
    group_s1 = parser.add_argument_group("Stage 1: Audio normalizer")
    group_s1.add_argument("--sr", type=int, default=16000, help="Target sample rate (Hz). Use 22050 for TTS.")
    group_s1.add_argument("--lufs", type=float, default=-23.0, help="Target integrated loudness LUFS")
    group_s1.add_argument("--workers", type=int, default=4, help="Parallel workers for audio processing")

    # Stage 2: Text cleaner
    group_s2 = parser.add_argument_group("Stage 2: Text cleaner")
    group_s2.add_argument("--tts", action="store_true", help="TTS mode: preserve casing/punctuation, etc. Applies to multiple stages.")
    group_s2.add_argument("--lang", default="en", help="Language code for number expansion / cleaning")
    group_s2.add_argument("--max-tts-chars", type=int, default=200, help="Drop TTS samples longer than N chars")
    group_s2.add_argument("--remove-punctuation", action="store_true", help="Remove punctuation from ASR text (default: keep punctuation)")

    # Stage 3: Quality filter
    group_s3 = parser.add_argument_group("Stage 3: Quality filter")
    group_s3.add_argument("--cer", action="store_true", help="Enable CER verification via whisper-tiny (slow)")
    group_s3.add_argument("--filter-audio", action="store_true", help="Enable audio quality filtering (default: do not filter)")
    group_s3.add_argument("--min-dur", type=float, default=None, help="Override min clip duration (s)")
    group_s3.add_argument("--max-dur", type=float, default=None, help="Override max clip duration (s)")
    group_s3.add_argument("--min-snr", type=float, default=20.0, help="Minimum SNR (dB)")
    group_s3.add_argument("--max-cer", type=float, default=0.15, help="Maximum CER against whisper-tiny")
    group_s3.add_argument("--min-wps", type=float, default=0.5, help="Minimum words/second")
    group_s3.add_argument("--max-wps", type=float, default=5.0, help="Maximum words/second")

    # Stage 4: Split dataset
    group_s4 = parser.add_argument_group("Stage 4: Split dataset")
    group_s4.add_argument("--train", type=float, default=0.90, help="Train ratio")
    group_s4.add_argument("--val", type=float, default=0.05, help="Val ratio")
    group_s4.add_argument("--test", type=float, default=0.05, help="Test ratio")
    group_s4.add_argument("--seed", type=int, default=42, help="Random seed for splitting")
    group_s4.add_argument("--speaker-col", default=None, help="Manifest column for speaker ID (enables speaker-aware split)")
    group_s4.add_argument("--stratify-dur", action="store_true", help="Stratify by clip duration")

    # Stage 5: Packager
    group_s5 = parser.add_argument_group("Stage 5: Packager")
    group_s5.add_argument("--shard-size", type=int, default=500, help="Samples per Parquet shard")
    group_s5.add_argument("--push-to-hub", default=None, help="HF Hub repo ID to push to")
    group_s5.add_argument("--token", default=None, help="HF API token for push")

    args = parser.parse_args()

    root_dir = get_project_root()

    def resolve_path(p: str | None, default: str | None = None) -> str | None:
        if p is None:
            if default is None:
                return None
            p = default
        path_obj = Path(p)
        if not path_obj.is_absolute():
            path_obj = root_dir / path_obj
        return str(path_obj.resolve())

    # Resolve default paths dynamically relative to project root
    args.clips = resolve_path(args.clips)
    args.manifest = resolve_path(args.manifest)
    args.norm_dir = resolve_path(args.norm_dir, f"datasets/{args.dataset_name}/normalized")
    args.out = resolve_path(args.out, f"datasets/{args.dataset_name}/packaged")

    if args.skip_normalization:
        args.start_stage = max(args.start_stage, 2)

    # Validate stage range
    if not (1 <= args.start_stage <= args.end_stage <= 5):
        parser.error(
            f"Invalid stage range: --start-stage {args.start_stage} --end-stage {args.end_stage}. "
            "Both must be 1–5 and start <= end."
        )

    # Track overall metadata
    subtitle_metadata = get_subtitle_metadata(args.clips)
    overall_metadata = {
        "pipeline_args": vars(args),
        "subtitle_metadata": subtitle_metadata,
        "stages": {},
        "start_time": time.time(),
        "success": False
    }

    print("========================================")
    print("  Dataset Packaging Pipeline")
    print("========================================\n")

    current_manifest = args.manifest

    # Note: to resume, we figure out what the last manifest would have been named cleanly
    # or the user passes `--manifest` pointing to the intermediate. 
    # For robust resuming, let's auto-discover the input manifest if resuming.
    norm_dir = Path(args.norm_dir)
    manifest_stage1 = norm_dir / "_manifest_1_normalized.csv"
    manifest_stage2 = norm_dir / "_manifest_2_cleaned.csv"
    manifest_stage3 = norm_dir / "_manifest_3_filtered.csv"
    
    if args.start_stage > 1:
        # Determine the correct manifest to pick up from
        if args.start_stage == 2:
            current_manifest = str(manifest_stage1)
        elif args.start_stage == 3:
            current_manifest = str(manifest_stage2)
        elif args.start_stage == 4:
            current_manifest = str(manifest_stage3)
        elif args.start_stage == 5:
            current_manifest = None # Manifest not needed for stage 5, it uses splits_dir
            
        if current_manifest:
            print(f"Resuming at stage {args.start_stage}, using manifest: {current_manifest}")
        else:
            print(f"Resuming at stage {args.start_stage}")

    try:
        # ── Stage 1: Audio normalizer ─────────────────────────────────────────
        if args.start_stage <= 1 <= args.end_stage:
            print(f"[1/5] Normalizing audio (mono, {args.sr} Hz, {args.lufs} LUFS)...")
            # Stage 1 prepends --clips to --manifest, so extract just the
            # filename relative to the clips directory.
            manifest_name = Path(current_manifest).name
            s1_args = [
                "--clips", args.clips,
                "--out", args.norm_dir,
                "--sr", str(args.sr),
                "--lufs", str(args.lufs),
                "--workers", str(args.workers),
                "--manifest", manifest_name,
            ]
            out_manifest = stage1.main(s1_args)
            current_manifest = str(out_manifest)

            s1_meta_path = norm_dir / "metadata_1_normalized.json"
            _inject_subtitle_metadata(s1_meta_path, subtitle_metadata)
            with open(s1_meta_path, "r") as f:
                overall_metadata["stages"]["1_normalized"] = json.load(f)
            print("")
        # ── Stage 2: text_cleaner ─────────────────────────────────────────────
        if args.start_stage <= 2 <= args.end_stage:
            print("[2/5] Cleaning transcript text...")
            s2_args = [
                "--manifest", current_manifest,
                "--lang", args.lang,
                "--max-tts-chars", str(args.max_tts_chars)
            ]
            if args.tts:
                s2_args.append("--tts")
            if args.remove_punctuation:
                s2_args.append("--remove-punctuation")
            
            out_manifest = stage2.main(s2_args)
            current_manifest = str(out_manifest)

            s2_meta_path = Path(current_manifest).parent / "metadata_2_cleaned.json"
            _inject_subtitle_metadata(s2_meta_path, subtitle_metadata)
            with open(s2_meta_path, "r") as f:
                overall_metadata["stages"]["2_cleaned"] = json.load(f)
            print("")
        # ── Stage 3: quality_filter ───────────────────────────────────────────
        if args.start_stage <= 3 <= args.end_stage:
            print("[3/5] Filtering by duration, SNR, and speaking rate...")
            s3_args = [
                "--manifest", current_manifest,
                "--min-snr", str(args.min_snr),
                "--max-cer", str(args.max_cer),
                "--min-wps", str(args.min_wps),
                "--max-wps", str(args.max_wps)
            ]
            if args.text_col: s3_args.extend(["--text-col", args.text_col])
            if args.tts: s3_args.append("--tts")
            if args.cer: s3_args.append("--cer")
            if args.filter_audio: s3_args.append("--filter-audio")
            if args.min_dur is not None: s3_args.extend(["--min-dur", str(args.min_dur)])
            if args.max_dur is not None: s3_args.extend(["--max-dur", str(args.max_dur)])
            
            out_manifest = stage3.main(s3_args)
            current_manifest = str(out_manifest)

            s3_meta_path = Path(current_manifest).parent / "metadata_3_filtered.json"
            _inject_subtitle_metadata(s3_meta_path, subtitle_metadata)
            with open(s3_meta_path, "r") as f:
                overall_metadata["stages"]["3_filtered"] = json.load(f)
            print("")
        # ── Stage 4: split_dataset ────────────────────────────────────────────
        splits_dir = Path(args.norm_dir) / "splits" if args.start_stage == 5 else None
        if args.start_stage <= 4 <= args.end_stage:
            print("[4/5] Splitting into train/val/test...")
            s4_args = [
                "--manifest", current_manifest,
                "--train", str(args.train),
                "--val", str(args.val),
                "--test", str(args.test),
                "--seed", str(args.seed),
                "--out", str(Path(args.norm_dir) / "splits")
            ]
            if args.speaker_col: s4_args.extend(["--speaker-col", args.speaker_col])
            if args.stratify_dur: s4_args.append("--stratify-dur")
            
            splits_dir = stage4.main(s4_args)

            s4_meta_path = splits_dir / "metadata_4_split.json"
            _inject_subtitle_metadata(s4_meta_path, subtitle_metadata)
            with open(s4_meta_path, "r") as f:
                overall_metadata["stages"]["4_split"] = json.load(f)
            print("")
        # ── Stage 5: packager ─────────────────────────────────────────────────
        if args.start_stage <= 5 <= args.end_stage:
            print("[5/5] Packaging into HuggingFace dataset format...")
            if splits_dir is None:
                splits_dir = Path(args.norm_dir) / "splits"

            s5_args = [
                "--splits", str(splits_dir),
                "--wav-dir", args.norm_dir,
                "--out", args.out,
                "--shard-size", str(args.shard_size),
                "--dataset-name", args.dataset_name,
                "--lang", args.lang,
            ]
            if args.tts: s5_args.append("--tts")
            if args.speaker_col: s5_args.extend(["--speaker-col", args.speaker_col])
            if args.push_to_hub: s5_args.extend(["--push-to-hub", args.push_to_hub])
            if args.token: s5_args.extend(["--token", args.token])
            
            packaged_dir = stage5.main(s5_args)

            s5_meta_path = packaged_dir / "metadata_5_packaged.json"
            _inject_subtitle_metadata(s5_meta_path, subtitle_metadata)
            with open(s5_meta_path, "r") as f:
                overall_metadata["stages"]["5_packaged"] = json.load(f)
            print("")
        overall_metadata["success"] = True

    except Exception as e:
        overall_metadata["success"] = False
        overall_metadata["error"] = str(e)
        raise e
        
    finally:
        # Save overall metadata
        overall_metadata["end_time"] = time.time()
        overall_metadata["elapsed_s"] = overall_metadata["end_time"] - overall_metadata["start_time"]
        
        # Pull in any stage metadata we can find if it's not already in memory
        splits_dir = norm_dir / "splits"
        final_dir = Path(args.out)
        
        stage_files = {
            "1_normalized": norm_dir / "metadata_1_normalized.json",
            "2_cleaned": norm_dir / "metadata_2_cleaned.json",
            "3_filtered": norm_dir / "metadata_3_filtered.json",
            "4_split": splits_dir / "metadata_4_split.json",
            "5_packaged": final_dir / "metadata_5_packaged.json",
        }
        for stg_key, stg_file in stage_files.items():
            if stg_key not in overall_metadata["stages"] and stg_file.exists():
                _inject_subtitle_metadata(stg_file, subtitle_metadata)
                try:
                    with open(stg_file, "r") as f:
                        overall_metadata["stages"][stg_key] = json.load(f)
                except Exception as e:
                    pass
        
        final_dir.mkdir(parents=True, exist_ok=True)
        with open(final_dir / "metadata_overall.json", "w", encoding="utf-8") as f:
            json.dump(overall_metadata, f, indent=2)

    if overall_metadata["success"]:
        print("========================================")
        print(f"  Done! Overall metadata saved to: {final_dir / 'metadata_overall.json'}")
        print("========================================")

if __name__ == "__main__":
    main()
