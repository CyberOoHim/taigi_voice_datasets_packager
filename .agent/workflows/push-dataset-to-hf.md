---
description: Push a local dataset to the Hugging Face hub
---

This workflow handles uploading a packaged local dataset folder directly to the `<hf_id>/<repo_id>` Hugging Face repository using the `hf` CLI.

**Instructions for the AI Assistant:**
When the user asks to run this workflow (e.g. `/push-dataset`), identify the local path to the dataset (`<LOCAL_PATH>`) and the target directory path on the Hugging Face repository (`<REMOTE_PATH>`). If not provided by the user, ask for them.

// turbo-all

1. Use the `run_command` tool to execute the `hf upload` command with the provided paths. Use the globally installed `hf` CLI, which is already authenticated. Replace the placeholders in the command below with the actual paths:

```powershell
hf upload <repo_id> <LOCAL_PATH> <REMOTE_PATH> --repo-type dataset
```

1. Check the command output to ensure it finished hashing and copying the dataset.
2. Once completed, inform the user that the dataset has been successfully uploaded to the repository.
