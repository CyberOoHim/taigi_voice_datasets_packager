import argparse
import csv
import io
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional, Union

from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is not installed. Please run 'pip install yt-dlp'.", file=sys.stderr)
    sys.exit(1)

from .root_finder import get_project_root
from .utils import is_supported_url, find_downloaded_file, build_cookies_opts, detect_platform


def _generate_cut_filename(title: str, ext: str, start_str: str, end_str: str, prefix: str = "cut") -> str:
    """Filename generator specific to the cut/edit operation (includes timing)."""
    clean_title = re.sub(r'[^\w\s-]', '', title).replace(' ', '_')[:30]
    safe_start = str(start_str).replace(':', '-')
    safe_end = str(end_str).replace(':', '-')
    taipei_tz = timezone(timedelta(hours=8))
    taipei_time = datetime.now(taipei_tz).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{clean_title}_{safe_start}_{safe_end}_{taipei_time}.{ext}"


# ── Subtitle traceability helpers ─────────────────────────────────────────────

def _parse_srt_header_and_body(content: str):
    """Split an SRT/CSV file into (header_text, body_text)."""
    # SRT: body starts at the first bare integer line (the index "1").
    srt_match = re.search(r'(?m)^(\d+)\n\d{2}:\d{2}:\d{2},\d{3}\s*-->', content)
    if srt_match:
        split = srt_match.start(1)
        return content[:split], content[split:]
    # CSV: body starts at the Index, header row.
    csv_match = re.search(r'(?mi)^(Index,Start,End,Text)', content)
    if csv_match:
        split = csv_match.start(1)
        return content[:split], content[split:]
    return "", content


def _extract_header_field(header: str, key: str, default: str = "") -> str:
    m = re.search(rf'^{re.escape(key)}:\s*(.+)$', header, re.MULTILINE)
    return m.group(1).strip() if m else default


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace('.', ',')


def shift_subs_file(
    subs_path: str,
    trim_start: float,
    trim_end: float,
    output_path: Optional[str] = None,
) -> str:
    """
    Given a companion SRT/CSV file and the trim window [trim_start, trim_end]
    (in *local* time relative to the clip that subtitle file describes), produce
    a resynced subtitle file where:

      * Sync Offset  = map_time_to_original(trim_start)
      * Dropped Gaps = old gaps filtered to those that fall after the new offset
      * Timestamps   = shifted so t=0 in the new file corresponds to trim_start
                        in the old file.  Cues that fall entirely outside the
                        window are dropped.

    Returns the path of the written output file.
    """
    with open(subs_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    header, body = _parse_srt_header_and_body(content)
    is_csv = subs_path.lower().endswith('.csv')

    # Parse old traceability state -------------------------------------------
    old_sync_str = _extract_header_field(header, 'Sync Offset', '0.000s')
    try:
        old_sync = float(old_sync_str.rstrip('s'))
    except ValueError:
        old_sync = 0.0

    old_gaps_str = _extract_header_field(header, 'Dropped Gaps', '[]')
    try:
        old_gaps = json.loads(old_gaps_str)
    except json.JSONDecodeError:
        old_gaps = []

    def map_time_to_original(local_t: float) -> float:
        """Map a local timestamp to its absolute original-video time."""
        t_orig = local_t + old_sync
        for gap in sorted(old_gaps, key=lambda g: g[0]):
            if gap[0] <= t_orig:
                t_orig += gap[1]
        return t_orig

    # Compute new Sync Offset and filter gaps --------------------------------
    new_sync = map_time_to_original(trim_start)

    filtered_gaps: list = []
    for gap in sorted(old_gaps, key=lambda g: g[0]):
        gap_start, gap_dur = gap[0], gap[1]
        if gap_start + gap_dur <= new_sync:
            continue  # entirely before new start — discard
        if gap_start < new_sync:
            # Overlaps the new start — keep the remainder
            filtered_gaps.append([new_sync, gap_dur - (new_sync - gap_start)])
        else:
            filtered_gaps.append(gap)

    # Re-write header --------------------------------------------------------
    new_header = re.sub(
        r'^Sync Offset:.*$', f'Sync Offset: {new_sync:.3f}s', header, flags=re.MULTILINE
    )
    new_header = re.sub(
        r'^Dropped Gaps:.*$',
        f'Dropped Gaps: {json.dumps(filtered_gaps, separators=(",", ":"))}',
        new_header,
        flags=re.MULTILINE,
    )
    # Update Total Segments count placeholder — we'll know it after filtering
    # so we patch it at the end.

    # Shift timestamps in body -----------------------------------------------
    if is_csv:
        out_rows = []
        reader = csv.DictReader(io.StringIO(body))
        for row in reader:
            try:
                s = float(row['Start'])
                e = float(row['End'])
            except (KeyError, ValueError):
                continue
            if e <= trim_start or s >= trim_end:
                continue  # outside the trim window
            new_s = max(0.0, s - trim_start)
            new_e = max(0.0, e - trim_start)
            out_rows.append({'Index': row.get('Index', ''), 'Start': f'{new_s:.3f}',
                             'End': f'{new_e:.3f}', 'Text': row.get('Text', '')})

        new_header = re.sub(
            r'^Total Segments:.*$', f'Total Segments: {len(out_rows)}', new_header,
            flags=re.MULTILINE,
        )
        buf = io.StringIO()
        buf.write(new_header)
        writer = csv.DictWriter(buf, fieldnames=['Index', 'Start', 'End', 'Text'])
        writer.writeheader()
        writer.writerows(out_rows)
        new_content = buf.getvalue()

    else:  # SRT
        blocks = re.split(r'\n{2,}', body.strip())
        out_blocks = []
        for block in blocks:
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            tm = re.match(
                r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})',
                lines[1],
            )
            if not tm:
                continue

            def _secs(ts: str) -> float:
                h, m, s = ts.split(':')
                sec, ms = s.split(',')
                return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0

            s = _secs(tm.group(1))
            e = _secs(tm.group(2))
            if e <= trim_start or s >= trim_end:
                continue
            new_s = max(0.0, s - trim_start)
            new_e = max(0.0, e - trim_start)
            out_blocks.append(
                f"{len(out_blocks)+1}\n{_srt_ts(new_s)} --> {_srt_ts(new_e)}\n"
                + '\n'.join(lines[2:])
            )

        new_header = re.sub(
            r'^Total Segments:.*$', f'Total Segments: {len(out_blocks)}', new_header,
            flags=re.MULTILINE,
        )
        new_content = new_header + '\n'.join(out_blocks) + '\n'

    # Determine output path --------------------------------------------------
    if output_path is None:
        p = Path(subs_path)
        output_path = str(p.parent / f"resync_{p.name}")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return output_path


