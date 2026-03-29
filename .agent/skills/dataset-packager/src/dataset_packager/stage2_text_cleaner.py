"""
2_text_cleaner.py — Transcript normalization for ASR/TTS datasets
==================================================================
Reads the normalized manifest and cleans the `text` column according
to the target task:

  ASR mode  (default)
  --------------------
  • Lowercase everything
  • Expand common abbreviations (Mr. -> mister, etc.)
  • Remove all punctuation except apostrophes
  • Collapse whitespace
  • Drop clips whose cleaned text is empty or too short

  TTS mode  (--tts)
  -------------------
  • Preserve casing (model learns prosody from capitals)
  • Normalize punctuation (smart quotes -> straight, em-dash -> comma+space)
  • Expand numbers to words  (42 -> forty-two)
  • Expand common abbreviations with correct casing
  • Keep sentence-ending punctuation (. ! ?)
  • Drop clips whose text exceeds MAX_TTS_CHARS (renderer limit)

Both modes:
  • Strip SRT HTML tags (in case any slipped through)
  • Strip leading/trailing whitespace
  • Reject texts that are purely numeric, URL-like, or gibberish
  • Add `text_asr` or `text_tts` column (keeps original `text` untouched)

Usage
-----
  python 2_text_cleaner.py --manifest normalized/_manifest.csv
  python 2_text_cleaner.py --manifest normalized/_manifest.csv --tts
  python 2_text_cleaner.py --manifest normalized/_manifest.csv --lang zh  # Chinese
  python 2_text_cleaner.py --help

Requirements
------------
  pip install tqdm --break-system-packages
  Optional: pip install num2words  (for number expansion in TTS mode)
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    from num2words import num2words
    HAS_NUM2WORDS = True
except ImportError:
    HAS_NUM2WORDS = False

# ── constants ─────────────────────────────────────────────────────────────────
MIN_ASR_CHARS   = 2      # drop ASR samples shorter than this after cleaning
MIN_TTS_CHARS   = 3
MAX_TTS_CHARS   = 200    # most TTS models have a token length limit
MIN_WORD_COUNT  = 1      # drop single-character transcripts

# Common English abbreviation expansions (case-insensitive match)
ABBREVIATIONS = {
    r"\bMr\.":    "mister",
    r"\bMrs\.":   "missus",
    r"\bMs\.":    "miz",
    r"\bDr\.":    "doctor",
    r"\bProf\.":  "professor",
    r"\bSt\.":    "saint",
    r"\bvs\.":    "versus",
    r"\betc\.":   "et cetera",
    r"\bi\.e\.":  "that is",
    r"\be\.g\.":  "for example",
    r"\bU\.S\.":  "United States",
    r"\bU\.K\.":  "United Kingdom",
    r"\bA\.I\.":  "artificial intelligence",
    r"\bA\.D\.":  "anno domini",
    r"\bB\.C\.":  "before christ",
}

# Punctuation normalization for TTS (smart -> straight)
PUNCT_NORMALIZE = [
    ("\u2018", "'"), ("\u2019", "'"),   # smart single quotes
    ("\u201c", '"'), ("\u201d", '"'),   # smart double quotes
    ("\u2014", ", "),                   # em-dash -> comma space
    ("\u2013", "-"),                    # en-dash -> hyphen
    ("\u2026", "..."),                  # ellipsis character
    ("\u00b7", "."),                    # middle dot
]

# ─────────────────────────────────────────────────────────────────────────────


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def strip_annotations(text: str) -> str:
    """Remove [music], (laughter), {noise} style annotations."""
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\{.*?\}", "", text)
    return text


def expand_abbreviations(text: str, preserve_case: bool = False) -> str:
    for pattern, expansion in ABBREVIATIONS.items():
        if preserve_case:
            # Match the case of the expansion to the original text.
            # expansion is bound via default arg to avoid closure-over-loop-variable fragility.
            def repl(m, exp=expansion):
                matched = m.group()
                if matched.isupper():
                    return exp.upper()
                elif matched.istitle() or matched[0].isupper():
                    return exp.title()
                else:
                    return exp.lower()
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        else:
            text = re.sub(pattern, expansion, text, flags=re.IGNORECASE)
    return text


def expand_numbers(text: str, lang: str = "en") -> str:
    """
    Replace digit sequences with their word equivalents.
    Requires num2words.  Skips silently if not installed.
    """
    if not HAS_NUM2WORDS:
        return text

    def _replace(m):
        try:
            return num2words(int(m.group()), lang=lang)
        except Exception:
            return m.group()

    return re.sub(r"\b\d+\b", _replace, text)


def is_garbage(text: str) -> bool:
    """Return True if text should be rejected entirely."""
    if not text.strip():
        return True
    # After stripping, check for pure symbols/numbers
    stripped = re.sub(r"\s+", " ", text).strip()
    # Allow Hanzi/CJK characters to not be treated as garbage symbols
    if re.match(r"^[\d\s\W\u3000-\u9fff\u30a0-\u30ff\uac00-\ud7af]+$", stripped):
        # If it's pure Hanzi, it's NOT garbage
        if re.search(r"[\u3000-\u9fff\u30a0-\u30ff\uac00-\ud7af]", stripped):
            pass
        else:
            return True
    if re.search(r"https?://\S+", stripped):
        return True
    # Check word count
    words = stripped.split()
    if len(words) < MIN_WORD_COUNT:
        return True
    return False


def clean_asr(text: str, lang: str = "en", remove_punctuation: bool = False) -> str:
    """
    Clean text for ASR training.
    Output: lowercase, optional punctuation removal, collapsed whitespace.
    """
    text = strip_html(text)
    text = strip_annotations(text)
    text = expand_abbreviations(text, preserve_case=False)
    text = text.lower()
    if remove_punctuation:
        # Keep only letters, apostrophes, spaces (and CJK for Chinese/Taigi)
        if lang in ("zh", "ja", "ko", "nan"):
            # For CJK + Lomaji: keep CJK chars + basic latin + common Lomaji diacritics + apostrophe
            # \u00c0-\u017f covers most Latin-1 Supplement and Latin Extended-A (accents)
            # \u0300-\u036f covers Combining Diacritical Marks (e.g., Peh-oe-ji tone marks)
            if lang == "nan":
                # For Taigi Lomaji, preserve hyphens as they are part of the orthography
                text = re.sub(r"[^\w\u3000-\u9fff\u30a0-\u30ff\uac00-\ud7af\u00c0-\u017f\u0300-\u036f\s'\-]", " ", text)
            else:
                text = re.sub(r"[^\w\u3000-\u9fff\u30a0-\u30ff\uac00-\ud7af\u00c0-\u017f\u0300-\u036f\s']", " ", text)
        else:
            text = re.sub(r"[^a-z0-9\s']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_tts(text: str, lang: str = "en") -> str:
    """
    Clean text for TTS training.
    Output: normalized punctuation, expanded numbers, preserved casing.
    """
    text = strip_html(text)
    text = strip_annotations(text)

    # Normalize punctuation
    for src, dst in PUNCT_NORMALIZE:
        text = text.replace(src, dst)

    text = expand_abbreviations(text, preserve_case=True)
    text = expand_numbers(text, lang=lang)

    # Remove characters that TTS phonemizers can't handle
    if lang in ("zh", "ja", "ko", "nan"):
        text = re.sub(r"[^\w\s.,!?;:()\-'\u3000-\u9fff\u30a0-\u30ff\uac00-\ud7af\u00c0-\u017f\u0300-\u036f\"]+", " ", text)
    else:
        text = re.sub(r"[^\w\s.,!?;:()\-'\"]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Ensure sentence ends with punctuation (helps prosody)
    if text and text[-1] not in ".!?":
        text += "."

    return text


def main(args_list: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(
        description="Clean and normalize transcript text for ASR or TTS training.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python 2_text_cleaner.py --manifest normalized/_manifest.csv
  python 2_text_cleaner.py --manifest normalized/_manifest.csv --tts
  python 2_text_cleaner.py --manifest normalized/_manifest.csv --lang zh
        """,
    )
    parser.add_argument("--manifest", required=True,
                        help="Path to _manifest.csv from audio_normalizer")
    parser.add_argument("--tts",  action="store_true",
                        help="TTS mode: preserve casing/punctuation, expand numbers")
    parser.add_argument("--lang", default="en",
                        help="Language code for number expansion and charset (default: en)")
    parser.add_argument("--max-tts-chars", type=int, default=MAX_TTS_CHARS,
                        help=f"Drop TTS samples longer than N chars (default {MAX_TTS_CHARS})")
    parser.add_argument("--remove-punctuation", action="store_true",
                        help="Remove punctuation from ASR text (default: keep punctuation)")
    args = parser.parse_args(args_list)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        sys.exit(f"ERROR: manifest not found: {manifest_path}")

    mode     = "TTS" if args.tts else "ASR"
    out_col  = "text_tts" if args.tts else "text_asr"
    min_chars = MIN_TTS_CHARS if args.tts else MIN_ASR_CHARS

    print(f"Mode     : {mode}")
    print(f"Language : {args.lang}")
    if not HAS_NUM2WORDS and args.tts:
        print("WARNING  : num2words not installed — numbers will NOT be expanded.")
        print("           pip install num2words")

    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Input    : {len(rows)} rows\n")

    out_rows = []
    dropped  = 0
    iterator = tqdm(rows) if HAS_TQDM else rows

    for row in iterator:
        original = row.get("text", "")

        # ── 1. check garbage on original text ─────────────────────────────────
        if is_garbage(original):
            dropped += 1
            continue

        if args.tts:
            cleaned = clean_tts(original, lang=args.lang)
        else:
            cleaned = clean_asr(original, lang=args.lang, remove_punctuation=args.remove_punctuation)

        # ── 2. rejection gates on cleaned text ────────────────────────────────
        if len(cleaned) < min_chars:
            dropped += 1
            continue
        if args.tts and len(cleaned) > args.max_tts_chars:
            dropped += 1
            continue

        out_row = {**row, out_col: cleaned}
        out_rows.append(out_row)

    # ── write in-place (overwrites manifest with new column) ──────────────────
    manifest_out = manifest_path.parent / "_manifest_2_cleaned.csv"
    if out_rows:
        with open(manifest_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_rows[0].keys())
            writer.writeheader()
            writer.writerows(out_rows)

    print(f"\n{'─' * 50}")
    print(f"  Kept    : {len(out_rows)}  (column '{out_col}' added)")
    print(f"  Dropped : {dropped}  (garbage / too short / too long)")
    print(f"  Manifest: {manifest_out.name}  (written)")
    print(f"{'─' * 50}")

    metadata_out = manifest_path.parent / "metadata_2_cleaned.json"
    with open(metadata_out, "w", encoding="utf-8") as f:
        json.dump({
            "stage": 2,
            "name": "text_cleaner",
            "args": vars(args),
            "stats": {"kept": len(out_rows), "dropped": dropped}
        }, f, indent=2)

    return manifest_out


if __name__ == "__main__":
    main()
