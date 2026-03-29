import argparse
import csv
import io
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    TranslationLanguageNotAvailable,
)

from pathlib import Path

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False

from .root_finder import get_project_root
from .utils import extract_video_id, generate_filename


def get_video_info(video_id: str) -> Dict[str, str]:
    if not YT_DLP_AVAILABLE:
        return {'title': f'YouTube Video {video_id}', 'channel': None, 'duration': None}
    
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
            return {
                'title': info.get('title', f'YouTube Video {video_id}'),
                'channel': info.get('channel', info.get('uploader')),
                'duration': info.get('duration')
            }
    except Exception as e:
        # Include the exception type so users can distinguish network errors
        # from auth/access errors.
        print(
            f"Warning: {type(e).__name__} while fetching video info for {video_id}: {e}",
            file=sys.stderr,
        )
        return {'title': f'YouTube Video {video_id}', 'channel': None, 'duration': None}

def seconds_to_srt_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millisecs = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def format_as_srt(segments: List[Dict], video_info: Dict, video_id: str, language: str, url: str) -> str:
    header = f"Title: {video_info.get('title', f'YouTube Video {video_id}')}\n"
    header += f"Channel: {video_info.get('channel', 'Unknown')}\n"
    header += f"Video ID: {video_id}\n"
    header += f"URL: {url}\n"
    header += f"Language: {language}\n"
    header += f"Sync Offset: 0.000s\n"
    header += f"Dropped Gaps: []\n"
    header += f"Duration: {video_info.get('duration', 'Unknown')}s\n"
    header += f"Total Segments: {len(segments)}\n"
    header += "-" * 40 + "\n\n"
    
    srt_lines = []
    for i, seg in enumerate(segments, 1):
        start_time = seconds_to_srt_time(seg['start'])
        end_time = seconds_to_srt_time(seg['start'] + seg['duration'])
        srt_lines.append(f"{i}")
        srt_lines.append(f"{start_time} --> {end_time}")
        srt_lines.append(seg['text'])
        srt_lines.append("")
    
    return header + '\n'.join(srt_lines)

def format_as_csv(segments: List[Dict], video_info: Dict, video_id: str, language: str, url: str) -> str:
    output = io.StringIO()
    output.write(f"Title: {video_info.get('title', f'YouTube Video {video_id}')}\n")
    output.write(f"Channel: {video_info.get('channel', 'Unknown')}\n")
    output.write(f"Video ID: {video_id}\n")
    output.write(f"URL: {url}\n")
    output.write(f"Language: {language}\n")
    output.write(f"Sync Offset: 0.000s\n")
    output.write(f"Dropped Gaps: []\n")
    output.write(f"Duration: {video_info.get('duration', 'Unknown')}s\n")
    output.write(f"Total Segments: {len(segments)}\n")
    
    writer = csv.writer(output)
    writer.writerow(["Index", "Start", "End", "Text"])
    for i, seg in enumerate(segments, 1):
        text = seg['text'].replace('\n', ' ')
        writer.writerow([i, seg['start'], seg['start'] + seg['duration'], text])
    return output.getvalue()

def main():
    parser = argparse.ArgumentParser(description="Fetch subtitles from a YouTube video.")
    parser.add_argument("url", help="YouTube video URL or ID")
    parser.add_argument("--format", choices=["srt", "csv"], default="csv", help="Output format (srt or csv)")
    parser.add_argument("--output", help="Optional output file path. Defaults to media-subtitles/[title_words]_[time].[format]")
    parser.add_argument("--lang", default="en", help="Language code (default: en)")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    if not video_id:
        print("Error: Could not extract video ID from URL.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching metadata for {video_id}...", file=sys.stderr)
    video_info = get_video_info(video_id)

    print(f"Fetching transcript for {video_id}...", file=sys.stderr)
    try:
        api = YouTubeTranscriptApi()
        fetched_transcript = api.fetch(video_id, languages=[args.lang])
        transcript_data = fetched_transcript.to_raw_data()

        # Process segments to fix overlapping times (common in YouTube auto-generated transcripts)
        segments = []
        for i, seg in enumerate(transcript_data):
            start = seg['start']
            duration = seg['duration']

            # If there's a next segment, clamp the duration so it doesn't overlap
            if i < len(transcript_data) - 1:
                next_start = transcript_data[i+1]['start']
                if start + duration > next_start:
                    duration = max(0.0, next_start - start)

            segments.append({
                'text': seg['text'],
                'start': start,
                'duration': duration
            })

    except VideoUnavailable:
        print(
            f"Error: Video '{video_id}' is unavailable. "
            "It may be private, age-restricted, or deleted.",
            file=sys.stderr,
        )
        sys.exit(1)
    except TranscriptsDisabled:
        print(
            f"Error: Subtitles are disabled for video '{video_id}'.\n"
            "Tip: If this video has embedded captions (not a YouTube transcript), "
            "try 'python -m media_fetcher.fetch_subtitles_fb' instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    except (NoTranscriptFound, TranslationLanguageNotAvailable) as e:
        # Try to list what IS available so the user can pick the right --lang.
        try:
            available = api.list(video_id)
            manual_langs = [t.language_code for t in available if not t.is_generated]
            auto_langs   = [t.language_code for t in available if t.is_generated]
        except Exception:
            manual_langs, auto_langs = [], []

        print(
            f"Error: No transcript found for language '{args.lang}' on video '{video_id}'.",
            file=sys.stderr,
        )
        if manual_langs:
            print(f"  Manual subtitles available : {manual_langs}", file=sys.stderr)
        if auto_langs:
            print(f"  Auto-generated available   : {auto_langs}", file=sys.stderr)
        if not manual_langs and not auto_langs:
            print("  No subtitle tracks found for this video at all.", file=sys.stderr)
        print(
            "Tip: Re-run with a different --lang (e.g. --lang zh-TW) "
            "or use 'python -m media_fetcher.fetch_subtitles_fb' for non-YouTube sources.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error ({type(e).__name__}): {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "srt":
        formatted_text = format_as_srt(segments, video_info, video_id, fetched_transcript.language_code, args.url)
        ext = "srt"
    else:
        formatted_text = format_as_csv(segments, video_info, video_id, fetched_transcript.language_code, args.url)
        ext = "csv"

    if args.output:
        output_path = args.output
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    else:
        root = get_project_root()
        output_dir = root / "media-subtitles"
        os.makedirs(output_dir, exist_ok=True)
        filename = generate_filename(video_info.get("title", f"YouTube_Video_{video_id}"), ext)
        output_path = os.path.join(output_dir, filename)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(formatted_text)
        print(json.dumps({
            "status": "success",
            "video_id": video_id,
            "title": video_info.get("title"),
            "format": args.format,
            "output_file": output_path
        }))
    except IOError as e:
        print(f"Error saving file: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()