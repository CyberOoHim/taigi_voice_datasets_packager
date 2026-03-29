import argparse
import csv
import io
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta

def srt_time_to_seconds(srt_time: str) -> float:
    parts = srt_time.split(',')
    time_parts = parts[0].split(':')
    hours = int(time_parts[0])
    minutes = int(time_parts[1])
    seconds = int(time_parts[2])
    milliseconds = int(parts[1]) if len(parts) > 1 else 0
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0

def seconds_to_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millisecs = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def convert_srt_to_csv(input_path: str, output_path: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.splitlines()
    header_lines = []
    data_start_idx = 0
    
    has_custom_header = False
    for i, line in enumerate(lines):
        if line.startswith('----------------------------------------'):
            has_custom_header = True
            data_start_idx = i + 1
            break
            
    if has_custom_header:
        header_lines = lines[:data_start_idx-1]
    else:
        header_lines = []
        data_start_idx = 0

    data_content = '\n'.join(lines[data_start_idx:]).strip()
    
    blocks = re.split(r'\n\n+', data_content)
    
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        for header in header_lines:
            if not header.startswith('----------------------------------------'):
                f.write(f"{header}\n")
                
        writer = csv.writer(f)
        writer.writerow(["Index", "Start", "End", "Text"])
        
        for block in blocks:
            if not block.strip():
                continue
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                idx = lines[0]
                time_line = lines[1]
                times = time_line.split(' --> ')
                if len(times) == 2:
                    start_sec = srt_time_to_seconds(times[0])
                    end_sec = srt_time_to_seconds(times[1])
                    text = ' '.join(lines[2:])
                    writer.writerow([idx, start_sec, end_sec, text])

def convert_csv_to_srt(input_path: str, output_path: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    data_start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('Index,Start'):
            data_start_idx = i
            break
            
    header_lines = [line.strip() for line in lines[:data_start_idx] if line.strip()]
    data_lines = lines[data_start_idx:]
    
    segments = []
    reader = csv.reader(data_lines)
    for row in reader:
        if not row:
            continue
        if row[0] == "Index" and row[1] == "Start":
            continue # Skip header row
        else:
            idx = row[0]
            # Fallback: first column looks like a floating-point start time rather
            # than an integer index (header row was absent or malformed).
            if '.' in idx and len(row) >= 3:
                print(
                    f"Warning: unexpected decimal in index column '{idx}' — "
                    "treating row as (Start, End, Text) and re-indexing.",
                    file=sys.stderr,
                )
                start = float(row[0])
                end = float(row[1])
                text = '\n'.join(row[2:])
                idx = len(segments) + 1
            else:
                start = float(row[1])
                end = float(row[2])
                text = '\n'.join(row[3:])
                
            segments.append({'idx': idx, 'start': start, 'end': end, 'text': text})

    with open(output_path, 'w', encoding='utf-8') as f:
        for header in header_lines:
            f.write(f"{header}\n")
        if header_lines:
            f.write("-" * 40 + "\n\n")
            
        for seg in segments:
            start_time = seconds_to_srt_time(seg['start'])
            end_time = seconds_to_srt_time(seg['end'])
            f.write(f"{seg['idx']}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{seg['text']}\n\n")

def get_base_filename(input_path: str) -> str:
    base_name = os.path.basename(input_path)
    return os.path.splitext(base_name)[0]

def main():
    parser = argparse.ArgumentParser(description="Convert subtitles between SRT and CSV formats.")
    parser.add_argument("input", help="Input file path (.srt or .csv)")
    parser.add_argument("--output", help="Optional output file path. Defaults to media-subtitles/[filename].[ext]")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()
    
    if args.output:
        output_path = args.output
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = "media-subtitles"
        os.makedirs(output_dir, exist_ok=True)
        base_name = get_base_filename(input_path)
        new_ext = ".csv" if ext == ".srt" else ".srt"
        output_path = os.path.join(output_dir, base_name + new_ext)

    if ext == '.srt':
        convert_srt_to_csv(input_path, output_path)
        format_from, format_to = "srt", "csv"
    elif ext == '.csv':
        convert_csv_to_srt(input_path, output_path)
        format_from, format_to = "csv", "srt"
    else:
        print(f"Error: Unsupported file extension {ext}. Use .srt or .csv.", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "status": "success",
        "action": f"convert_{format_from}_to_{format_to}",
        "input_file": input_path,
        "output_file": output_path
    }))

if __name__ == "__main__":
    main()