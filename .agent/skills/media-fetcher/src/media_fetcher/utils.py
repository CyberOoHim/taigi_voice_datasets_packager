"""Shared utilities used across media_fetcher modules."""
import re
import os
import glob
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal

# Supported platform identifiers
Platform = Literal["youtube", "facebook", "twitter", "unknown"]


def detect_platform(url: str) -> Platform:
    """
    Identify which platform a URL belongs to.
    Returns one of: "youtube", "facebook", "twitter", "unknown".
    """
    url_lower = url.lower()
    if any(d in url_lower for d in ("youtube.com", "youtu.be", "music.youtube.com")):
        return "youtube"
    if any(d in url_lower for d in ("facebook.com", "fb.watch", "fb.com")):
        return "facebook"
    if any(d in url_lower for d in ("twitter.com", "x.com", "t.co")):
        return "twitter"
    return "unknown"


def extract_video_id(url: str) -> Optional[str]:
    """
    Extract a video ID from a URL or bare ID string.

    - YouTube: returns the 11-character video ID (as before).
    - Facebook: returns the numeric video ID embedded in the URL.
    - X/Twitter: returns the numeric tweet/status ID.
    - Unknown URLs: returns None (but callers should still attempt download
      via yt-dlp, which supports many more platforms generically).
    """
    # ── YouTube ──────────────────────────────────────────────────────────────
    yt_patterns = [
        r'(?:youtube\.com|youtu\.be|m\.youtube\.com)/watch\?v=([0-9A-Za-z_-]{11})',
        r'(?:youtube\.com|youtu\.be)/embed/([0-9A-Za-z_-]{11})',
        r'youtu\.be/([0-9A-Za-z_-]{11})',
        r'youtube\.com/shorts/([0-9A-Za-z_-]{11})',
        r'music\.youtube\.com/watch\?v=([0-9A-Za-z_-]{11})',
    ]
    for pattern in yt_patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)

    # Generic v= query-string fallback (YouTube)
    if 'v=' in url and ('youtube' in url or 'youtu.be' in url):
        param_pairs = url.split('?')[1].split('&') if '?' in url else []
        for pair in param_pairs:
            if pair.startswith('v='):
                video_id = pair[2:].split('&')[0]
                if len(video_id) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', video_id):
                    return video_id

    # Bare 11-character YouTube ID
    if len(url) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url):
        return url

    # ── Facebook ─────────────────────────────────────────────────────────────
    # Handles patterns like:
    #   facebook.com/username/videos/123456789
    #   facebook.com/video/embed?video_id=123456789
    #   fb.watch/AbCdEf123/          (short links — return the slug)
    fb_patterns = [
        r'facebook\.com/(?:[^/]+/videos|video/embed\?video_id=|watch\?v=)/?(\d+)',
        r'facebook\.com/reel/(\d+)',
        r'facebook\.com/story\.php\?story_fbid=(\d+)',
        r'fb\.watch/([A-Za-z0-9_-]+)',   # short link slug
        r'fb\.com/(?:[^/]+/videos)/?(\d+)',
    ]
    for pattern in fb_patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)

    # ── X / Twitter ──────────────────────────────────────────────────────────
    # Handles patterns like:
    #   twitter.com/user/status/1234567890123456789
    #   x.com/user/status/1234567890123456789
    tw_patterns = [
        r'(?:twitter|x)\.com/[^/]+/status/(\d+)',
        r't\.co/([A-Za-z0-9]+)',          # short link — return slug
    ]
    for pattern in tw_patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)

    return None


def is_supported_url(url: str) -> bool:
    """
    Return True if the URL is a recognised platform URL OR any generic http(s)
    URL that yt-dlp may be able to handle.

    Note: bare YouTube video IDs (11 chars) are intentionally NOT accepted here.
    Only full http(s) URLs are valid — this prevents bare IDs from being
    misrouted into the URL download branch instead of the local-file branch.
    """
    if not re.match(r'https?://', url):
        return False
    # Platform-recognised URLs return True immediately
    if extract_video_id(url) is not None:
        return True
    # Let yt-dlp attempt any remaining http(s) URL as a generic extractor
    return True


