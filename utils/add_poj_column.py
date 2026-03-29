import csv
import sys
import os
import converter

def add_poj(input_csv, output_csv):
    with open(input_csv, 'r', encoding='utf-8') as fin, \
         open(output_csv, 'w', encoding='utf-8', newline='') as fout:
        
        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames
        if 'POJ' not in fieldnames:
            fieldnames.append('POJ')
            
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        
        count = 0
        for row in reader:
            lomaji = row.get('lomaji', '')
            poj = converter.convert_text(lomaji)
            row['POJ'] = poj
            writer.writerow(row)
            count += 1
            
        print(f"Processed {count} rows.")

if __name__ == '__main__':
    base_dir = r"c:\Users\marti\Projects\asr_datasets_packing\datasets\cv-corpus-24.0-nan-tw\cv-corpus-24.0-2025-12-05\nan-tw"
    files = ["train_lomaji.csv", "dev_lomaji.csv", "test_lomaji.csv", "validated_lomaji.csv"]
    
    for fname in files:
        input_file = os.path.join(base_dir, fname)
        temp_file = os.path.join(base_dir, fname + ".tmp")
        if os.path.exists(input_file):
            print(f"Adding POJ to {fname}...")
            add_poj(input_file, temp_file)
            os.replace(temp_file, input_file)
            print(f"Updated {input_file}\n")
        else:
            print(f"File not found: {input_file}\n")
