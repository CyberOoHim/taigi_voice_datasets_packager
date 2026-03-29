import os
import sys
import glob
import re
import argparse
from pathlib import Path

import datasets
import pandas as pd
import numpy as np

def get_latest_parquet_files(split_dir):
    files = glob.glob(os.path.join(split_dir, "*.parquet"))
    if not files:
        return []
    
    groups = {}
    for f in files:
        m = re.search(r'-of-(\d+)\.parquet$', f)
        if m:
            suffix = m.group(1)
            groups.setdefault(suffix, []).append(f)
        else:
            groups.setdefault('none', []).append(f)
            
    best_group = []
    max_mtime = -1
    for suffix, group_files in groups.items():
        mtime = max(os.path.getmtime(f) for f in group_files)
        if mtime > max_mtime:
            max_mtime = mtime
            best_group = group_files
    return [os.path.basename(f) for f in best_group]

def get_project_root() -> Path:
    # A simple helper to find the project root by looking for pyproject.toml
    # Starts from the directory where this script is, and goes up.
    current_dir = Path(__file__).resolve().parent
    for p in [current_dir] + list(current_dir.parents):
        if (p / "pyproject.toml").exists():
            return p
            
    # Fallback to CWD
    return Path.cwd()

def main():
    parser = argparse.ArgumentParser(
        description="Generate statistics for one or multiple packaged datasets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    
    parser.add_argument(
        "--datasets", 
        nargs="+", 
        default=["cv-nan-tw-validated", "suisiann", "taigi-example-lomaji", "taigi-vocab-lomaji"],
        help="List of dataset names to process."
    )
    
    parser.add_argument(
        "--out", 
        default="stats.md",
        help="Output markdown file for the statistics."
    )
    
    args = parser.parse_args()
    
    root_dir = get_project_root()
    base_dir = root_dir / "datasets"
    
    all_durations = []
    results = []
    dataset_examples = {}
    
    print(f"Generating stats for {len(args.datasets)} dataset(s)...")
    
    for name in args.datasets:
        # Check standard packaged path, and custom paths if applicable
        path_options = [
            base_dir / name / "packaged",
            base_dir / name / "packaged_filtered_single_word",
            Path(name) # Check if user passed an absolute path
        ]
        
        path = None
        for opt in path_options:
            if opt.exists() and opt.is_dir():
                path = str(opt.resolve())
                break
                
        if not path:
            print(f"Warning: Could not find packaged data for dataset '{name}'. Skipping.")
            continue
            
        desc = f"{name} Dataset"
        print(f"Loading {name} from {path}...")
        try:
            train_files = get_latest_parquet_files(os.path.join(path, "train"))
            val_files = get_latest_parquet_files(os.path.join(path, "validation"))
            test_files = get_latest_parquet_files(os.path.join(path, "test"))
            
            data_files = {}
            if train_files: data_files["train"] = [f"train/{f}" for f in train_files]
            if val_files: data_files["validation"] = [f"validation/{f}" for f in val_files]
            if test_files: data_files["test"] = [f"test/{f}" for f in test_files]

            if not data_files:
                print(f"Warning: No valid parquet files found in train/validation/test for '{name}'. Skipping.")
                continue

            ds = datasets.load_dataset(
                "parquet", 
                data_files=data_files,
                data_dir=path
            )
            
            if isinstance(ds, datasets.DatasetDict):
                dfs = [d.to_pandas() for split, d in ds.items()]
                df = pd.concat(dfs, ignore_index=True)
            else:
                df = ds.to_pandas()
                
            if 'duration_s' not in df.columns:
                print(f"Warning: 'duration_s' not found for {name}.")
                continue
                
            durations = df['duration_s'].dropna().values
            # Filter out extreme outliers for stat calculation if needed (optional)
            # durations = durations[durations <= 15.0]
            
            all_durations.extend(durations)
            
            text_col = None
            if 'text' in df.columns:
                text_col = 'text'
            elif 'sentence' in df.columns:
                text_col = 'sentence'
                
            if text_col:
                valid_texts = df[text_col].dropna()
                n_samples = min(10, len(valid_texts))
                if n_samples > 0:
                    sampled = valid_texts.sample(n=n_samples, random_state=42).tolist()
                    dataset_examples[name] = sampled
            
            count = len(durations)
            total_hours = np.sum(durations) / 3600
            
            results.append({
                "Dataset": name,
                "Path": getattr(Path(path), 'name', path),
                "Description": desc,
                "Count": count,
                "Total Hours": total_hours,
                "Min (s)": np.min(durations) if count > 0 else 0,
                "Max (s)": np.max(durations) if count > 0 else 0,
                "Mean (s)": np.mean(durations) if count > 0 else 0,
                "P50 (s)": np.percentile(durations, 50) if count > 0 else 0,
                "P75 (s)": np.percentile(durations, 75) if count > 0 else 0,
                "P90 (s)": np.percentile(durations, 90) if count > 0 else 0,
                "P95 (s)": np.percentile(durations, 95) if count > 0 else 0,
                "P99 (s)": np.percentile(durations, 99) if count > 0 else 0
            })
        except Exception as e:
            print(f"Error loading {name}: {e}")

    if all_durations and len(args.datasets) > 1:
        durations = np.array(all_durations)
        count = len(durations)
        results.append({
            "Dataset": "TOTAL (Combined)",
            "Path": "",
            "Description": "Aggregated stats for combined datasets",
            "Count": count,
            "Total Hours": np.sum(durations) / 3600,
            "Min (s)": np.min(durations) if count > 0 else 0,
            "Max (s)": np.max(durations) if count > 0 else 0,
            "Mean (s)": np.mean(durations) if count > 0 else 0,
            "P50 (s)": np.percentile(durations, 50) if count > 0 else 0,
            "P75 (s)": np.percentile(durations, 75) if count > 0 else 0,
            "P90 (s)": np.percentile(durations, 90) if count > 0 else 0,
            "P95 (s)": np.percentile(durations, 95) if count > 0 else 0,
            "P99 (s)": np.percentile(durations, 99) if count > 0 else 0
        })

    if not results:
        print("No valid datasets processed. Exiting.")
        return

    df_results = pd.DataFrame(results)

    # Round floats to 2 decimal places
    for col in df_results.columns:
        if col not in ['Dataset', 'Path', 'Description', 'Count']:
            df_results[col] = df_results[col].round(2)

    try:
        with open(args.out, 'w', encoding='utf-8') as out_f:
            out_f.write("# Dataset Statistics\n\n")
            
            # Manually generate Markdown table
            headers = df_results.columns.tolist()
            header_row = "| " + " | ".join(headers) + " |"
            sep_row = "|" + "|".join(["---"] * len(headers)) + "|"
            
            out_f.write(header_row + "\n")
            out_f.write(sep_row + "\n")
            for _, row in df_results.iterrows():
                row_strs = []
                for val in row.values:
                    if isinstance(val, (float, np.floating)):
                        row_strs.append(f"{val:.2f}")
                    else:
                        row_strs.append(str(val))
                out_f.write("| " + " | ".join(row_strs) + " |\n")

            out_f.write("\n# Random Text Examples\n\n")
            for ds_name, examples in dataset_examples.items():
                out_f.write(f"## {ds_name} ({len(examples)} examples)\n\n")
                for i, ex in enumerate(examples, 1):
                    out_f.write(f"{i}. {ex}\n")
                out_f.write("\n")
                
        print(f"\nStatistics successfully generated and saved to: {args.out}")
        
    except Exception as e:
        print(f"Error saving to {args.out}: {e}")

if __name__ == "__main__":
    main()
