import pandas as pd
import json

df = pd.read_excel('Mathematics_1stTerm_SS1.xlsx', sheet_name='SS 1Q - 1st Term', skiprows=4)
# Print students we care about
subset = df[df['Name'].isin(['Aishat Musa', 'Bola Ahmed', 'Chidi Obi'])][['Name', 'Class', '1st CA', '2nd CA', 'Total CA']]
print(subset.to_json(orient='records', indent=2))
