---
name: dataset-packager
description: Unifies the 5-stage packaging pipeline for ASR and TTS datasets into a single CLI tool. Normalizes audio, cleans text, filters quality, splits sets, and produces HuggingFace Parquet format. Supports intermediate saving and resumability.
---

# Dataset Packager

This skill provides two main commands:
1. `dataset-pack`: Executes the 5-stage dataset packaging pipeline. It reads raw clips and metadata produced by `media-slice`, processes them through normalization, cleaning, filtering, and splitting, then outputs a Parquet dataset suitable for `datasets.load_dataset()` and HuggingFace Hub distribution.
2. `dataset-stats`: Generates statistics for one or multiple packaged datasets.

## Setup

Ensure your project environment is active and updated:

```bash
.\.venv\Scripts\pip install -e .
```

*Requirements:*
- Python >= 3.10
- `ffmpeg` and `ffprobe` must be on your system PATH.
- `pyarrow`, `soundfile`, `numpy`, `tqdm`
- Optional: `openai-whisper` (for `--cer` checks), `num2words` (for TTS number expansion)
- Optional: `datasets`, `huggingface_hub` (for `--push-to-hub`)

**Hardware Acceleration:**
If an NVIDIA GPU is present and `torch` with CUDA support is installed (`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124`), Stage 3 quality filtering with `--cer` will automatically load the Whisper model onto the GPU for vastly improved performance.

## Usage

Use the globally installed `dataset-pack` command from the **project root**:

