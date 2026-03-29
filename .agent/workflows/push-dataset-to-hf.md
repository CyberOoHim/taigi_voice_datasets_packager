---
description: Push a local dataset to the Hugging Face hub
---

This workflow handles uploading a packaged local dataset folder directly to the `<hf_id>/<repo_id>` Hugging Face repository using the `hf` CLI.

If the dataset was previously pushed using `save_to_disk` format (non-standard), it will first be converted to standard parquet format before uploading.

**Instructions for the AI Assistant:**
When the user asks to run this workflow (e.g. `/push-dataset`), identify the local path to the dataset (`<LOCAL_PATH>`) and the target directory path on the Hugging Face repository (`<REMOTE_PATH>`). If not provided by the user, ask for them.

// turbo-all

1. **Convert existing HF datasets to standard parquet format.** If the user specifies a `<REPO_ID>` that already exists on Hugging Face and needs conversion from `save_to_disk` format, run the conversion script first:

```powershell
python .agent/workflows/convert_hf_dataset.py <REPO_ID>
```

Optional flags:
- `--dry-run` — Download and inspect only, don't push.
- `--target-repo <TARGET_REPO>` — Push to a different repo instead of overwriting.
- `--private` — Make the target repo private.
- `--subfolder <SUBFOLDER>` — Specify subfolder containing `dataset_dict.json` (auto-detected if omitted).

Skip this step if the user is uploading a fresh local dataset that hasn't been pushed before.

2. Use the `run_command` tool to execute the `hf upload` command with the provided paths. Use the globally installed `hf` CLI, which is already authenticated. Replace the placeholders in the command below with the actual paths:

```powershell
hf upload <repo_id> <LOCAL_PATH> <REMOTE_PATH> --repo-type dataset
```

3. Check the command output to ensure it finished hashing and copying the dataset.
4. Once completed, inform the user that the dataset has been successfully uploaded to the repository.
