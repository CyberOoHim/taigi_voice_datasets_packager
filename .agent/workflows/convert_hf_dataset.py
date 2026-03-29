"""
Convert a save_to_disk-format HF dataset repo to standard parquet format.

Usage:
# Dry run first (inspect without pushing)
python convert_hf_dataset.py lazy-worm/taigi-asr-trial --dry-run

# Convert and push (overwrites the same repo)
python convert_hf_dataset.py lazy-worm/taigi-asr-trial

# Push to a different repo
python convert_hf_dataset.py lazy-worm/taigi-asr-trial --target-repo lazy-worm/taigi-asr-trial-v2
"""
import argparse
import sys
import tempfile
import shutil
import os

def main():
    parser = argparse.ArgumentParser(
        description="Convert a save_to_disk-format HF dataset repo to standard parquet format."
    )
    parser.add_argument("repo_id", help="HF dataset repo ID (e.g. lazy-worm/taigi-asr-trial)")
    parser.add_argument(
        "--subfolder", default=None,
        help="Subfolder inside the repo that contains dataset_dict.json (auto-detected if omitted)"
    )
    parser.add_argument(
        "--target-repo", default=None,
        help="Target repo ID to push to (defaults to same as source repo, overwriting it)"
    )
    parser.add_argument(
        "--private", action="store_true",
        help="Make the target repo private"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Download and inspect only, don't push"
    )
    args = parser.parse_args()

    from huggingface_hub import snapshot_download
    from datasets import load_from_disk

    target_repo = args.target_repo or args.repo_id

    # Download
    tmp_dir = tempfile.mkdtemp(prefix="hf_convert_")
    print(f"⬇️  Downloading {args.repo_id} to {tmp_dir}...")
    local_path = snapshot_download(args.repo_id, repo_type="dataset", local_dir=tmp_dir)

    # Auto-detect subfolder if not specified
    load_path = local_path
    if args.subfolder:
        load_path = os.path.join(local_path, args.subfolder)
    else:
        # Look for dataset_dict.json in subdirectories
        for root, dirs, files in os.walk(local_path):
            if "dataset_dict.json" in files and root != local_path:
                rel = os.path.relpath(root, local_path)
                print(f"🔍 Auto-detected subfolder: {rel}")
                load_path = root
                break

    # Load
    print(f"📂 Loading dataset from: {load_path}")
    ds = load_from_disk(load_path)
    print(f"\n📊 Dataset info:")
    print(ds)
    for split in ds:
        print(f"  {split}: {len(ds[split])} rows, columns: {ds[split].column_names}")

    if args.dry_run:
        print("\n🔍 Dry run — not pushing. Dataset looks good!")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # Push as standard parquet
    print(f"\n⬆️  Pushing to {target_repo} as standard parquet format...")
    ds.push_to_hub(target_repo, private=args.private)
    print(f"✅ Done! Dataset is now loadable via: load_dataset(\"{target_repo}\")")

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
