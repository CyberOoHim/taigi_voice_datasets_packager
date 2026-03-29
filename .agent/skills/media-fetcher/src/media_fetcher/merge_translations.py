import argparse
import csv
import json
import re
import sys
import os


def merge_to_csv(input_csv: str, translations: dict, output_csv: str):
    with open(input_csv, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the line that starts the CSV data (Index,Start,End,Text)
    data_start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('Index,Start'):
            data_start_idx = i
            break

    if data_start_idx is None:
        print("Error: Could not find 'Index,Start' header row in CSV.", file=sys.stderr)
        sys.exit(1)

    header_lines = lines[:data_start_idx]
    data_lines = lines[data_start_idx:]

    # Optionally prepend the Taigi title to the header block
    new_header_lines = []
    taigi_title = translations.get('title')
    for line in header_lines:
        if line.startswith('Title:') and taigi_title:
            new_header_lines.append(f"標題: {taigi_title}\n")
        new_header_lines.append(line)

    reader = csv.reader(data_lines)
    rows = list(reader)

    if not rows:
        print("Error: No data in CSV", file=sys.stderr)
        sys.exit(1)

    # rows[0] is guaranteed to be the Index header row (we found it above).
    if rows[0][0] == "Index":
        if "Taigi" not in rows[0]:
            # Insert Taigi column before Text (index 3: Index, Start, End, [Taigi,] Text)
            rows[0].insert(3, "Taigi")

    for i in range(1, len(rows)):
        if not rows[i]:
            continue
        idx = str(rows[i][0])
        taigi = translations.get(idx, "")
        if "Taigi" in rows[0]:
            rows[i].insert(3, taigi)

    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        for header in new_header_lines:
            f.write(header)
        writer = csv.writer(f)
        writer.writerows(rows)


def merge_to_srt(input_srt: str, translations: dict, output_srt: str):
    with open(input_srt, 'r', encoding='utf-8') as f:
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

    header_lines = lines[:data_start_idx]
    new_header_lines = []
    taigi_title = translations.get('title')
    for line in header_lines:
        if line.startswith('Title:') and taigi_title:
            new_header_lines.append(f"標題: {taigi_title}")
        new_header_lines.append(line)

    header_content = '\n'.join(new_header_lines)
    data_content = '\n'.join(lines[data_start_idx:]).strip()

    blocks = re.split(r'\n\n+', data_content)

    with open(output_srt, 'w', encoding='utf-8') as f:
        if has_custom_header:
            f.write(header_content + "\n")

        for block in blocks:
            if not block.strip():
                continue
            block_lines = block.strip().split('\n')
            if len(block_lines) >= 3:
                idx = block_lines[0].strip()
                time_line = block_lines[1]
                orig_text = '\n'.join(block_lines[2:])

                f.write(f"{idx}\n")
                f.write(f"{time_line}\n")

                # Taigi first, then original
                taigi_text = translations.get(idx, "")
                if taigi_text:
                    f.write(f"{taigi_text}\n")
                f.write(f"{orig_text}\n\n")


def main():
    parser = argparse.ArgumentParser(
        description="Merge Taigi translations into a subtitle file (.srt or .csv)."
    )
    parser.add_argument("input_file", help="Original .srt or .csv subtitle file")
    parser.add_argument("translations_json", help="JSON file mapping segment indices to Taigi translations")
    parser.add_argument("output_file", help="Path for the merged output file")
    args = parser.parse_args()

    input_file = args.input_file
    translations_json = args.translations_json
    output_file = args.output_file

    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(translations_json):
        print(f"Error: Translations JSON not found: {translations_json}", file=sys.stderr)
        sys.exit(1)

    with open(translations_json, 'r', encoding='utf-8') as f:
        try:
            translations = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}", file=sys.stderr)
            sys.exit(1)

    # Normalise keys to strings for consistent lookup
    translations = {str(k): v for k, v in translations.items()}

    ext = os.path.splitext(input_file)[1].lower()
    if ext == '.csv':
        merge_to_csv(input_file, translations, output_file)
    elif ext == '.srt':
        merge_to_srt(input_file, translations, output_file)
    else:
        print(f"Error: Unsupported file extension {ext}. Use .srt or .csv.", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "status": "success",
        "action": "merge_translations",
        "input_file": input_file,
        "output_file": output_file
    }))

if __name__ == "__main__":
    main()