```bash
.\.venv\Scripts\dataset-pack --clips clips --manifest clips/_manifest.csv --dataset-name custom-dataset [options]
```
```

### Output Folders

The pipeline saves its artifacts into two main directories based on the provided `--dataset-name`:
- **Normalized Outputs** (`datasets/<dataset-name>/normalized/`): Contains the generated intermediate mono WAV files and step-by-step manifests.
- **Packaged Dataset** (`datasets/<dataset-name>/packaged/`): Contains the final HuggingFace-compatible Parquet files, ready to be loaded via `load_dataset()`.

### Language & Non-English Datasets

By default, the pipeline applies English (`--lang en`) rules. This means it will aggressively strip out non-Latin characters (like Chinese or Japanese) during Stage 2, and may drop valid clips during Stage 3's words-per-second filter (since those languages don't use space dividers for words).

When packing non-English datasets, you **MUST** provide:
- `--lang <code-here>` (e.g. `--lang zh`) to preserve the appropriate character set and number expansions.
- `--min-wps 0.0` to bypass the minimum words-per-second filter, which will otherwise drop valid Asian-language transcripts.
- **For Taigi (Taiwanese):** Always skip the `--cer` flag in Stage 3. Whisper's internal models (used for CER verification) do not currently support Taigi reliably, and enabling it will cause most valid clips to be incorrectly discarded.

### Full Pipeline Run

By default, running the tool without `--start-stage` or `--end-stage` executes all 5 stages in order:

1. **Audio normalizer**: Converts clips to mono WAV at target sample rate and -23 LUFS. (Note: Uses a simple volume filter based on integrated LUFS offset, not a full two-pass loudnorm).
2. **Text cleaner**: Normalizes text (lowercase/strip for ASR, expand/punctuate for TTS).
3. **Quality filter**: Drops clips that are too long/short, noisy, or misaligned.
4. **Split dataset**: Generates train, val, and test manifests.
5. **Packager**: Encodes into Parquet shards with embedded audio.

### Resumability

Each stage writes its intermediate outputs non-destructively inside `--norm-dir` (default `datasets/<dataset-name>/normalized/`):

| Stage | Output |
|-------|--------|
| 1 – Audio normalizer | `datasets/<dataset-name>/normalized/_manifest_1_normalized.csv` |
| 2 – Text cleaner | `datasets/<dataset-name>/normalized/_manifest_2_cleaned.csv` |
| 3 – Quality filter | `datasets/<dataset-name>/normalized/_manifest_3_filtered.csv` |
| 4 – Split dataset | `datasets/<dataset-name>/normalized/splits/{train,val,test}.csv` |

When resuming with `--start-stage`, the CLI automatically picks up the correct intermediate manifest (e.g. starting at stage 3 reads `_manifest_2_cleaned.csv`). You do not need to re-specify `--manifest`.

```bash
# Re-run just the split and packaging stages after tweaking ratios:
# (Note: if you used a custom --dataset-name previously, you must specify it again)
.\.venv\Scripts\dataset-pack --start-stage 4 --end-stage 5 --dataset-name custom-dataset --train 0.8 --val 0.1 --test 0.1
```

### Hugging Face Hub Integration (`hf` CLI)

Easily fetch datasets from or push your packaged datasets to the Hugging Face Hub using the officially supported `hf` CLI directly.

**Setup (`.env`):**
Ensure you have a `.env` file in your root directory. The AI agent or user can parse this to enforce the target token and repository directly in the terminal (overriding the system-cached HF auth):
```env
HF_TOKEN=your_huggingface_token
HF_REPO_ID=yourname/my-dataset
```

**Upload to HF Hub:**
Uploads a packaged dataset (e.g. `datasets/my-dataset/packaged/`) to your Hugging Face repository.
```powershell
# Using PowerShell (reads .env to override system auth and repo id):
$token = (Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*HF_TOKEN\s*=" }) -replace "^\s*HF_TOKEN\s*=\s*", ""
$repo = (Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*HF_REPO_ID\s*=" }) -replace "^\s*HF_REPO_ID\s*=\s*", ""

if ($token) { $env:HF_TOKEN = $token.Trim().Trim('"', "'") }
if (!$repo) { $repo = "yourname/my-dataset" } else { $repo = $repo.Trim().Trim('"', "'") }

# Upload to the root of the repository:
hf upload $repo datasets/my-dataset/packaged/ . --repo-type dataset

# Upload as a named configuration (subfolder):
hf upload $repo datasets/my-dataset/packaged/ my-dataset --repo-type dataset
```

**Download from HF Hub:**
Downloads a dataset from Hugging Face Hub to a local directory (e.g. `datasets/`).
```powershell
# Using PowerShell (reads .env to override system auth and repo id):
$token = (Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*HF_TOKEN\s*=" }) -replace "^\s*HF_TOKEN\s*=\s*", ""
$repo = (Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*HF_REPO_ID\s*=" }) -replace "^\s*HF_REPO_ID\s*=\s*", ""

if ($token) { $env:HF_TOKEN = $token.Trim().Trim('"', "'") }
if (!$repo) { $repo = "yourname/my-dataset" } else { $repo = $repo.Trim().Trim('"', "'") }


hf download $repo --local-dir datasets/my-dataset --repo-type dataset
```

### Generating Statistics (`dataset-stats`)

You can generate comprehensive statistics for your packaged datasets using the `dataset-stats` command. It calculates metrics like total hours, minimum/maximum/median lengths, and various percentiles.

```bash
# Generate stats for the default built-in datasets:
.\.venv\Scripts\dataset-stats

# Generate stats for a specific dataset or multiple datasets:
.\.venv\Scripts\dataset-stats --datasets my-dataset my-other-dataset --out report.md
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--datasets` | [4 built-in defaults] | List of dataset names to query. By default it looks under `<root>/datasets/<name>/packaged`. |
| `--out` | `stats.md` | Path to save the markdown report. |

### Arguments Reference

#### Pipeline Control

| Argument | Default | Description |
|----------|---------|-------------|
| `--start-stage` | `1` | Stage to start from (1–5). |
| `--end-stage` | `5` | Stage to end at (1–5). |
| `--clips` | `clips` | Input directory of media clips from `media-slice`. |
| `--manifest` | `clips/_manifest.csv` | Input manifest CSV from `media-slice`. |
| `--dataset-name` | `my-dataset` | Dataset name, used for output folders and README card. |
| `--norm-dir` | auto | Intermediate directory for WAVs (default: `datasets/<dataset-name>/normalized`). |
| `--out` | auto | Final output directory for dataset (default: `datasets/<dataset-name>/packaged`). |
| `--skip-normalization` | off | Allow repacking datasets using already normalized audio directly, bypassing Stage 1's normalizer processing. |

#### Stage 1: Audio Normalizer

| Argument | Default | Description |
|----------|---------|-------------|
| `--sr` | `16000` | Target sample rate in Hz. Use `22050` for TTS. |
| `--lufs` | `-23.0` | Target integrated loudness (LUFS, EBU R128). |
| `--workers` | `4` | Parallel ffmpeg workers. |

#### Stage 2: Text Cleaner

| Argument | Default | Description |
|----------|---------|-------------|
| `--tts` | off | TTS mode: preserve casing/punctuation, expand numbers. Applies to stages 2, 3, and 5. |
| `--lang` | `en` | Language code for number expansion and charset. |
| `--max-tts-chars` | `200` | Drop TTS samples longer than N characters. |
| `--remove-punctuation` | off | Remove punctuation during text cleaning (default: keeps punctuation). |

#### Stage 3: Quality Filter

| Argument | Default | Description |
|----------|---------|-------------|
| `--filter-audio` | off | IMPORTANT: Controls whether clips are actually dropped based on duration and acoustic quality metrics. Defaults to `off` (only computes metrics without dropping). |
| `--cer` | off | Enable CER verification via whisper-tiny (slow). Requires `openai-whisper`. |
| `--min-dur` | auto | Minimum clip duration in seconds (ASR default: 1.0, TTS default: 1.0). Must enable `--filter-audio` to drop. |
| `--max-dur` | auto | Maximum clip duration in seconds (ASR default: 15.0, TTS default: 12.0). Must enable `--filter-audio` to drop. |
| `--min-snr` | `20.0` | Minimum SNR in dB. Must enable `--filter-audio` to drop. |
| `--max-cer` | `0.15` | Maximum Character Error Rate against whisper-tiny transcript. Must enable `--filter-audio` to drop. |
| `--min-wps` | `0.5` | Minimum words per second. Must enable `--filter-audio` to drop. |
| `--max-wps` | `5.0` | Maximum words per second. Must enable `--filter-audio` to drop. |
| `--device` | `None` | Device to run whisper model on (e.g., 'cuda', 'cpu'). Defaults to auto-detect. |

#### Stage 4: Split Dataset

| Argument | Default | Description |
|----------|---------|-------------|
| `--train` | `0.90` | Training set ratio. |
| `--val` | `0.05` | Validation set ratio. |
| `--test` | `0.05` | Test set ratio. |
| `--seed` | `42` | Random seed for reproducible splits. |
| `--speaker-col` | none | Manifest column for speaker ID (enables speaker-aware split). |
| `--stratify-dur` | off | Stratify splits by clip duration buckets. |

#### Stage 5: Packager

| Argument | Default | Description |
|----------|---------|-------------|
| `--shard-size` | `500` | Samples per Parquet shard. |
| `--shard-size` | `500` | Samples per Parquet shard. |
| `--push-to-hub` | none | HuggingFace Hub repo ID (e.g. `yourname/my-dataset`). |
| `--token` | none | HuggingFace API token (required with `--push-to-hub`). |
| `--lang` | `en` | Language code written into the README dataset card (e.g. `zh`, `fr`). |

### Reviewing Metadata

Each stage saves a `metadata_N_*.json` file alongside its output. At pipeline completion, a comprehensive `metadata_overall.json` summarizing all stages and arguments is saved in the `--out` directory. This enables total reproducibility of your HuggingFace dataset.
