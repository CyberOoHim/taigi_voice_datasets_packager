---
language:
- nan
license: other
task_categories:
- automatic-speech-recognition
tags:
- speech
- audio
- asr
---

# lai-ching-te-speech

ASR dataset packaged from SRT-aligned audio clips.

## Dataset info

| Split      | Samples |
|------------|---------|
| train      | 10 |
| validation | 0 |
| test       | 0 |

Total audio: **0.0 hours**

## Usage

```python
from datasets import load_dataset, Audio

ds = load_dataset("lai-ching-te-speech")
ds = ds.cast_column("audio", Audio(sampling_rate=16000))

# Access a sample
sample = ds["train"][0]
print(sample["text"])        # transcript
print(sample["audio"])       # {"array": ..., "sampling_rate": ...}
```

## Columns

- `audio` — raw WAV bytes embedded in Parquet
- `text` — ASR-normalized transcript (lowercase, no punctuation)
- `duration_s` — clip duration in seconds
- `snr_db` — estimated signal-to-noise ratio
- `wps` — words per second (speaking rate)
- `original_text` — raw subtitle text before normalization
