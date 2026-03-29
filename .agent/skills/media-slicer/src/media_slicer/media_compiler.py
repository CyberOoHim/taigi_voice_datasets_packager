import argparse
import csv
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Dict, Any

from .srt_clipper import (
    check_dependencies,
    configure_logging,
    fmt,
    is_audio_only,
    load_srt,
    output_ext,
    probe_all_keyframes,
    probe_media_duration,
    sec,
    snap_to_keyframe,
    extract_srt_metadata,
)

# Per-block ffmpeg timeout (mirrors CLIP_TIMEOUT in srt_clipper)
CLIP_TIMEOUT = 600  # seconds; blocks may span many minutes

log = logging.getLogger("srt_clipper")

def load_subtitle_items(subs_path: str, encoding: str | None = None) -> List[Dict[str, Any]]:
    """Load subtitle items (index, start, end, text) from SRT or CSV."""
    items = []

    if subs_path.lower().endswith('.csv'):
        encodings = (encoding,) if encoding else ("utf-8-sig", "utf-8", "latin-1")
        for enc in encodings:
            try:
                with open(subs_path, 'r', encoding=enc) as f:
                    lines = f.readlines()
                start_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('Index,Start'):
                        start_idx = i
                        break

                reader = csv.DictReader(lines[start_idx:])
                for row_num, row in enumerate(reader, start=1):
                    try:
                        items.append({
                            'Index': row.get('Index', str(row_num)),
                            'Start': float(row.get('Start', 0)),
                            'End': float(row.get('End', 0)),
                            'Text': row.get('Text', '')
                        })
                    except (KeyError, ValueError) as row_err:
                        log.warning(
                            "Skipping malformed CSV row %d in '%s': %s",
                            row_num, subs_path, row_err,
                        )
                return items
            except UnicodeDecodeError:
                continue
        sys.exit(f"ERROR: could not decode or parse CSV '{subs_path}'.")
    else:
        # Parse SRT
        import pysrt
        subs = list(load_srt(subs_path, encoding_override=encoding))
        for sub in subs:
            items.append({
                'Index': str(sub.index),
                'Start': sec(sub.start),
                'End': sec(sub.end),
                'Text': sub.text
            })

    return items

