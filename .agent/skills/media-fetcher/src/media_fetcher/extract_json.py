import argparse
import csv
import json
import re
import sys
import os

def extract_from_srt(input_path: str, output_path: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.splitlines()
    data_start_idx = 0
    
    has_custom_header = False
    for i, line in enumerate(lines):
        if line.startswith('----------------------------------------'):
            has_custom_header = True
            data_start_idx = i + 1
            break
            
    if not has_custom_header:
        data_start_idx = 0

    header_lines = lines[:data_start_idx-1] if has_custom_header else []
    
    output_dict = {}
    for line in header_lines:
        if line.startswith('Title:'):
            output_dict['title'] = line.split('Title:', 1)[1].strip()

    data_content = '\n'.join(lines[data_start_idx:]).strip()
    blocks = re.split(r'\n\n+', data_content)
    
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            idx = lines[0].strip()
            # lines[1] is time
            text = ' '.join(lines[2:])
            output_dict[idx] = text
            
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_dict, f, ensure_ascii=False, indent=2)

def extract_from_csv(input_path: str, output_path: str):
    output_dict = {}
    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    data_start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('Index,Start'):
            data_start_idx = i
            break
            
    header_lines = lines[:data_start_idx]
    for line in header_lines:
        if line.startswith('Title:'):
            output_dict['title'] = line.split('Title:', 1)[1].strip()
            
    data_lines = lines[data_start_idx:]
    reader = csv.reader(data_lines)
    for row in reader:
        if not row or (row[0] == "Index" and row[1] == "Start"):
            continue
        idx = row[0]
        if '.' in idx and len(row) >= 3:
            # Fallback format
            text = '\n'.join(row[2:])
            idx = str(len(output_dict) + (0 if 'title' in output_dict else 1))
        else:
            text = '\n'.join(row[3:])
        output_dict[idx] = text

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_dict, f, ensure_ascii=False, indent=2)

def get_base_filename(input_path: str) -> str:
    base_name = os.path.basename(input_path)
    return os.path.splitext(base_name)[0]

def main():
    parser = argparse.ArgumentParser(description="Extract index and subtitle text to JSON.")
    parser.add_argument("input", help="Input file path (.srt or .csv)")
    parser.add_argument("--output", help="Optional output file path. Defaults to same directory with .json ext")
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
        output_dir = os.path.dirname(input_path) or "."
        base_name = get_base_filename(input_path)
        output_path = os.path.join(output_dir, base_name + ".json")

    if ext == '.srt':
        extract_from_srt(input_path, output_path)
    elif ext == '.csv':
        extract_from_csv(input_path, output_path)
    else:
        print(f"Error: Unsupported file extension {ext}. Use .srt or .csv.", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "status": "success",
        "action": "extract_json",
        "input_file": input_path,
        "output_file": output_path
    }))

if __name__ == "__main__":
    main()