def parse_time(time_str: Union[str, float, int]) -> float:
    if isinstance(time_str, (int, float)):
        return float(time_str)

    if re.match(r'^\d+(\.\d+)?$', time_str):
        return float(time_str)

    # Handle HH:MM:SS.mmm or MM:SS.mmm
    parts = time_str.split(':')
    if len(parts) == 3:  # HH:MM:SS
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:  # MM:SS
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        raise ValueError(f"Invalid time format: {time_str}")


def _get_video_codec() -> list[str]:
    """Auto-detect if NVIDIA GPU is available via PyTorch to use NVENC and return encode args."""
    try:
        import torch
        if torch.cuda.is_available():
            print("Using NVIDIA GPU (NVENC) for video encoding...", file=sys.stderr)
            # NVENC uses -cq for CRF-like behavior and p6 for slower/high quality
            return ["-c:v", "h264_nvenc", "-cq", "18", "-rc", "vbr", "-preset", "p6"]
    except ImportError:
        pass
    print("Using CPU (libx264) for video encoding...", file=sys.stderr)
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]


def cut_local_file(input_path: str, start: float, end: float,
                   output_path: str, is_audio: bool = False,
                   reencode: bool = False):
    """
    Cut a local file using ffmpeg.

    Args:
        input_path:  Path to the source media file.
        start:       Start time in seconds (after padding has been applied).
        end:         End time in seconds (after padding has been applied).
        output_path: Destination file path.
        is_audio:    If True, extract audio-only as WAV (default) or MP3.
        reencode:    If True, force re-encoding instead of stream-copy for
                     video. Re-encoding is slower but frame-accurate and works
                     with any codec. When False, stream-copy is attempted first
                     and re-encoding is used as a fallback if stream-copy fails.
    """
    if is_audio:
        # Audio extraction always requires re-encoding; reencode flag is a no-op here.
        ext = os.path.splitext(output_path)[1].lower().lstrip('.')
        if ext == 'wav':
            # Use pcm_s16le for standard ASR-compatible WAV
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-to', str(end),
                '-i', input_path,
                '-vn',
                '-acodec', 'pcm_s16le',
                output_path
            ]
        else:
            # Assume m4a/aac as the primary alternative/fallback
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-to', str(end),
                '-i', input_path,
                '-vn',
                '-acodec', 'aac',
                '-b:a', '192k',
                output_path
            ]

        try:
            _run_ffmpeg(cmd)
        except Exception as e:
            if ext == 'wav':
                print(f"Warning: Failed to produce .wav ({e}). Falling back to .m4a...", file=sys.stderr)
                m4a_path = str(Path(output_path).with_suffix('.m4a'))
                cmd_fallback = [
                    'ffmpeg', '-y',
                    '-ss', str(start),
                    '-to', str(end),
                    '-i', input_path,
                    '-vn',
                    '-acodec', 'aac',
                    '-b:a', '192k',
                    m4a_path
                ]
                _run_ffmpeg(cmd_fallback)
            else:
                raise e
        return

    if reencode:
        # Frame-accurate cut: place -ss after -i so ffmpeg decodes from the
        # nearest keyframe to the exact start position.
        vargs = _get_video_codec()
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-ss', str(start),
            '-to', str(end),
            *vargs,
            output_path
        ]
        _run_ffmpeg(cmd)
        return

    # Stream-copy path (fast, but cuts on keyframe boundaries).
    # -ss before -i = fast input seek; exact enough for most use-cases.
    stream_copy_cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-to', str(end),
        '-i', input_path,
        '-c', 'copy',
        output_path
    ]
    try:
        _run_ffmpeg(stream_copy_cmd)
    except subprocess.CalledProcessError:
        # Stream-copy can fail when the container/codec combination does not
        # support copying (e.g. some HEVC or VP9 streams). Fall back to
        # re-encoding automatically and inform the caller.
        print(
            "Warning: stream-copy failed — falling back to re-encode. "
            "This may take longer but will produce a correct output.",
            file=sys.stderr
        )
        vargs = _get_video_codec()
        reencode_cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-ss', str(start),
            '-to', str(end),
            *vargs,
            output_path
        ]
        _run_ffmpeg(reencode_cmd)


