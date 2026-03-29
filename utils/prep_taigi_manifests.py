import csv
import shutil
from pathlib import Path

csv_path = Path(r"C:\Users\marti\Projects\taigi-semantic\datasets\kautian_complete_poj_2026_0204_en_v1a.csv")
sutiau_dir = Path(r"C:\Users\marti\Projects\taigi-semantic\datasets\sutiau-mp3")
leku_dir = Path(r"C:\Users\marti\Projects\taigi-semantic\datasets\leku-mp3")

output_base = Path("datasets")

datasets = {
    "taigi_vocab_hanzi": {
        "text_col": "詞目_漢字",
        "audio_col": "詞目_音檔",
        "audio_dir": sutiau_dir,
    },
    "taigi_vocab_lomaji": {
        "text_col": "詞目_羅馬字",
        "audio_col": "詞目_音檔",
        "audio_dir": sutiau_dir,
    },
    "taigi_example_hanzi": {
        "text_col": "例句_漢字",
        "audio_col": "例句_音檔",
        "audio_dir": leku_dir,
    },
    "taigi_example_lomaji": {
        "text_col": "例句_羅馬字",
        "audio_col": "例句_音檔",
        "audio_dir": leku_dir,
    }
}

def main():
    # Setup directories and writers
    writers = {}
    files = {}
    for ds_name, config in datasets.items():
        clips_dir = output_base / ds_name / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        
        manifest_path = clips_dir / "_manifest.csv"
        f = open(manifest_path, "w", encoding="utf-8", newline="")
        writer = csv.DictWriter(f, fieldnames=["index", "file", "text"])
        writer.writeheader()
        
        writers[ds_name] = writer
        files[ds_name] = f
        config["clips_dir"] = clips_dir

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
            for index, row in enumerate(reader):
                for ds_name, config in datasets.items():
                    text = row.get(config["text_col"], "").strip()
                    audio_filename = row.get(config["audio_col"], "").strip()
                    
                    if not text or not audio_filename:
                        continue
                    
                    if not audio_filename.lower().endswith(".mp3"):
                        audio_filename += ".mp3"
                        
                    src_audio = config["audio_dir"] / audio_filename
                    if not src_audio.exists():
                        continue
                        
                    dst_audio = config["clips_dir"] / audio_filename
                    
                    # Copy audio if not already copied (to save I/O, though we should probably check if it's there)
                    if not dst_audio.exists():
                        dst_audio.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_audio, dst_audio)
                        
                    # Write to manifest
                    writers[ds_name].writerow({
                        "index": index,
                        "file": audio_filename,
                        "text": text
                    })
                    
        print("Manifests prepared successfully.")
        
    finally:
        for f in files.values():
            f.close()

if __name__ == "__main__":
    main()