def generate_filename(title: str, ext: str) -> str:
    """Build a filename from the first 4 title words + Taipei timestamp."""
    words = re.sub(r'[^\w\s]', '', title).split()
    head_words = "_".join(words[:4]) or "media"  # fallback for empty/all-punct titles

    taipei_tz = timezone(timedelta(hours=8))
    taipei_time = datetime.now(taipei_tz).strftime("%Y%m%d_%H%M%S")

    return f"{head_words}_{taipei_time}.{ext}"


def find_downloaded_file(output_dir: str, expected_ext: str) -> Optional[str]:
    """
    Scan *output_dir* for the most recently modified file that ends with
    *expected_ext* (e.g. '.mp3').  Returns the absolute path or None.

    This is the safe alternative to constructing `prepare_filename()` + extension
    replacement, which breaks when yt-dlp sanitises characters in the title.
    """
    pattern = os.path.join(output_dir, f"*{expected_ext}")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def build_cookies_opts(cookies_from_browser: Optional[str] = None,
                       cookies_file: Optional[str] = None) -> dict:
    """
    Return a dict of yt-dlp options for cookie-based authentication.

    Priority: cookies_from_browser > cookies_file > nothing.

    Args:
        cookies_from_browser: Browser name string, e.g. "chrome", "firefox",
                              "edge", "brave", "safari", "chromium", "vivaldi".
        cookies_file:         Path to a Netscape-format cookies.txt file.
    """
    if cookies_from_browser:
        return {"cookiesfrombrowser": (cookies_from_browser,)}
    if cookies_file:
        return {"cookiefile": cookies_file}
    return {}