def _run_ffmpeg(cmd: list):
    """
    Run an ffmpeg command, surfacing stderr on failure.

    Raises:
        subprocess.CalledProcessError: re-raised after printing ffmpeg's stderr
                                       so callers (and users) can see what went wrong.
    """
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg error (exit {e.returncode}) — see console output above for details.", file=sys.stderr)
        raise


def edit_sub(source: str, start_time: str, end_time: str,
             head_padding: float = 0.5, tail_padding: float = 0.5,
             output_path: Optional[str] = None, is_audio: bool = False,
             cookies_from_browser: Optional[str] = None,
             cookies_file: Optional[str] = None,
             reencode: bool = False,
             subs_path: Optional[str] = None):
    """
    Cut a video/audio segment from a URL or local file.

    Partial-download behaviour by platform:
      - YouTube: Only the requested time range is fetched (fragment-level
        download via yt-dlp's download_ranges).  ``--reencode`` has no effect
        here because yt-dlp handles encoding internally.
      - Facebook / X/Twitter: These platforms use segmented DASH/HLS streams.
        yt-dlp's download_ranges is silently ignored for these platforms, so we
        download the full video and trim it locally with ffmpeg instead.
        A clear warning is printed before the download begins.
      - Local files: Trimmed directly with ffmpeg — no network needed.

    Args:
        source:               YouTube/Facebook/X URL or local file path.
        start_time:           Segment start (seconds, MM:SS, or HH:MM:SS).
        end_time:             Segment end (same formats).
        head_padding:         Seconds to prepend before start_time (default 0.5).
        tail_padding:         Seconds to append after end_time (default 0.5).
        output_path:          Optional explicit output file path.
        is_audio:             Extract audio-only (mp3).
        cookies_from_browser: Browser name for cookie auth (FB/X private content).
        cookies_file:         Path to a Netscape-format cookies.txt file.
        reencode:             Force re-encoding for local / FB / X cuts.
                              Has no effect for YouTube (yt-dlp controls encoding).
        subs_path:            Optional companion SRT/CSV subtitle file. When provided,
                              a resynced subtitle file (resync_<name>) is written
                              next to the output media with updated Sync Offset and
                              Dropped Gaps traceability headers.
    """
    start = max(0, parse_time(start_time) - head_padding)
    end = parse_time(end_time) + tail_padding

    # ── Pre-flight validation ──────────────────────────────────────────────────
    if end <= start:
        print(
            f"Error: end time ({end:.3f}s) must be greater than start time ({start:.3f}s). "
            "Check your --start / --end values and padding.",
            file=sys.stderr
        )
        sys.exit(1)

    root = get_project_root()
    is_url = is_supported_url(source)

    if is_url:
        platform = detect_platform(source)
        output_dir = str(root / ("media-downloads/audio" if is_audio else "media-downloads/video"))
        os.makedirs(output_dir, exist_ok=True)

        if platform == 'youtube':
            # ── YouTube: fragment-level partial download ────────────────────────
            if reencode:
                print(
                    "Warning: --reencode has no effect for YouTube downloads. "
                    "yt-dlp controls encoding internally. "
                    "To re-encode, download the file first and then run with --reencode on the local path.",
                    file=sys.stderr
                )

            def _make_ranges(info_dict, ydl_instance):
                return [{"start_time": start, "end_time": end}]

            ydl_opts = {
                'format': 'bestaudio/best' if is_audio else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': os.path.join(output_dir, '%(title).200s.%(ext)s'),
                'quiet': False,
                'noprogress': False,
                'no_warnings': True,
                'download_ranges': _make_ranges,
                'force_keyframes_at_cuts': True,
                'merge_output_format': 'mp4',
                **build_cookies_opts(cookies_from_browser, cookies_file),
            }

            if is_audio:
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                    'preferredquality': '192',
                }]

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(source, download=True)
            except Exception as e:
                if is_audio:
                    print(f"Warning: Failed to produce .wav ({e}). Falling back to .m4a...", file=sys.stderr)
                    ydl_opts['postprocessors'][0]['preferredcodec'] = 'm4a'
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(source, download=True)
                else:
                    raise e

            ext = 'wav' if is_audio else 'mp4'
            # We need to verify if it actually fell back to m4a
            if is_audio:
                downloaded_wav = find_downloaded_file(output_dir, ".wav")
                if not downloaded_wav:
                    downloaded_m4a = find_downloaded_file(output_dir, ".m4a")
                    if downloaded_m4a:
                        ext = 'm4a'

            if not output_path:
                final_name = _generate_cut_filename(
                    info.get('title', 'cut'), ext, start_time, end_time, prefix="partial"
                )
                output_path = os.path.join(output_dir, final_name)

            downloaded = find_downloaded_file(output_dir, f".{ext}")
            if downloaded and os.path.abspath(downloaded) != os.path.abspath(output_path):
                os.rename(downloaded, output_path)
            elif not downloaded:
                temp = ydl.prepare_filename(info)
                if is_audio:
                    base, _ = os.path.splitext(temp)
                    temp = f"{base}.{ext}"
                if os.path.exists(temp):
                    os.rename(temp, output_path)
                else:
                    print(
                        f"Warning: Downloaded file not found at expected path: {temp}",
                        file=sys.stderr
                    )

        else:
            # ── Facebook / X/Twitter / unknown: full download + local trim ─────
            # yt-dlp's download_ranges is not honoured for DASH/HLS streams, so
            # we download the full video first, trim with ffmpeg, then clean up.
            print(
                f"Warning: '{platform}' uses DASH/HLS streams — the complete video must be "
                "downloaded before ffmpeg can trim the segment. "
                f"Downloading full video from {source} …",
                file=sys.stderr
            )

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': os.path.join(output_dir, '%(title).200s.%(ext)s'),
                'quiet': False,
                'noprogress': False,
                'no_warnings': True,
                'merge_output_format': 'mp4',
                **build_cookies_opts(cookies_from_browser, cookies_file),
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source, download=True)

            full_video = find_downloaded_file(output_dir, '.mp4')
            if full_video is None:
                print("Error: Could not locate the downloaded video file.", file=sys.stderr)
                sys.exit(1)

            ext = 'wav' if is_audio else 'mp4'
            if not output_path:
                final_name = _generate_cut_filename(
                    info.get('title', 'cut'), ext, start_time, end_time, prefix="partial"
                )
                output_path = os.path.join(output_dir, final_name)

            # Trim the full download to the requested range
            try:
                cut_local_file(full_video, start, end, output_path, is_audio, reencode=reencode)
            except Exception as e:
                if is_audio and ext == 'wav':
                    print(f"Warning: Failed to produce .wav ({e}). Falling back to .m4a...", file=sys.stderr)
                    ext = 'm4a'
                    final_name = _generate_cut_filename(
                        info.get('title', 'cut'), ext, start_time, end_time, prefix="partial"
                    )
                    output_path = os.path.join(output_dir, final_name)
                    cut_local_file(full_video, start, end, output_path, is_audio, reencode=reencode)
                else:
                    raise e

            # Remove the full-length download to save disk space
            if os.path.exists(full_video) and os.path.abspath(full_video) != os.path.abspath(output_path):
                os.remove(full_video)

        print(json.dumps({
            "status": "success",
            "source": "url",
            "platform": platform,
            "title": info.get('title'),
            "start": start,
            "end": end,
            "output_file": output_path,
            "resynced_subs": _maybe_shift_subs(subs_path, start, end, output_path),
        }))

    else:
        # ── Local file ─────────────────────────────────────────────────────────
        if not os.path.exists(source):
            print(f"Error: Local file not found: {source}", file=sys.stderr)
            sys.exit(1)

        if not output_path:
            input_path = Path(source)
            output_dir = input_path.parent
            ext = 'wav' if is_audio else input_path.suffix.lstrip('.')
            final_name = _generate_cut_filename(
                input_path.stem, ext, start_time, end_time, prefix="local_cut"
            )
            output_path = str(output_dir / final_name)

        cut_local_file(source, start, end, output_path, is_audio, reencode=reencode)

        print(json.dumps({
            "status": "success",
            "source": "local",
            "input_file": source,
            "start": start,
            "end": end,
            "output_file": output_path,
            "resynced_subs": _maybe_shift_subs(subs_path, start, end, output_path),
        }))


