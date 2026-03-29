import pandas as pd

orig_csv = r'c:\Users\marti\Projects\asr_datasets_packing\datasets\suisiann\SuiSiann.csv'
poj_csv = r'c:\Users\marti\Projects\asr_datasets_packing\datasets\suisiann\SuiSiann_Lomaji_POJ.csv'

print(f"Reading original: {orig_csv}")
df_orig = pd.read_csv(orig_csv)

print(f"Reading POJ: {poj_csv}")
df_poj = pd.read_csv(poj_csv)

assert len(df_orig) == len(df_poj), "Row counts do not match!"

# Select only the relevant columns from POJ
# Assuming '音檔' is the ID column
df_poj_subset = df_poj[['音檔', '羅馬字_POJ']]

# Drop the old '羅馬字' from original and merge the new one
df_orig = df_orig.drop(columns=['羅馬字'])
df_orig = pd.merge(df_orig, df_poj_subset, on='音檔', how='left')

# Rename the new column back to '羅馬字'
df_orig = df_orig.rename(columns={'羅馬字_POJ': '羅馬字'})

# Restore original column order if possible
cols = ['音檔', '來源', '漢字', '羅馬字', '長短']
df_orig = df_orig[cols]

print(f"Saving merged data back to {orig_csv}")
df_orig.to_csv(orig_csv, index=False, encoding='utf-8')
print("Done. 羅馬字 column has been replaced with 羅馬字_POJ.")
