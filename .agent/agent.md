# ASR Datasets Packing - Agent Workflow & Routing Guide

> **CRITICAL AGENT INSTRUCTION:** Always prioritize using the local Python virtual environment (`.venv`) for all commands and package management.

This workspace is structured as a **linear, 4-stage pipeline** for transforming raw external media into high-quality, ML-ready datasets (ASR/TTS). Agents should route tasks based on the current stage of the dataset lifecycle.

## 🔄 Major Workflow Stages

### Stage 1: Acquisition & Annotation (`media-fetcher` & AI Studio)

**Goal:** Gather the raw source material and generate high-fidelity text-to-audio alignment.

- **Route to `media-fetcher`** for downloading video/audio, grabbing existing subtitles, surgical trimming (`edit_sub`), or subtitle-synchronized media extraction (`media-sync`).
- **Direct to [Google AI Studio](https://aistudio.google.com/)** for **Taigi Transcription**, **Multilingual Translation**, or generating precise **Sentence-Aligned SRTs** using Gemini 3.1 Pro+.
- **Output:** Raw media file (`.mp4`, `.m4a`, `.mp3`, etc) + Synchronized subtitle file (`.srt`, `.csv`).

### Stage 2: Conversion & Normalization (`media-converter`)

**Goal:** Standardize the raw media for deterministic processing.

- **Route to `media-converter`** to convert videos to audio (e.g., 16kHz mono WAV) and apply **EBU R128 two-pass loudness normalization**.
- **Crucial:** This stage ensures that the volume and format are consistent before the precision slicing occurs.
- **Output:** Normalized audio master file (`.wav`).

### Stage 3: Slicing & Alignment (`media-slicer`)

**Goal:** Transform long-form masters into thousands of granular training pairs.

- **Route to `media-slicer`** to mass-split the normalized audio into clips based on the SRT timestamps.
- **Tasks:** Precise acoustic padding (`--lead`, `--tail`), stitching "good" segments (`media-compile`), and generating HTML review files to verify alignment.
- **Output:** Folder of short audio clips + Manifest CSV file mapping filenames to text.

### Stage 4: Processing, Packaging, & Statistics (`dataset-packager`)

**Goal:** Final quality control, statistics generation, and export to ML-ready formats.

- **Route to `dataset-packager` immediately** if the user asks to:
  - Generate dataset statistics or reports (`dataset-stats`).
  - Package, filter, or split datasets.
  - Convert clips to Hugging Face Parquet format.
  - Bypass normalization or customize quality filtering.
- **Commands Available:**
  - `dataset-pack`: The 5-stage automated pipeline (Audio Norm, Text Clean, Qual Filter, Data Split, Parquet Pack).
  - `dataset-stats`: Generates aggregated markdown statistics (duration percentiles, counts) for single or multiple packaged datasets.
- **Output:** ML-ready dataset on Hugging Face or local disk, and detailed markdown statistical reports.

## 🛠️ Environment Mandates

- **Always use the project's virtual environment**: `.venv\Scripts\python.exe` (on Windows).
- Never install packages to the global Python environment.
- Always run scripts from the project root while referencing the subfolders (e.g., `.venv\Scripts\python.exe test_scripts\test_finetune.py`).

## 📄 File Usage Rules

- **TL to POJ Conversion**: Whenever doing TL to POJ conversion, always use `utils\converter.py`.