def _maybe_shift_subs(
    subs_path: Optional[str],
    trim_start: float,
    trim_end: float,
    media_output_path: Optional[str],
) -> Optional[str]:
    """Shift subtitle traceability headers if subs_path is provided. Returns output path or None."""
    if not subs_path:
        return None
    if not os.path.isfile(subs_path):
        print(
            f"Warning: --subs file not found: {subs_path}; skipping subtitle resync.",
            file=sys.stderr,
        )
        return None
    # Place the resynced subs alongside the output media file when possible.
    resync_out: Optional[str] = None
    if media_output_path:
        p = Path(subs_path)
        out_dir = Path(media_output_path).parent
        resync_out = str(out_dir / f"resync_{p.name}")
    try:
        return shift_subs_file(subs_path, trim_start, trim_end, output_path=resync_out)
    except Exception as exc:
        print(f"Warning: subtitle resync failed: {exc}", file=sys.stderr)
        return None


def main():

    parser = argparse.ArgumentParser(
        description="Cut video or audio from YouTube, Facebook, X/Twitter, or a local file."
    )
    parser.add_argument(
        "source",
        help="YouTube/Facebook/X URL or local file path"
    )
    parser.add_argument("--start", required=True,
                        help="Start time (seconds, MM:SS, or HH:MM:SS)")
    parser.add_argument("--end", required=True,
                        help="End time (seconds, MM:SS, or HH:MM:SS)")
    parser.add_argument("--head-pad", type=float, default=0.5,
                        help="Padding before start time in seconds (default: 0.5)")
    parser.add_argument("--tail-pad", type=float, default=0.5,
                        help="Padding after end time in seconds (default: 0.5)")
    parser.add_argument("--audio", action="store_true",
                        help="Extract audio only (mp3)")
    parser.add_argument("--output", help="Optional output file path")
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="Read cookies from this browser (chrome, firefox, edge, brave, safari, …). "
             "Required for private or login-gated Facebook/X content.",
    )
    parser.add_argument(
        "--cookies-file",
        metavar="FILE",
        help="Path to a Netscape-format cookies.txt file (alternative to --cookies-from-browser).",
    )
    parser.add_argument(
        "--reencode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Force re-encoding instead of stream-copy for video cuts (default: True, use --no-reencode to disable). "
            "Produces frame-accurate output and works with any codec, but is slower. "
            "Has no effect for YouTube URLs (yt-dlp controls encoding). "
            "For audio (--audio), re-encoding is always performed regardless of this flag."
        ),
    )
    parser.add_argument(
        "--subs",
        metavar="FILE",
        help=(
            "Companion SRT or CSV subtitle file for this clip. "
            "When provided, a resynced subtitle file (resync_<name>) is written "
            "next to the output media with updated Sync Offset and Dropped Gaps "
            "headers so the clip can always be traced back to the original video."
        ),
    )

    args = parser.parse_args()

    edit_sub(
        args.source, args.start, args.end,
        args.head_pad, args.tail_pad,
        args.output, args.audio,
        args.cookies_from_browser, args.cookies_file,
        args.reencode,
        subs_path=args.subs,
    )


if __name__ == "__main__":
    main()