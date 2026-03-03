# -*- coding: utf-8 -*-
"""Patch the export_excel sheet loop to match the physical Nigerian mark book exactly."""
import io

with io.open('c:/Users/Administrator/Desktop/Script recorder/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the sheet generation block
old_block = '''            for class_name, rows in classes_in_level.items():
                all_terms = ["1st Term", "2nd Term", "3rd Term", "Annual"]
                base_df = pd.DataFrame(rows)
                standard_cols = ['Name', 'Class', '1st CA', '2nd CA', 'Open Day', 'Note', 'Assignment', 'Total CA', 'Exam', 'Grand Total', 'Grade', 'Remarks']
                
                for t in all_terms:
                    sheet_name = "{} - {}".format(class_name[:20], t[:10])
                    
                    if t == term:
                        df = base_df.copy()
                        rank_col = 'Grand Total' if 'Grand Total' in df.columns and len(df['Grand Total'].dropna()) > 0 else (
                                   'Total CA' if 'Total CA' in df.columns and len(df['Total CA'].dropna()) > 0 else None)
                        if rank_col:
                            numeric_scores = pd.to_numeric(df[rank_col], errors='coerce')
                            df['Rank'] = numeric_scores.rank(method='min', ascending=False)
                            def format_position(rank):
                                if pd.isna(rank): return ''
                                r = int(rank)
                                if 11 <= (r % 100) <= 13: return "{}th".format(r)
                                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                                return "{}{}".format(r, suffix)
                            df['Position'] = df['Rank'].apply(format_position)
                            df = df.drop(columns=['Rank'])
                        else:
                            df['Position'] = ''
                    else:
                        empty_rows = [{"Name": r.get('Name'), "Class": r.get('Class')} for r in rows]
                        df = pd.DataFrame(empty_rows)
                        df['Position'] = ''
                        
                    past_term_cols = [c for c in list(df.columns) if "Term Total" in c or "Term Average" in c]
                    final_cols = ['Name', 'Class'] + past_term_cols
                    
                    for col in standard_cols[2:]:
                        final_cols.append(col)
                        if col not in df.columns:
                            df[col] = ''
                            
                    existing_custom = [c for c in df.columns if c not in final_cols and c not in ['Position', 'Rank']]
                    final_cols.extend(existing_custom)
                    
                    if 'Position' not in final_cols:
                        final_cols.append('Position')
                        
                    df = df[final_cols]
                    df = df.fillna('')
                    sheets_dict[sheet_name] = df'''

new_block = '''            for class_name, rows in classes_in_level.items():
                # Exactly 3 terms as per physical mark book — NO Annual tab
                all_terms = ["1st Term", "2nd Term", "3rd Term"]
                base_df = pd.DataFrame(rows)
                standard_ca_cols = ['1st CA', '2nd CA', 'Open Day', 'Note', 'Assignment']
                
                # Build a lookup of previous term Grand Totals from existingRecords
                prev_term_totals = {}  # {student_name: {"1st Term": total, "2nd Term": total}}
                if existing_records and isinstance(existing_records, list):
                    for rec in existing_records:
                        rname = str(rec.get('Name', '')).strip().title()
                        if not rname:
                            continue
                        if rname not in prev_term_totals:
                            prev_term_totals[rname] = {}
                        # Check for previous term total columns
                        for prev_key in ['1st Term Total', '2nd Term Total', '3rd Term Total']:
                            val = rec.get(prev_key)
                            if val is not None and str(val).strip() != '':
                                try:
                                    prev_term_totals[rname][prev_key] = float(val)
                                except (ValueError, TypeError):
                                    pass
                        # Also check Grand Total as a fallback for the term
                        gt = rec.get('Grand Total')
                        if gt is not None and str(gt).strip() != '':
                            try:
                                prev_term_totals[rname]['_grand_total'] = float(gt)
                            except (ValueError, TypeError):
                                pass
                
                for t in all_terms:
                    sheet_name = "{} - {}".format(class_name[:20], t[:10])
                    
                    if t == term:
                        # Active term — use the populated data
                        df = base_df.copy()
                    else:
                        # Inactive term — blank template with student names
                        empty_rows = [{"Name": r.get('Name'), "Class": r.get('Class')} for r in rows]
                        df = pd.DataFrame(empty_rows)
                    
                    # Build standard columns: CA1-5, Total CA, Exam, Grand Total, Grade, Remarks
                    for col in standard_ca_cols + ['Total CA', 'Exam', 'Grand Total', 'Grade', 'Remarks']:
                        if col not in df.columns:
                            df[col] = ''
                    
                    # === CUMULATIVE COLUMNS based on term ===
                    if t == "2nd Term":
                        # Add "1st Term Total" column pulled from previous data
                        first_totals = []
                        for _, row in df.iterrows():
                            sname = str(row.get('Name', '')).strip().title()
                            pt = prev_term_totals.get(sname, {})
                            val = pt.get('1st Term Total', pt.get('_grand_total', ''))
                            first_totals.append(val)
                        df['1st Term Total'] = first_totals
                        
                        # "1st & 2nd" = 1st Term Total + 2nd Term Grand Total
                        cumulative = []
                        for _, row in df.iterrows():
                            t1 = row.get('1st Term Total', '')
                            t2 = row.get('Grand Total', '')
                            try:
                                t1_val = float(t1) if t1 != '' else None
                                t2_val = float(t2) if t2 != '' else None
                                if t1_val is not None and t2_val is not None:
                                    cumulative.append(round(t1_val + t2_val, 1))
                                else:
                                    cumulative.append('')
                            except (ValueError, TypeError):
                                cumulative.append('')
                        df['1st & 2nd'] = cumulative
                        
                    elif t == "3rd Term":
                        # Add "1st Term Total" and "2nd Term Total" columns
                        first_totals = []
                        second_totals = []
                        for _, row in df.iterrows():
                            sname = str(row.get('Name', '')).strip().title()
                            pt = prev_term_totals.get(sname, {})
                            first_totals.append(pt.get('1st Term Total', ''))
                            second_totals.append(pt.get('2nd Term Total', ''))
                        df['1st Term Total'] = first_totals
                        df['2nd Term Total'] = second_totals
                        
                        # "1st 2nd & 3rd" = sum of all 3 Grand Totals
                        cumulative = []
                        averages = []
                        for _, row in df.iterrows():
                            t1 = row.get('1st Term Total', '')
                            t2 = row.get('2nd Term Total', '')
                            t3 = row.get('Grand Total', '')
                            vals = []
                            for v in [t1, t2, t3]:
                                try:
                                    if v != '' and v is not None:
                                        vals.append(float(v))
                                except (ValueError, TypeError):
                                    pass
                            if vals:
                                total = round(sum(vals), 1)
                                avg = round(total / 3.0, 1)
                                cumulative.append(total)
                                averages.append(avg)
                            else:
                                cumulative.append('')
                                averages.append('')
                        df['1st 2nd & 3rd'] = cumulative
                        df['Average'] = averages
                    
                    # === BUILD FINAL COLUMN ORDER ===
                    final_cols = ['Name', 'Class']
                    for col in standard_ca_cols:
                        final_cols.append(col)
                    final_cols.extend(['Total CA', 'Exam', 'Grand Total'])
                    
                    # Add cumulative columns in the right position
                    if t == "2nd Term":
                        final_cols.extend(['1st Term Total', '1st & 2nd'])
                    elif t == "3rd Term":
                        final_cols.extend(['1st Term Total', '2nd Term Total', '1st 2nd & 3rd', 'Average'])
                    
                    final_cols.extend(['Grade', 'Remarks'])
                    
                    # === RANKING ===
                    if t == "3rd Term" and 'Average' in df.columns:
                        # 3rd term: rank by Average
                        numeric_avg = pd.to_numeric(df['Average'], errors='coerce')
                        if numeric_avg.dropna().shape[0] > 0:
                            df['Rank'] = numeric_avg.rank(method='min', ascending=False)
                            def format_position(rank):
                                if pd.isna(rank): return ''
                                r = int(rank)
                                if 11 <= (r % 100) <= 13: return "{}th".format(r)
                                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                                return "{}{}".format(r, suffix)
                            df['Position'] = df['Rank'].apply(format_position)
                            df = df.drop(columns=['Rank'])
                        else:
                            df['Position'] = ''
                    elif t == term:
                        # Active term: rank by Grand Total or Total CA
                        rank_col = 'Grand Total' if 'Grand Total' in df.columns and df['Grand Total'].replace('', None).dropna().shape[0] > 0 else (
                                   'Total CA' if 'Total CA' in df.columns and df['Total CA'].replace('', None).dropna().shape[0] > 0 else None)
                        if rank_col:
                            numeric_scores = pd.to_numeric(df[rank_col], errors='coerce')
                            df['Rank'] = numeric_scores.rank(method='min', ascending=False)
                            def format_position(rank):
                                if pd.isna(rank): return ''
                                r = int(rank)
                                if 11 <= (r % 100) <= 13: return "{}th".format(r)
                                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                                return "{}{}".format(r, suffix)
                            df['Position'] = df['Rank'].apply(format_position)
                            df = df.drop(columns=['Rank'])
                        else:
                            df['Position'] = ''
                    else:
                        df['Position'] = ''
                    
                    final_cols.append('Position')
                    
                    # Ensure all columns exist
                    for col in final_cols:
                        if col not in df.columns:
                            df[col] = ''
                    
                    df = df[final_cols]
                    df = df.fillna('')
                    sheets_dict[sheet_name] = df'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with io.open('c:/Users/Administrator/Desktop/Script recorder/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Replaced sheet generation block')
else:
    print('ERROR: Old block not found in app.py')
