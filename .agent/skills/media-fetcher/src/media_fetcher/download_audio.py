import argparse
import json
import os
import sys
from typing import Optional

try:
    import yt_dlp
    import yt_dlp.utils as yt_utils
except ImportError:
    print("Error: yt-dlp is not installed. Please run 'pip install yt-dlp'.", file=sys.stderr)
    sys.exit(1)

from .root_finder import get_project_root
from .utils import extract_video_id, is_supported_url, generate_filename, find_downloaded_file, build_cookies_opts


def download_audio(url: str, format_choice: str = "wav",
                   output_path: Optional[str] = None,
                   cookies_from_browser: Optional[str] = None,
                   cookies_file: Optional[str] = None):
    """
    Download audio from YouTube, Facebook, X/Twitter, or any yt-dlp–supported URL.

    Notes on Facebook & X/Twitter:
      - Both platforms expose audio-only streams in their DASH manifests.
        yt-dlp selects the best one via `bestaudio`, and ffmpeg converts it to
        the requested format.  No extra post-download conversion is required.
      - Some older Twitter/X videos use a single combined stream; yt-dlp will
        automatically extract the audio track from it (same quality).
      - Private or friends-only Facebook content requires --cookies-from-browser
        or --cookies-file.

    Args:
        url:                  Video/post URL.
        format_choice:        Target audio format: wav, mp3, m4a, etc.
        output_path:          Optional explicit output file path.
        cookies_from_browser: Browser name to read cookies from (e.g. "chrome").
        cookies_file:         Path to a Netscape-format cookies.txt file.
    """
    root = get_project_root()
    output_dir = str(root / "media-downloads/audio")
    if output_path:
        output_dir = os.path.dirname(output_path) or "."

    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title).200s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': format_choice,
            'preferredquality': '192',
        }],
        **build_cookies_opts(cookies_from_browser, cookies_file),
    }

    if output_path:
        ydl_opts['outtmpl'] = output_path

    def _run(opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info

    try:
        try:
            info = _run(ydl_opts)
        except (yt_utils.PostProcessingError, Exception) as e:
            if format_choice == "wav":
                print(f"Warning: Failed to produce .wav ({e}). Falling back to .m4a...", file=sys.stderr)
                format_choice = "m4a"
                ydl_opts['postprocessors'][0]['preferredcodec'] = "m4a"
                info = _run(ydl_opts)
            else:
                raise e

        if not output_path:
            final_filename = find_downloaded_file(output_dir, f".{format_choice}")
            if final_filename is None:
                # Fallback: guess path from yt-dlp's template
                with yt_dlp.YoutubeDL({'quiet': True}) as _ydl:
                    guessed = _ydl.prepare_filename(info)
                base, _ = os.path.splitext(guessed)
                final_filename = f"{base}.{format_choice}"
            else:
                new_filename = generate_filename(info.get('title', 'audio'), format_choice)
                new_path = os.path.join(output_dir, new_filename)
                os.rename(final_filename, new_path)
                final_filename = new_path
        else:
            # ydl_opts['outtmpl'] was already set to output_path, so yt-dlp
            # wrote the file there directly. No rename needed.
            final_filename = output_path

        print(json.dumps({
            "status": "success",
            "platform": info.get('extractor_key', 'unknown').lower(),
            "video_id": info.get('id'),
            "title": info.get('title'),
            "output_file": final_filename
        }))

    except yt_utils.PostProcessingError as e:
        # ffmpeg post-processing failed (e.g. ffmpeg not installed)
        print(f"Warning: ffmpeg post-processing failed ({e}). "
              "Downloading best available audio without conversion.", file=sys.stderr)
        fallback_opts = {k: v for k, v in ydl_opts.items() if k != 'postprocessors'}
        try:
            info = _run(fallback_opts)
            # Use prepare_filename to find the actual downloaded file reliably
            with yt_dlp.YoutubeDL({'quiet': True}) as _ydl:
                raw = _ydl.prepare_filename(info)
            ext = os.path.splitext(raw)[1]
            final_filename = raw if os.path.exists(raw) else (
                find_downloaded_file(output_dir, ext) or output_dir
            )
            print(json.dumps({
                "status": "success",
                "platform": info.get('extractor_key', 'unknown').lower(),
                "video_id": info.get('id'),
                "title": info.get('title'),
                "output_file": final_filename,
                "note": "ffmpeg not found, used default format"
            }))
        except Exception as e2:
            print(f"Error downloading audio: {e2}", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"Error downloading audio: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Download audio from YouTube, Facebook, X/Twitter, or any yt-dlp–supported URL."
    )
    parser.add_argument("url", nargs="?", help="Video URL (YouTube, Facebook, X/Twitter, etc.)")
    parser.add_argument("--subs", help="Extract URL and download full media for this SRT/CSV file.")
    parser.add_argument("--format", default="wav", help="Audio format: wav, mp3, m4a, etc. (default: wav)")
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

    download_audio(url, args.format, args.output,
                   args.cookies_from_browser, args.cookies_file)


if __name__ == "__main__":
    main()
