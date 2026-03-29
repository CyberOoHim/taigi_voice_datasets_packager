import json
import re
from pathlib import Path
from .root_finder import get_project_root

SRT_FALLBACK_ENCODINGS = ("latin-1", "cp1252", "shift_jis", "gb2312")

def extract_srt_metadata(path: str) -> dict:
    encodings = ("utf-8-sig",) + SRT_FALLBACK_ENCODINGS
    metadata = {"Sync Offset": "0.000s", "Dropped Gaps": "[]"}
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    line = line.strip()
                    if line == "----------------------------------------" or line == "1" or line.lower().startswith("index,"):
                        break
                    if ":" in line:
                        key, val = line.split(":", 1)
                        metadata[key.strip()] = val.strip()
            return metadata
        except UnicodeDecodeError:
            continue
    return {}

def get_subtitle_metadata(clips_dir: str) -> dict:
    if not clips_dir:
        return {}
        
    clips_path = Path(clips_dir)
    fallback_srt = None
    
    # 1. Try to find from metadata json in clips folder
    if clips_path.exists() and clips_path.is_dir():
        for json_file in clips_path.glob("*_metadata.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "subtitle_metadata" in data:
                        return data["subtitle_metadata"]
                    if "srt" in data:
                        fallback_srt = data["srt"]
            except Exception:
                pass
                
    # 2. Try to fallback to the source .srt or .csv
    if fallback_srt:
        srt_path = Path(fallback_srt)
        if not srt_path.is_absolute():
            srt_path = get_project_root() / srt_path
        if srt_path.exists():
            meta = extract_srt_metadata(str(srt_path))
            if meta:
                return meta
            
    # 3. If no json found or fallback_srt missing, maybe there's an srt next to clips folder?
    if clips_path.exists():
        # Look for srt in parent dir with same stem
        parent = clips_path.parent
        stem = clips_path.name
        possible_srt = parent / f"{stem}.srt"
        if possible_srt.exists():
            meta = extract_srt_metadata(str(possible_srt))
            if meta:
                return meta
                
        possible_csv = parent / f"{stem}.csv"
        if possible_csv.exists():
            meta = extract_srt_metadata(str(possible_csv))
            if meta:
                return meta

    return {}
