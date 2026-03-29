"""
fetch_subtitles_fb.py — Download subtitles/captions from Facebook (and X/Twitter where available)
using yt-dlp's subtitle extraction, then normalise the output into the same SRT/CSV format
produced by fetch_subtitles.py for YouTube.

Platform notes:
  Facebook  — Auto-generated (AI) and manually uploaded captions are available on many
              page/creator videos. Personal posts and most Reels have no captions.
              Captions come from Facebook's own delivery system (not a third-party API).
  X/Twitter — No publicly accessible subtitle/caption track; this command will report
              "no subtitles available" for X URLs rather than failing silently.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is not installed. Please run 'pip install yt-dlp'.", file=sys.stderr)
    sys.exit(1)

from .root_finder import get_project_root
from .utils import is_supported_url, generate_filename, build_cookies_opts, detect_platform


# ──────────────────────────────────────────────────────────────────────────────
# SRT helpers  (mirrors fetch_subtitles.py exactly)
# ──────────────────────────────────────────────────────────────────────────────

def seconds_to_srt_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millisecs = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"


def srt_time_to_seconds(srt_time: str) -> float:
    """Parse SRT timestamp → float seconds."""
    srt_time = srt_time.strip()
    parts = srt_time.split(',')
    ms = int(parts[1]) if len(parts) > 1 else 0
    h, m, s = parts[0].split(':')
    return int(h) * 3600 + int(m) * 60 + int(s) + ms / 1000.0


# ──────────────────────────────────────────────────────────────────────────────
# SRT file parser  (reads the raw .srt yt-dlp writes to disk)
# ──────────────────────────────────────────────────────────────────────────────

def parse_srt_content(content: str) -> List[Dict]:
    """
    Parse a standard SRT string into a list of segment dicts:
      { 'index': int, 'start': float, 'end': float, 'text': str }
    Handles both simple text and VTT-style cue tags embedded in SRT (yt-dlp
    sometimes writes these for auto-generated captions).
    """
    # Strip BOM if present
    content = content.lstrip('\ufeff')

    # Remove VTT / HTML-style tags that sometimes leak into yt-dlp SRT output
    def _strip_tags(text: str) -> str:
        text = re.sub(r'<[^>]+>', '', text)          # <c>, <i>, etc.
        text = re.sub(r'\{[^}]+\}', '', text)         # {an8} positioning
        return text.strip()

    segments: List[Dict] = []
    blocks = re.split(r'\n{2,}', content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue

        # First line: optional index (integer)
        idx_line = lines[0].strip()
        if not re.match(r'^\d+$', idx_line):
            continue
        idx = int(idx_line)

        # Second line: timestamp
        time_line = lines[1]
        m = re.match(
            r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})',
            time_line
        )
        if not m:
            continue

        start = srt_time_to_seconds(m.group(1).replace('.', ','))
        end   = srt_time_to_seconds(m.group(2).replace('.', ','))
        text  = _strip_tags(' '.join(lines[2:]))

        if text:
            segments.append({'index': idx, 'start': start, 'end': end, 'text': text})

    return segments


# ──────────────────────────────────────────────────────────────────────────────
# Formatters  (identical header schema to fetch_subtitles.py)
# ──────────────────────────────────────────────────────────────────────────────

def _make_header(video_info: Dict, url: str, language: str, total: int) -> str:
    return (
        f"Title: {video_info.get('title', 'Unknown')}\n"
        f"Channel: {video_info.get('channel') or video_info.get('uploader') or 'Unknown'}\n"
        f"Video ID: {video_info.get('id', 'Unknown')}\n"
        f"URL: {url}\n"
        f"Language: {language}\n"
        f"Sync Offset: 0.000s\n"
        f"Dropped Gaps: []\n"
        f"Duration: {video_info.get('duration', 'Unknown')}s\n"
        f"Total Segments: {total}\n"
        f"{'-' * 40}\n\n"
    )


def format_as_srt(segments: List[Dict], video_info: Dict, url: str, language: str) -> str:
    header = _make_header(video_info, url, language, len(segments))
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{seconds_to_srt_time(seg['start'])} --> {seconds_to_srt_time(seg['end'])}")
        lines.append(seg['text'])
        lines.append('')
    return header + '\n'.join(lines)


def format_as_csv(segments: List[Dict], video_info: Dict, url: str, language: str) -> str:
    out = io.StringIO()
    out.write(_make_header(video_info, url, language, len(segments)))
    writer = csv.writer(out)
    writer.writerow(["Index", "Start", "End", "Text"])
    for i, seg in enumerate(segments, 1):
        writer.writerow([i, seg['start'], seg['end'], seg['text'].replace('\n', ' ')])
    return out.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Core logic
# ──────────────────────────────────────────────────────────────────────────────

def list_available_subtitles(url: str,
                              cookies_from_browser: Optional[str] = None,
                              cookies_file: Optional[str] = None) -> None:
    """
    Print all available subtitle tracks for a URL (like yt-dlp --list-subs).
    Useful for discovering which language codes to pass to --lang.
    """
    opts = {
        'skip_download': True,
        'listsubtitles': True,
        'quiet': False,
        **build_cookies_opts(cookies_from_browser, cookies_file),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=False)


def fetch_subtitles(url: str,
                    lang: str = 'en',
                    output_format: str = 'csv',
                    output_path: Optional[str] = None,
                    include_auto: bool = True,
                    cookies_from_browser: Optional[str] = None,
                    cookies_file: Optional[str] = None) -> None:
    """
    Download subtitles from a Facebook (or other yt-dlp–supported non-YouTube) URL
    and save them in the same SRT/CSV format used by fetch_subtitles.py.

    Args:
        url:                  Facebook / other video URL.
        lang:                 BCP-47 language code to prefer (e.g. 'en', 'zh-TW').
                              Partial matching is used: 'en' also matches 'en_US', 'en_GB'.
        output_format:        'srt' or 'csv'.
        output_path:          Explicit output file path (optional).
        include_auto:         Also consider auto-generated captions (default True).
        cookies_from_browser: Browser name for cookie auth.
        cookies_file:         Path to Netscape cookies.txt.
    """
    platform = detect_platform(url)

    if platform == 'twitter':
        raise ValueError(
            "X/Twitter does not expose subtitle or caption tracks. "
            "No subtitles can be downloaded for this URL."
        )

    # ── Step 1: probe available subtitles without downloading video ───────────
    probe_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        **build_cookies_opts(cookies_from_browser, cookies_file),
    }

    print(f"Probing subtitle availability for {url} …", file=sys.stderr)
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"Error fetching video info: {e}", file=sys.stderr)
            sys.exit(1)

    manual_subs = info.get('subtitles') or {}
    auto_subs   = info.get('automatic_captions') or {}

    def _find_lang_key(subs_dict: dict, lang_pref: str) -> Optional[str]:
        """
        Find the best-matching key in subs_dict for lang_pref.
        Exact match → prefix match → first available.
        """
        if not subs_dict:
            return None
        # Exact
        if lang_pref in subs_dict:
            return lang_pref
        # Prefix (e.g. 'en' matches 'en_US', 'en_GB')
        for key in subs_dict:
            if key.startswith(lang_pref):
                return key
        return None

    chosen_key    = _find_lang_key(manual_subs, lang)
    using_auto    = False
    if chosen_key is None and include_auto:
        chosen_key = _find_lang_key(auto_subs, lang)
        using_auto = chosen_key is not None

    if chosen_key is None:
        all_manual = list(manual_subs.keys())
        all_auto   = list(auto_subs.keys())

        if not all_manual and not all_auto:
            print(
                "No subtitle or caption tracks are available for this video.\n"
                "The video owner has not provided captions and auto-generation is not available.",
                file=sys.stderr,
            )
        else:
            print(f"No subtitles found for language '{lang}'.", file=sys.stderr)
            if all_manual:
                print(f"  Manual subtitles available : {all_manual}", file=sys.stderr)
            if all_auto:
                print(f"  Auto-generated available   : {all_auto}", file=sys.stderr)

        no_auto_hint = ", or remove --no-auto to include auto-generated captions" if not include_auto and auto_subs else ""
        print(f"Tip: Use --list-subs to inspect all tracks{no_auto_hint}, or pass a different --lang.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Found {'auto-generated' if using_auto else 'manual'} subtitles "
        f"for language key '{chosen_key}'.",
        file=sys.stderr
    )

    # ── Step 2: download the SRT track into a temp directory ─────────────────
    with tempfile.TemporaryDirectory() as tmp_dir:
        dl_opts = {
            'skip_download': True,
            'writesubtitles': not using_auto,
            'writeautomaticsub': using_auto,
            'subtitleslangs': [chosen_key],
            'subtitlesformat': 'srt',
            'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            **build_cookies_opts(cookies_from_browser, cookies_file),
        }

        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            try:
                ydl.download([url])
            except Exception as e:
                print(f"Error downloading subtitles: {e}", file=sys.stderr)
                sys.exit(1)

        # Locate the downloaded subtitle file and read its content inside the
        # temp dir while it still exists (avoids race if refactored later).
        srt_files = list(Path(tmp_dir).glob('*.srt'))
        if not srt_files:
            # yt-dlp sometimes writes .vtt even when srt is requested (fallback)
            srt_files = list(Path(tmp_dir).glob('*.vtt'))

        if not srt_files:
            print(
                "Subtitles were reported as available but no file was written. "
                "This can happen if the caption track is empty or DRM-protected.",
                file=sys.stderr
            )
            sys.exit(1)

        srt_path = str(srt_files[0])
        is_vtt = srt_path.endswith('.vtt')
        with open(srt_path, encoding='utf-8', errors='replace') as f:
            raw_content = f.read()

    # Parse outside the temp dir — content is already in memory
    if is_vtt:
        print(
            "Warning: subtitle track was returned as WebVTT instead of SRT; "
            "stripping VTT header and normalising timestamps.",
            file=sys.stderr
        )
        # Strip the WEBVTT header block (everything before the first double-newline
        # that follows the WEBVTT signature) so the SRT block parser can handle it.
        raw_content = re.sub(r'^WEBVTT[^\n]*\n(.*?\n)?\n', '', raw_content, flags=re.DOTALL)
        # VTT uses '.' as the millisecond separator; SRT uses ','.
        raw_content = re.sub(r'(\d{2}:\d{2}:\d{2})\.(\d{3})', r'\1,\2', raw_content)

    segments = parse_srt_content(raw_content)

    if not segments:
        print(
            "Subtitle file was downloaded but contained no parseable segments. "
            "The track may be empty or in an unsupported format.",
            file=sys.stderr
        )
        sys.exit(1)

    # ── Step 3: format and save ───────────────────────────────────────────────
    if output_format == 'srt':
        content = format_as_srt(segments, info, url, chosen_key)
        ext = 'srt'
    else:
        content = format_as_csv(segments, info, url, chosen_key)
        ext = 'csv'

    if output_path:
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        final_path = output_path
    else:
        root = get_project_root()
        out_dir = str(root / 'media-subtitles')
        os.makedirs(out_dir, exist_ok=True)
        filename  = generate_filename(info.get('title', f"video_{info.get('id', 'unknown')}"), ext)
        final_path = os.path.join(out_dir, filename)

    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(json.dumps({
        "status": "success",
        "platform": platform,
        "video_id": info.get('id'),
        "title": info.get('title'),
        "language": chosen_key,
        "auto_generated": using_auto,
        "total_segments": len(segments),
        "format": output_format,
        "output_file": final_path,
    }))


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Download subtitles/captions from Facebook (or any yt-dlp–supported URL) "
            "and save them in the same SRT/CSV format used for YouTube subtitles."
        )
    )
    parser.add_argument("url", help="Facebook (or other) video URL")
    parser.add_argument(
        "--format", choices=["srt", "csv"], default="csv",
        help="Output format (default: csv)"
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Optional output file path. Default: media-subtitles/[title]_[timestamp].[format]"
    )
    parser.add_argument(
        "--lang", default="en",
        help="Preferred language code, e.g. 'en', 'zh-TW', 'id' (default: en). "
             "Partial prefix matching is used."
    )
    parser.add_argument(
        "--no-auto", action="store_true",
        help="Skip auto-generated captions and only use manually uploaded subtitles."
    )
    parser.add_argument(
        "--list-subs", action="store_true",
        help="List all available subtitle tracks and exit (no file written)."
    )
    parser.add_argument(
        "--cookies-from-browser", metavar="BROWSER",
        help="Read cookies from this browser (chrome, firefox, edge, brave, safari, …). "
             "Required for private or login-gated content."
    )
    parser.add_argument(
        "--cookies-file", metavar="FILE",
        help="Path to a Netscape-format cookies.txt file."
    )

    args = parser.parse_args()

    if not is_supported_url(args.url):
        print(f"Error: Unrecognised URL: {args.url}", file=sys.stderr)
        sys.exit(1)

    if args.list_subs:
        list_available_subtitles(args.url, args.cookies_from_browser, args.cookies_file)
        return

    try:
        fetch_subtitles(
            url=args.url,
            lang=args.lang,
            output_format=args.format,
            output_path=args.output,
            include_auto=not args.no_auto,
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies_file,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
