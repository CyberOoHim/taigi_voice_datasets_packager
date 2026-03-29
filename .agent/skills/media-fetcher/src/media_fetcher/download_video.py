import argparse
import json
import os
import sys
from typing import Optional

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is not installed. Please run 'pip install yt-dlp'.", file=sys.stderr)
    sys.exit(1)

from .root_finder import get_project_root
from .utils import extract_video_id, is_supported_url, generate_filename, find_downloaded_file, build_cookies_opts


def download_video(url: str, output_path: Optional[str] = None,
                   cookies_from_browser: Optional[str] = None,
                   cookies_file: Optional[str] = None):
    """
    Download a video from YouTube, Facebook, X/Twitter, or any yt-dlp–supported URL.

    Args:
        url:                  Video URL.
        output_path:          Optional explicit output file path.
        cookies_from_browser: Browser name to read cookies from (e.g. "chrome",
                              "firefox", "edge"). Required for private FB/X content.
        cookies_file:         Path to a Netscape-format cookies.txt file (alternative
                              to cookies_from_browser).
    """
    root = get_project_root()
    output_dir = str(root / "media-downloads/video")
    if output_path:
        output_dir = os.path.dirname(output_path) or "."

    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(output_dir, '%(title).200s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        # Merge output into mp4 when separate streams are downloaded
        'merge_output_format': 'mp4',
        **build_cookies_opts(cookies_from_browser, cookies_file),
    }

    if output_path:
        ydl_opts['outtmpl'] = output_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Always 'mp4' — merge_output_format ensures this regardless of source streams.
            # info.get('ext') would return the pre-merge stream ext (e.g. 'webm'), not 'mp4'.
            ext = 'mp4'

            if not output_path:
                downloaded = find_downloaded_file(output_dir, f".{ext}")
                if downloaded is None:
                    downloaded = ydl.prepare_filename(info)

                new_filename = generate_filename(info.get('title', 'video'), ext)
                new_path = os.path.join(output_dir, new_filename)
                if os.path.exists(downloaded):
                    os.rename(downloaded, new_path)
                    filename = new_path
                else:
                    filename = downloaded
            else:
                filename = output_path

            print(json.dumps({
                "status": "success",
                "platform": info.get('extractor_key', 'unknown').lower(),
                "video_id": info.get('id'),
                "title": info.get('title'),
                "output_file": filename
            }))
    except Exception as e:
        print(f"Error downloading video: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Download video from YouTube, Facebook, X/Twitter, or any yt-dlp–supported URL."
    )
    parser.add_argument("url", nargs="?", help="Video URL (YouTube, Facebook, X/Twitter, etc.)")
    parser.add_argument("--subs", help="Extract URL and download full media for this SRT/CSV file.")
    parser.add_argument("--output", help="Optional output file path.")
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Read cookies from this browser (chrome, firefox, edge, brave, safari, \u2026). "
             "Required for private or login-gated Facebook/X content.",
    )
    parser.add_argument(
        "--cookies-file",
        metavar="FILE",
        help="Path to a Netscape-format cookies.txt file (alternative to --cookies-from-browser).",
    )
    args = parser.parse_args()

    url = args.url
    if args.subs:
        from .utils import get_url_from_subs
        parsed_url = get_url_from_subs(args.subs)
        if not parsed_url:
            print(f"Error: Could not find URL in {args.subs}. Run patch_metadata first.", file=sys.stderr)
            sys.exit(1)
        url = parsed_url

    if not url:
        print("Error: You must provide a URL or a --subs file.", file=sys.stderr)
        sys.exit(1)

    if not is_supported_url(url):
        print(f"Error: Unrecognised URL: {url}", file=sys.stderr)
        sys.exit(1)

    download_video(url, args.output, args.cookies_from_browser, args.cookies_file)


if __name__ == "__main__":
    main()