def get_url_from_subs(filepath: str) -> Optional[str]:
    """Parse the metadata header of a .srt or .csv file to extract the video URL.
    Safely skips leading empty lines.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('URL:'):
                    return line[4:].strip()
                if line.startswith('----------------------------------------'):
                    break  # End of metadata block
    except Exception:
        pass
    return None


def _srt_time_to_seconds(time_str: str) -> float:
    parts = time_str.replace(',', '.').split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def get_time_range_from_subs(filepath: str) -> tuple[Optional[float], Optional[float]]:
    """Parse the first and last cues of a subtitle file to calculate start and end times in seconds."""
    ext = os.path.splitext(filepath)[1].lower()
    start_time = None
    end_time = None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            if ext == '.csv':
                import csv
                reader = csv.reader(f)
                rows = []
                for row in reader:
                    if row and len(row) >= 3 and not row[0].startswith('----------') and row[0] != 'Index':
                        # Check header lines that might sneak in
                        if 'Title:' in row[0] or 'URL:' in row[0]:
                            continue
                        rows.append(row)
                if rows:
                    first_row = rows[0]
                    last_row = rows[-1]
                    if '.' in first_row[0] and '.' not in first_row[1]:
                        # index holds start
                        start_time = float(first_row[0])
                    else:
                        start_time = float(first_row[1])

                    if '.' in last_row[0] and '.' not in last_row[1]:
                        end_time = float(last_row[1])
                    else:
                        end_time = float(last_row[2])
            elif ext == '.srt':
                content = f.read()
                matches = re.findall(r'(\d+:\d{2}:\d{2},\d+)\s*-->\s*(\d+:\d{2}:\d{2},\d+)', content)
                if matches:
                    start_time = _srt_time_to_seconds(matches[0][0])
                    end_time = _srt_time_to_seconds(matches[-1][1])
    except Exception as e:
        print(f"Error parsing times from {filepath}: {e}")

    return start_time, end_time

def shift_subs_file(input_path: str, output_path: str, offset_seconds: float):
    """Shift timestamps in a .srt or .csv file and output a resynced file."""
    ext = os.path.splitext(input_path)[1].lower()

    header = ""
    body = ""
    with open(input_path, 'r', encoding='utf-8') as f:
        # Normalise line endings so CRLF (Windows/YouTube) doesn't break regex splitting
        content = f.read().replace('\r\n', '\n').replace('\r', '\n')

    if ext == '.srt':
        match = re.search(r'(?m)^(\d+\n\d{2}:\d{2}:\d{2},\d{3}\s*-->)', content)
        if match:
            split_idx = match.start(1)
            header = content[:split_idx]
            body = content[split_idx:]
        else:
            body = content
    elif ext == '.csv':
        match = re.search(r'(?mi)^(Index,Start,End,Text)', content)
        if match:
            split_idx = match.start(1)
            header = content[:split_idx]
            body = content[split_idx:]
            if body.startswith('\n'):
                body = body[1:]
                header += '\n'
        else:
            body = content

    # --- Cumulative mapping logic ---
    old_sync = 0.0
    old_gaps = []

    sync_match = re.search(r'^Sync Offset:\s*([\d\.]+)s?$', header, re.MULTILINE)
    if sync_match:
        old_sync = float(sync_match.group(1))

    gaps_match = re.search(r'^Dropped Gaps:\s*(\[.*?\])$', header, re.MULTILINE)
    if gaps_match:
        try:
            old_gaps = json.loads(gaps_match.group(1))
        except json.JSONDecodeError:
            old_gaps = []

    def map_time_to_original(local_t: float) -> float:
        """Map a local time to the absolute original time."""
        t_orig = local_t + old_sync
        for gap in old_gaps:
            gap_start = gap[0]
            gap_dur = gap[1]
            if gap_start <= t_orig:
                t_orig += gap_dur
        return t_orig

    # The new absolute sync offset is the original time corresponding to local time `offset_seconds`
    new_sync = map_time_to_original(offset_seconds)

    # We drop any previous gaps that fall ENTIRELY before our new starting point.
    # The new starting point in absolute time is `new_sync`.
    # A gap falls entirely before new_sync if (gap_start + gap_dur) <= new_sync.
    filtered_gaps = []
    for gap in old_gaps:
        gap_start, gap_dur = gap
        if gap_start + gap_dur <= new_sync:
            continue
        # If the gap straddles our new start, we keep the part after the start
        if gap_start < new_sync:
            overlap = new_sync - gap_start
            filtered_gaps.append([new_sync, gap_dur - overlap])
        else:
            filtered_gaps.append(gap)

    # Re-write the header
    lines = header.splitlines()
    new_lines = []
    found_sync = False
    found_gaps = False

    for line in lines:
        if line.startswith("Sync Offset:"):
            new_lines.append(f"Sync Offset: {new_sync:.3f}s")
            found_sync = True
        elif line.startswith("Dropped Gaps:"):
            # Dump without spaces to keep it single-line if small, or just standard compact JSON
            new_lines.append(f"Dropped Gaps: {json.dumps(filtered_gaps)}")
            found_gaps = True
        else:
            new_lines.append(line)

    if not found_sync:
        inserted = False
        for i, line in enumerate(new_lines):
            if line.startswith("Duration:"):
                new_lines.insert(i, f"Sync Offset: {new_sync:.3f}s")
                inserted = True
                break
        if not inserted:
            new_lines.append(f"Sync Offset: {new_sync:.3f}s")

    if not found_gaps:
        inserted = False
        for i, line in enumerate(new_lines):
            if line.startswith("Duration:"):
                new_lines.insert(i, f"Dropped Gaps: {json.dumps(filtered_gaps)}")
                inserted = True
                break
        if not inserted:
            new_lines.append(f"Dropped Gaps: {json.dumps(filtered_gaps)}")

    new_header = "\n".join(new_lines) + "\n"        
    if ext == '.srt':
        new_header += "-" * 40 + "\n\n"
        
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_header)
        
    if ext == '.srt':
        import pysrt
        import tempfile
        # Create a temporary file with just the body to parse with pysrt
        with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8', suffix='.srt') as temp_f:
            temp_f.write(body)
            temp_path = temp_f.name
            
        try:
            subs = pysrt.open(temp_path)
            subs.shift(seconds=-offset_seconds)
            
            with open(output_path, 'a', encoding='utf-8') as f:
                for sub in subs:
                    f.write(f"{sub.index}\n{sub.start} --> {sub.end}\n{sub.text}\n\n")
        finally:
            os.remove(temp_path)
            
    elif ext == '.csv':
        import csv
        import io
        
        reader = csv.reader(io.StringIO(body))
        with open(output_path, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            for row in reader:
                if not row or row[0].lower() == 'index':
                    writer.writerow(row)
                    continue
                    
                new_row = list(row)
                try:
                    start_val = float(row[1])
                    end_val = float(row[2])
                    new_row[1] = f"{max(0.0, start_val - offset_seconds):.3f}"
                    new_row[2] = f"{max(0.0, end_val - offset_seconds):.3f}"
                except ValueError:
                    pass
                writer.writerow(new_row)