def write_resynced_subtitles(
    output_path: str,
    items: List[Dict[str, Any]],
    blocks: List[Tuple[float, float]],
    original_metadata: Dict[str, str]
):
    """Write resynced subtitles for concatenated blocks, accumulating Sync Offset and Dropped Gaps."""
    is_csv = output_path.lower().endswith('.csv')
    
    old_sync_str = original_metadata.get("Sync Offset", "0.000s")
    try:
        old_sync = float(old_sync_str.replace("s", ""))
    except ValueError:
        old_sync = 0.0

    old_gaps_str = original_metadata.get("Dropped Gaps", "[]")
    try:
        old_gaps = json.loads(old_gaps_str)
    except json.JSONDecodeError:
        old_gaps = []

    def map_time_to_original(local_t: float) -> float:
        """Map a local (already-trimmed) time to its absolute original-video time."""
        t_orig = local_t + old_sync
        # Defensive sort: the spec guarantees sorted gaps, but protect against
        # hand-edited headers where order may differ.
        for gap in sorted(old_gaps, key=lambda g: g[0]):
            gap_start = gap[0]
            gap_dur = gap[1]
            if gap_start <= t_orig:
                t_orig += gap_dur
        return t_orig

    # Calculate block offsets in the output media
    block_offsets = []
    current_offset = 0.0
    for b_start, b_end in blocks:
        block_offsets.append(current_offset)
        current_offset += (b_end - b_start)

    # 1. New Sync Offset is the original time of the very first block's start
    new_sync = map_time_to_original(blocks[0][0]) if blocks else old_sync

    # 2. Calculate newly dropped gaps between blocks and map them to absolute time
    new_gaps = []
    # If the first block doesn't start at 0, that's just a shift in Sync Offset, not a gap.
    # Gaps are the spaces *between* blocks.
    for i in range(len(blocks) - 1):
        gap_local_start = blocks[i][1]
        gap_local_end = blocks[i+1][0]
        gap_dur = gap_local_end - gap_local_start
        if gap_dur > 0:
            abs_gap_start = map_time_to_original(gap_local_start)
            new_gaps.append([abs_gap_start, gap_dur])

    # 3. Merge old and new gaps
    all_gaps = old_gaps + new_gaps
    # Sort by start time
    all_gaps.sort(key=lambda x: x[0])
    
    # 4. Filter out any gaps that occur entirely before our new starting point
    # Note: if a gap overlaps our start point, we technically started *inside* a gap,
    # which shouldn't happen if we're starting at a block boundary. But if it did, 
    # we'd just keep the remainder.
    filtered_gaps = []
    for gap in all_gaps:
        gap_start, gap_dur = gap
        if gap_start + gap_dur <= new_sync:
            continue
        if gap_start < new_sync:
            overlap = new_sync - gap_start
            filtered_gaps.append([new_sync, gap_dur - overlap])
        else:
            filtered_gaps.append(gap)

    # Update metadata
    meta = original_metadata.copy()
    meta["Sync Offset"] = f"{new_sync:.3f}s"
    # Compact JSON representation without extra spaces
    meta["Dropped Gaps"] = json.dumps(filtered_gaps, separators=(',', ':'))
    meta["Total Segments"] = str(len(items))
    
    header_lines = []
    for k, v in meta.items():
        header_lines.append(f"{k}: {v}")
    
    header_text = "\n".join(header_lines)
    
    with open(output_path, 'w', encoding='utf-8', newline='' if is_csv else None) as f:
        if is_csv:
            f.write(header_text + "\n")
            writer = csv.DictWriter(f, fieldnames=["Index", "Start", "End", "Text"])
            writer.writeheader()
            for item in items:
                # Find which block this item belongs to
                new_start = None
                new_end = None
                for i, (b_start, b_end) in enumerate(blocks):
                    # Use a small epsilon for float comparison
                    if b_start - 0.001 <= item['Start'] < b_end + 0.001:
                        new_start = block_offsets[i] + (item['Start'] - b_start)
                        new_end = new_start + (item['End'] - item['Start'])
                        break
                
                if new_start is not None:
                    writer.writerow({
                        "Index": item['Index'],
                        "Start": f"{new_start:.3f}",
                        "End": f"{new_end:.3f}",
                        "Text": item['Text']
                    })
        else:
            f.write(header_text + "\n" + "-" * 40 + "\n\n")
            for idx, item in enumerate(items, 1):
                new_start = None
                new_end = None
                for i, (b_start, b_end) in enumerate(blocks):
                    if b_start - 0.001 <= item['Start'] < b_end + 0.001:
                        new_start = block_offsets[i] + (item['Start'] - b_start)
                        new_end = new_start + (item['End'] - item['Start'])
                        break
                
                if new_start is not None:
                    from .srt_clipper import dur_str
                    # Convert seconds back to SRT format
                    def srt_fmt(s):
                        h = int(s // 3600)
                        m = int((s % 3600) // 60)
                        sec = s % 60
                        return f"{h:02d}:{m:02d}:{sec:06.3f}".replace('.', ',')
                    
                    f.write(f"{idx}\n{srt_fmt(new_start)} --> {srt_fmt(new_end)}\n{item['Text']}\n\n")

def load_intervals(subs_path: str, encoding: str | None = None) -> List[Tuple[float, float]]:
    items = load_subtitle_items(subs_path, encoding)
    return [(item['Start'], item['End']) for item in items]

def compute_blocks(
    intervals: List[Tuple[float, float]],
    merge_gap: float,
    head_pad: float,
    tail_pad: float,
    media_dur: float
) -> List[Tuple[float, float]]:
# ... (rest of compute_blocks remains the same)
    if not intervals:
        return []

    # Sort by start time
    intervals.sort(key=lambda x: x[0])

    # Pass 1: Merge intervals where gap <= merge_gap.
    # Overlapping inputs (e.g. dual-language SRTs) are handled here: when
    # start < prev_end the block is simply extended via max(prev_end, end).
    merged = []
    for start, end in intervals:
        if not merged:
            merged.append([start, end])
        else:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                # Overlapping cues — extend rather than add a new block
                log.debug(
                    "Overlapping cues at %.3f–%.3f and %.3f–%.3f; merging.",
                    prev_start, prev_end, start, end,
                )
                merged[-1][1] = max(prev_end, end)
            elif start - prev_end <= merge_gap:
                merged[-1][1] = max(prev_end, end)
            else:
                merged.append([start, end])

    # Pass 2: Apply padding
    padded = []
    for start, end in merged:
        padded.append([
            max(0.0, start - head_pad),
            min(media_dur, end + tail_pad)
        ])

    # Pass 3: Merge overlapping padded blocks
    final_blocks = []
    for start, end in padded:
        if not final_blocks:
            final_blocks.append([start, end])
        else:
            prev_start, prev_end = final_blocks[-1]
            if start <= prev_end:  # Overlap or touch after padding
                final_blocks[-1][1] = max(prev_end, end)
            else:
                final_blocks.append([start, end])

    # Return as tuples
    return [(b[0], b[1]) for b in final_blocks]

@functools.lru_cache(maxsize=1)
def _get_video_codec() -> tuple[str, list[str]]:
    """Auto-detect NVIDIA GPU to use NVENC and return (codec, [extra_args])."""
    try:
        import torch
        if torch.cuda.is_available():
            log.info("Using NVIDIA GPU (NVENC) for video encoding.")
            # NVENC uses -cq for CRF-like behavior and p6 for slower/high quality
            return "h264_nvenc", ["-cq", "18", "-rc", "vbr", "-preset", "p6"]
    except ImportError:
        pass
    log.info("Using CPU (libx264) for video encoding.")
    return "libx264", ["-crf", "18", "-preset", "veryfast"]


def extract_block(
    input_path: str,
    start: float,
    end: float,
    out_path: str,
    reencode: bool,
    audio_only: bool,
    crf: int = 18,
    preset: str = "veryfast"
) -> None:
# ... (rest of extract_block remains the same)
    """Extract a single block from the source file into out_path."""
    dur = end - start
    # fmt() is imported from srt_clipper — not redefined here.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", fmt(start),
        "-i", input_path,
        "-t", f"{dur:.3f}"
    ]

    if audio_only:
        if reencode:
            ext = Path(out_path).suffix.lower()
            if ext == ".mp3":
                cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
            elif ext == ".wav":
                cmd += ["-c:a", "pcm_s16le"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-c", "copy"]
    else:
        if reencode:
            vcodec, vargs = _get_video_codec()
            
            # Override crf/preset if libx264 is used
            if vcodec == "libx264":
                vargs = ["-crf", str(crf), "-preset", preset]
                
            cmd += [
                "-c:v", vcodec, *vargs,
                "-c:a", "aac", "-b:a", "192k"
            ]
        else:
            cmd += ["-c", "copy"]

    cmd.append(out_path)
    subprocess.run(cmd, check=True, timeout=CLIP_TIMEOUT)

def parse_args() -> argparse.Namespace:
# ... (rest of parse_args remains the same)
    parser = argparse.ArgumentParser(
        description="Compile a media file based on an edited SRT/CSV subtitle file."
    )
    parser.add_argument("--input", required=True, help="Input video or audio file")
    parser.add_argument("--subs", required=True, help="Input .srt or .csv subtitle file")
    parser.add_argument("--out", help="Output compiled media file path. Defaults to <input_stem>_compiled.<ext>")
    parser.add_argument("--head-pad", type=float, default=0.5, help="Lead-in padding (seconds) for blocks (default 0.5)")
    parser.add_argument("--tail-pad", type=float, default=0.5, help="Tail-out padding (seconds) for blocks (default 0.5)")
    parser.add_argument("--merge-gap", type=float, default=1.5, help="Merge continuous cues if gap is <= this (seconds) (default 1.5)")
    parser.add_argument("--reencode", action=argparse.BooleanOptionalAction, default=True, help="Re-encode segments for perfect concatenation cuts (default: True, use --no-reencode to disable)")
    parser.add_argument("--crf", type=int, default=18, help="Video re-encode quality CRF (default 18)")
    parser.add_argument("--preset", default="veryfast", help="Video re-encode preset (default veryfast)")
    parser.add_argument("--encoding", default=None, help="Force a specific subtitle encoding (e.g. latin-1)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output")
    return parser.parse_args()

def main():
    check_dependencies()
    args = parse_args()
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    if not os.path.isfile(args.input):
        sys.exit(f"ERROR: input file not found: {args.input}")
    if not os.path.isfile(args.subs):
        sys.exit(f"ERROR: subtitle file not found: {args.subs}")

    ext = output_ext(args.input)

    if args.out:
        out_path = Path(args.out)
    else:
        input_path = Path(args.input)
        out_path = input_path.parent / f"{input_path.stem}_compiled{ext}"

    # Force out_path to have the correct extension (computed once above).
    if out_path.suffix.lower() != ext:
        log.warning("Changing output extension to %s to match source format.", ext)
        out_path = out_path.with_suffix(ext)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio_only = is_audio_only(args.input)
    log.info("Input    : %s [%s]", args.input, "audio-only" if audio_only else "video+audio")
    log.info("Subs     : %s", args.subs)
    log.info("Output   : %s", out_path)  # log resolved path, not args.out (may be None)

    media_dur = probe_media_duration(args.input)
    items = load_subtitle_items(args.subs, args.encoding)
    intervals = [(item['Start'], item['End']) for item in items]

    if not intervals:
        sys.exit("No cues found in the subtitle file.")

    blocks = compute_blocks(intervals, args.merge_gap, args.head_pad, args.tail_pad, media_dur)
    log.info("Computed %d continuous blocks from %d cues.", len(blocks), len(intervals))

    all_keyframes = []
    if not audio_only and not args.reencode:
        log.info("Probing keyframes for snap-to-keyframe stream copy...")
        all_keyframes = probe_all_keyframes(args.input)

    temp_dir = Path(tempfile.mkdtemp(prefix="media_compiler_"))
    log.debug("Using temp dir: %s", temp_dir)
    
    try:
        from tqdm import tqdm
        HAS_TQDM = True
    except ImportError:
        HAS_TQDM = False

    use_tqdm = HAS_TQDM and not args.quiet
    iterator = tqdm(enumerate(blocks), total=len(blocks), unit="block", desc="Compiling") if use_tqdm else enumerate(blocks)

    try:
        temp_files = []
        final_blocks = []
        for i, (start, end) in iterator:
            if not audio_only and not args.reencode and all_keyframes:
                # Snap to keyframe to avoid broken stream copies
                start = snap_to_keyframe(all_keyframes, start, max_snap=args.head_pad + 0.5)
            
            final_blocks.append((start, end))
            block_file = temp_dir / f"block_{i:04d}{ext}"
            if not use_tqdm:
                log.info("Extracting block %d/%d (%.2f -> %.2f)...", i+1, len(blocks), start, end)
            extract_block(
                input_path=args.input,
                start=start,
                end=end,
                out_path=str(block_file),
                reencode=args.reencode,
                audio_only=audio_only,
                crf=args.crf,
                preset=args.preset
            )
            temp_files.append(block_file)
            
        # Concat
        log.info("Concatenating %d blocks...", len(temp_files))
        concat_list_path = temp_dir / "concat.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for tf in temp_files:
                # Ffmpeg requires forward slashes or escaped backslashes and single quotes for safe handling
                safe_path = str(tf.absolute()).replace('\\', '/')
                f.write(f"file '{safe_path}'\n")
                
        concat_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",
            str(out_path)
        ]
        
        subprocess.run(concat_cmd, check=True)
        log.info("Successfully created compiled media: %s", out_path)

        # ── generate resynced subtitles ──────────────────────────────────
        resync_subs_path = out_path.with_name(f"resync_{out_path.stem}{Path(args.subs).suffix}")
        
        original_metadata = extract_srt_metadata(args.subs, args.encoding)
        write_resynced_subtitles(str(resync_subs_path), items, final_blocks, original_metadata)
        log.info("Resynced subtitles -> %s", resync_subs_path)

        # ── write metadata.json ───────────────────────────────────────────
        meta_path = out_path.with_name(f"{out_path.stem}_metadata.json")
        meta = vars(args).copy()
        for k, v in meta.items():
            if isinstance(v, Path):
                meta[k] = str(v)
        
        # Inject subtitle metadata with updated Sync Offset and Dropped Gaps
        srt_meta = original_metadata.copy()
        # Get the same updated metadata that was written to the subtitle file
        with open(resync_subs_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            if line.startswith("Sync Offset:"):
                srt_meta["Sync Offset"] = line.split(":", 1)[1].strip()
            elif line.startswith("Dropped Gaps:"):
                srt_meta["Dropped Gaps"] = line.split(":", 1)[1].strip()
            elif line == "----------------------------------------" or line == "1" or line.startswith("Index,"):
                break
                
        meta["subtitle_metadata"] = srt_meta
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4)
        log.info("Metadata -> %s", meta_path)
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
