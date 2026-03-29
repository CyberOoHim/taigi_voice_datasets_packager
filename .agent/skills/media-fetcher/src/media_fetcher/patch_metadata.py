import argparse
import json
import os
import re
import sys
from pathlib import Path

from .fetch_subtitles import extract_video_id, get_video_info

try:
    import yt_dlp
    _YT_DLP_AVAILABLE = True
except ImportError:
    _YT_DLP_AVAILABLE = False


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r'youtube\.com|youtu\.be', url, re.IGNORECASE))


def _get_generic_video_info(url: str) -> dict:
    """Fetch title/channel/duration for any yt-dlp-supported URL."""
    if not _YT_DLP_AVAILABLE:
        print(
            "Warning: yt-dlp is not installed; cannot refresh metadata for non-YouTube URLs.",
            file=sys.stderr,
        )
        return {}
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'extract_flat': False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Unknown'),
                'channel': info.get('channel') or info.get('uploader') or 'Unknown',
                'duration': info.get('duration'),
                'id': info.get('id', 'Unknown'),
            }
    except Exception as e:
        print(f"Warning: could not fetch metadata for {url}: {e}", file=sys.stderr)
        return {}

def count_srt_segments(content: str) -> int:
    return content.count('-->')

def count_csv_segments(content: str) -> int:
    lines = content.strip().split('\n')
    # If there is no 'Index,' header in the content, count all non-empty lines.
    # Otherwise, count lines after the 'Index,' header.
    if not any(line.startswith('Index,') for line in lines):
        return sum(1 for line in lines if line.strip())
        
    count = 0
    started = False
    for line in lines:
        if started:
            if line.strip():
                count += 1
        elif line.startswith('Index,'):
            started = True
    return count

def extract_language(header_text: str) -> str:
    match = re.search(r'^Language:\s*(.+)$', header_text, re.MULTILINE)
    return match.group(1).strip() if match else 'Unknown'

def extract_sync_offset(header_text: str) -> str:
    match = re.search(r'^Sync Offset:\s*(.+)$', header_text, re.MULTILINE)
    return match.group(1).strip() if match else '0.000s'

def extract_dropped_gaps(header_text: str) -> str:
    match = re.search(r'^Dropped Gaps:\s*(.+)$', header_text, re.MULTILINE)
    return match.group(1).strip() if match else '[]'

def patch_file(file_path: str, url_arg: str = None):
    if not os.path.isfile(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    is_csv = file_path.lower().endswith('.csv')
    is_srt = file_path.lower().endswith('.srt')
    
    if not (is_csv or is_srt):
        print(f"Error: Unsupported file extension. Please provide a .srt or .csv file.", file=sys.stderr)
        sys.exit(1)

    body = ""
    old_header = ""
    
    if is_srt:
        # Find where the actual SRT blocks start. Look for "1" followed by a timestamp line.
        match = re.search(r'(?:\A|\n)(\d+\r?\n\d{2}:\d{2}:\d{2},\d{3}\s*-->)', content)
        if match:
            split_idx = match.start(1)
            old_header = content[:split_idx]
            body = content[split_idx:]
        else:
            body = content
    elif is_csv:
        match = re.search(r'(?:\A|\n)(Index,Start,End,Text)', content, re.IGNORECASE)
        if match:
            split_idx = match.start(1)
            old_header = content[:split_idx]
            body = content[split_idx:]
            if body.startswith('\n'):
                body = body[1:]
        else:
            body = content

    url = url_arg
    if not url:
        # Strategy 1: first line is explicitly a URL line
        first_line = content.split('\n', 1)[0].strip()
        url_line_match = re.match(r'^URL:\s*(.+)$', first_line, re.IGNORECASE)
        if url_line_match:
            url = url_line_match.group(1).strip()
        else:
            # Strategy 2: look in the extracted header block
            url_match = re.search(r'^URL:\s*(.+)$', old_header, re.IGNORECASE | re.MULTILINE)
            if url_match:
                url = url_match.group(1).strip()

    if not url:
        print(
            "Error: Could not find a URL in the file header, and no --url argument was provided.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Fetch fresh metadata depending on platform ────────────────────────────
    if _is_youtube_url(url):
        video_id = extract_video_id(url)
        if not video_id:
            print(f"Error: Could not extract YouTube video ID from URL: {url}", file=sys.stderr)
            sys.exit(1)
        print(f"Fetching YouTube metadata for {video_id}...", file=sys.stderr)
        raw = get_video_info(video_id)
        video_info = {
            'title': raw.get('title', f'YouTube Video {video_id}'),
            'channel': raw.get('channel', 'Unknown'),
            'duration': raw.get('duration', 'Unknown'),
            'id': video_id,
        }
        final_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        print(f"Fetching metadata via yt-dlp for {url}...", file=sys.stderr)
        video_info = _get_generic_video_info(url)
        if not video_info:
            # Graceful degradation: keep the old header values
            print(
                "Warning: metadata fetch failed; preserving existing title/channel/duration.",
                file=sys.stderr,
            )
            video_info = {
                'title': re.search(r'^Title:\s*(.+)$', old_header, re.MULTILINE).group(1).strip()
                    if re.search(r'^Title:\s*(.+)$', old_header, re.MULTILINE) else 'Unknown',
                'channel': re.search(r'^Channel:\s*(.+)$', old_header, re.MULTILINE).group(1).strip()
                    if re.search(r'^Channel:\s*(.+)$', old_header, re.MULTILINE) else 'Unknown',
                'duration': re.search(r'^Duration:\s*(\S+)', old_header, re.MULTILINE).group(1).strip().rstrip('s')
                    if re.search(r'^Duration:\s*(\S+)', old_header, re.MULTILINE) else 'Unknown',
                'id': video_info.get('id', 'Unknown'),
            }
        final_url = url
    
    language = extract_language(old_header)
    sync_offset = extract_sync_offset(old_header)

    total_segments = count_csv_segments(body) if is_csv else count_srt_segments(body)

    vid_id = video_info.get('id', 'Unknown')
    new_header = f"Title: {video_info.get('title', 'Unknown')}\n"
    new_header += f"Channel: {video_info.get('channel', 'Unknown')}\n"
    new_header += f"Video ID: {vid_id}\n"
    new_header += f"URL: {final_url}\n"
    new_header += f"Language: {language}\n"
    new_header += f"Sync Offset: {sync_offset}\n"
    new_header += f"Dropped Gaps: {extract_dropped_gaps(old_header)}\n"
    new_header += f"Duration: {video_info.get('duration', 'Unknown')}s\n"
    new_header += f"Total Segments: {total_segments}\n"

    if is_srt:
        new_header += "-" * 40 + "\n\n"
    else:
        # For CSV, if the body doesn't start with Index line, we should probably add it, 
        # but our extraction logic implies it does if it was properly formatted.
        if not body.lower().startswith('index,'):
            new_header += "Index,Start,End,Text\n"

    # Ensure no leading newlines in body if it's supposed to append directly
    final_content = new_header + body
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(final_content)
        
    print(f"Successfully patched metadata in {file_path}")

def main():
    parser = argparse.ArgumentParser(description="Patch metadata header into an existing .srt or .csv file.")
    parser.add_argument("file", help="Path to the .srt or .csv file")
    parser.add_argument("--url", help="YouTube URL (overrides URL in the file)")
    args = parser.parse_args()

    patch_file(args.file, args.url)

if __name__ == "__main__":
    main()