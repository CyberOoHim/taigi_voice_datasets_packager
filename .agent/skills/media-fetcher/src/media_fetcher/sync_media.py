import argparse
import os
import sys

from .utils import get_url_from_subs, get_time_range_from_subs, shift_subs_file
from .edit_sub import edit_sub

def main():
    parser = argparse.ArgumentParser(
        description="Sync-download a partial video or audio clip based on the first and last timestamps of a subtitle file."
    )
    parser.add_argument("--subs", required=True, help="Path to the .srt or .csv subtitle file.")
    parser.add_argument("--input", help="Optional local media file to sync against instead of downloading.")
    parser.add_argument("--head-pad", type=float, default=0.5,
                        help="Padding before start time in seconds (default: 0.5)")
    parser.add_argument("--tail-pad", type=float, default=0.5,
                        help="Padding after end time in seconds (default: 0.5)")
    parser.add_argument("--audio", action="store_true",
                        help="Extract audio only")
    parser.add_argument("--output", help="Optional output file path")
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Read cookies from this browser (chrome, firefox, edge, brave, safari, \u2026).",
    )
    parser.add_argument(
        "--cookies-file",
        metavar="FILE",
        help="Path to a Netscape-format cookies.txt file.",
    )
    parser.add_argument(
        "--reencode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Force re-encoding instead of stream-copy for video cuts (default: True, use --no-reencode to disable).",
    )

    args = parser.parse_args()

    if not os.path.exists(args.subs):
        print(f"Error: Subtitle file not found: {args.subs}", file=sys.stderr)
        sys.exit(1)

    url = get_url_from_subs(args.subs)
    if not url and not args.input:
        print(f"Error: Could not extract URL from metadata in {args.subs}. Run patch_metadata first, or provide --input.", file=sys.stderr)
        sys.exit(1)

    source = args.input if args.input else url

    start_time, end_time = get_time_range_from_subs(args.subs)
    if start_time is None or end_time is None:
        print(f"Error: Could not parse start and end times from {args.subs}.", file=sys.stderr)
        sys.exit(1)

    print(f"Sync-downloading {source}")
    print(f"Detected Time Range: {start_time}s -> {end_time}s")

    # Default to reencode for video if not explicitly disabled
    reencode_flag = args.reencode

    # Call edit_sub directly using the parsed seconds
    edit_sub(
        source=source,
        start_time=str(start_time),
        end_time=str(end_time),
        head_padding=args.head_pad,
        tail_padding=args.tail_pad,
        output_path=args.output,
        is_audio=args.audio,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies_file,
        reencode=reencode_flag
    )

    # Generate resynced subtitle file with timestamps shifted to media-local time
    offset = max(0.0, start_time - args.head_pad)
    output_dir = os.path.dirname(args.subs) or "."
    base_name = os.path.basename(args.subs)
    resync_path = os.path.join(output_dir, f"resync_{base_name}")

    print(f"Generating resynced subtitle file: {resync_path}")
    shift_subs_file(args.subs, resync_path, offset)

if __name__ == "__main__":
    main()
