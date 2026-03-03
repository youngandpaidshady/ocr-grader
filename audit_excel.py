import pandas as pd, json

xf = pd.ExcelFile('Mathematics_1stTerm_SS1.xlsx')
with open('audit_output.txt', 'w') as out:
    out.write('SHEET NAMES: {}\n'.format(json.dumps(xf.sheet_names)))
    
    for sn in xf.sheet_names:
        df = pd.read_excel('Mathematics_1stTerm_SS1.xlsx', sheet_name=sn, skiprows=4)
        out.write('\n=== SHEET: {} ===\n'.format(sn))
        out.write('COLUMNS: {}\n'.format(list(df.columns)))
        out.write('ROWS: {}\n'.format(len(df)))
        # Only show our 3 test students
        subset = df[df['Name'].isin(['Aishat Musa', 'Bola Ahmed', 'Chidi Obi'])]
        out.write(subset.to_string() + '\n')

print('Done')
