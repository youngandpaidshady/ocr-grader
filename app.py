import os
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context
from flask_cors import CORS
import google.generativeai as genai
import pandas as pd
from thefuzz import process, fuzz
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Active working file path configured globally
WORKING_EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ActiveRoaster.xlsx")

# Configure Gemini AI
api_key = os.getenv("GEMINI_API_KEY")
if not api_key or api_key == "your_gemini_api_key_here":
    print("WARNING: GEMINI_API_KEY is not set correctly in .env file.")

genai.configure(api_key=api_key)

# Initialize Flask App
app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 # 100MB upload limit to support unlimited batches

# The prompt instructions for Gemini
SYSTEM_PROMPT = """
You are an expert OCR Assistant helping a teacher grade test scripts.
I will provide you with images of handwritten test scripts. 
For EACH image, extract the following information:
1. Student Name (usually found at the top, e.g., "Name: ...")
2. Student Class (usually found near the name, e.g., "Class: ...")
3. Score (This is typically handwritten in **red ink**, often as a fraction like "7/10" or just a number circled in red on the margin).
4. Confidence (How confident you are that the extracted data is correct: "High", "Medium", or "Low". Use "Low" when handwriting is very messy or fields are partially obscured.)

If an image is unreadable or you cannot find a specific field, return null for that field and set confidence to "Low".

Return exactly a JSON array of objects, one object for each image provided, in the exact same order as the images are provided.
Use the following JSON schema:
[
  {
    "name": "Extracted Name",
    "class": "Extracted Class",
    "score": "Extracted Score",
    "confidence": "High | Medium | Low"
  },
  ...
]

Return ONLY the raw JSON array. DO NOT wrap it in markdown block quotes like ```json ... ```.
"""

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload-batch', methods=['POST'])
def upload_batch():
    try:
        data = request.json
        if not data or 'images' not in data:
            return jsonify({"error": "No images provided"}), 400
        
        images_base64 = data['images']
        target_class = data.get('targetClass', '').strip()
        
        if not images_base64:
             return jsonify({"error": "Empty images list"}), 400

        print(f"Received a batch of {len(images_base64)} images for processing...")
        
        # Pull known names for this class to improve accuracy (Smart Name Matching)
        known_names_text = ""
        
        if target_class and os.path.exists(WORKING_EXCEL_PATH):
            try:
                # Find matching sheet based on formatting logic
                import re
                c_cleaned = re.sub(r'[^A-Z0-9]', '', target_class.upper())
                match = re.match(r'([A-Z]+)(\d+.*)', c_cleaned)
                sheet_target = f"{match.group(1)} {match.group(2)}" if match else (target_class.upper() or "Unknown Class")
                sheet_target = sheet_target[:31]
                
                sheets_dict = pd.read_excel(WORKING_EXCEL_PATH, sheet_name=None)
                if sheet_target in sheets_dict:
                    df_existing = sheets_dict[sheet_target]
                    if 'Name' in df_existing.columns:
                        known_names = df_existing['Name'].dropna().tolist()
                        if known_names:
                            known_names_text = f"\n\nCRITICAL INSTRUCTION: You are grading papers for class '{sheet_target}'. Here is the authoritative list of known student names in this class: {known_names}. If the handwritten name on the paper resembles any of these, you MUST output the exact spelling from this list. Do not invent new names."
            except Exception as e:
                print(f"Error checking known names: {e}")
        
        # Prepare contents for Gemini
        dynamic_prompt = SYSTEM_PROMPT + known_names_text
        
        # Concurrent processing: split images into chunks and process in parallel
        CHUNK_SIZE = 3  # Process 3 images per Gemini call for optimal speed/accuracy
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Attach global index to each image
        indexed_images = list(enumerate(images_base64))
        
        # Split images into chunks
        image_chunks = []
        for i in range(0, len(indexed_images), CHUNK_SIZE):
            image_chunks.append(indexed_images[i:i + CHUNK_SIZE])
        
        def process_chunk(chunk_indexed_images):
            """Process a chunk of images through Gemini."""
            contents = [dynamic_prompt]
            for idx, img_b64 in chunk_indexed_images:
                if 'base64,' in img_b64:
                    img_b64 = img_b64.split('base64,')[1]
                contents.append({
                    "mime_type": "image/jpeg",
                    "data": img_b64
                })
            response = model.generate_content(contents)
            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            try:
                results = json.loads(response_text.strip())
            except Exception as e:
                print(f"Error parsing JSON from Gemini: {e}")
                results = []
                
            # Map back the global index to the result
            paired_results = []
            for i, res in enumerate(results):
                if i < len(chunk_indexed_images):
                    global_idx = chunk_indexed_images[i][0]
                    
                    # Clean the score (e.g., "8/10" -> "8")
                    raw_score = str(res.get('score', '')).strip()
                    if raw_score and '/' in raw_score:
                        res['score'] = raw_score.split('/')[0].strip()
                        
                    paired_results.append({"index": global_idx, "result": res})
            return paired_results
        
        @stream_with_context
        def generate():
            if len(image_chunks) == 1:
                # Single chunk
                chunk_res = process_chunk(image_chunks[0])
                for r in chunk_res:
                    yield f"data: {json.dumps(r)}\n\n"
            else:
                # Concurrent processing of multiple chunks
                with ThreadPoolExecutor(max_workers=min(len(image_chunks), 4)) as executor:
                    futures = [executor.submit(process_chunk, chunk) for chunk in image_chunks]
                    for future in as_completed(futures):
                        try:
                            chunk_res = future.result()
                            for r in chunk_res:
                                yield f"data: {json.dumps(r)}\n\n"
                        except Exception as exc:
                            print(f'Chunk generated an exception: {exc}')
            yield "data: [DONE]\n\n"
        
        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        print(f"Error processing batch: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/start-blank-sheet', methods=['POST'])
def start_blank_sheet():
    """Initializes a blank active roster and clears the old one."""
    try:
        # Create a single empty dataframe with generic columns to start
        df = pd.DataFrame(columns=["Name", "Class"])
        df.to_excel(WORKING_EXCEL_PATH, index=False, sheet_name="General")
        return jsonify({"message": "Blank sheet initialized.", "status": "success"}), 200
    except Exception as e:
        print(f"Error starting blank sheet: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/upload-sheet', methods=['POST'])
def upload_sheet():
    """Accepts an Excel, CSV, or Text file from the frontend and saves it as the active roster."""
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    if file and file.filename.lower().endswith(('.xlsx', '.xls', '.csv', '.txt', '.md')):
        try:
            # Handle Raw Text Lists
            if file.filename.lower().endswith(('.txt', '.md')):
                # Read all lines
                content = file.read().decode('utf-8')
                lines = [line.strip().title() for line in content.split('\n') if line.strip()]
                
                # Check for empty file
                if not lines:
                    return jsonify({"error": "Text file is empty or contains only whitespace"}), 400
                
                # Create DataFrame
                df = pd.DataFrame({"Name": lines})
                
                # Infer Class Name from Filename (e.g., "SS1Q.txt" -> "SS 1Q")
                import re
                base_name = os.path.splitext(file.filename)[0].upper()
                c_clean = re.sub(r'[^A-Z0-9]', '', base_name)
                match = re.match(r'([A-Z]+)(\d+.*)', c_clean)
                sheet_name = f"{match.group(1)} {match.group(2)}" if match else (base_name or "General")
                sheet_name = sheet_name[:31] # Excel sheet length limit
                
                # Load existing or create new
                sheets_dict = {}
                if os.path.exists(WORKING_EXCEL_PATH):
                    try:
                         sheets_dict = pd.read_excel(WORKING_EXCEL_PATH, sheet_name=None)
                    except Exception as e:
                         print(f"Warning: Could not read existing excel file, creating fresh. {e}")
                
                # Update specific sheet and save all
                sheets_dict[sheet_name] = df
                with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
                    for s_name, s_df in sheets_dict.items():
                        s_df.to_excel(writer, index=False, sheet_name=s_name)
                        
            # Handle CSV
            elif file.filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
                base_name = os.path.splitext(file.filename)[0][:31] or "General"
                
                sheets_dict = {}
                if os.path.exists(WORKING_EXCEL_PATH):
                    try:
                         sheets_dict = pd.read_excel(WORKING_EXCEL_PATH, sheet_name=None)
                    except Exception:
                         pass
                sheets_dict[base_name] = df
                with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
                    for s_name, s_df in sheets_dict.items():
                        s_df.to_excel(writer, index=False, sheet_name=s_name)
                        
            # Handle Excel
            else:
                file.save(WORKING_EXCEL_PATH)
                
            return jsonify({"message": f"Roster '{file.filename}' successfully uploaded and parsed.", "status": "success"}), 200
        except Exception as e:
            print(f"Error saving uploaded sheet: {e}")
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid file type. Please upload Excel, CSV, or Text (.txt)."}), 400

@app.route('/paste-sheet', methods=['POST'])
def paste_sheet():
    """Accepts raw text pasted from the UI and saves it as the active roster sheet."""
    try:
        data = request.json
        if not data or 'rawText' not in data or 'targetClass' not in data:
            return jsonify({"error": "Missing rawText or targetClass"}), 400
            
        raw_text = data['rawText']
        target_class = data['targetClass'].strip()
        
        if not raw_text.strip():
            return jsonify({"error": "Pasted text is empty"}), 400
            
        if not target_class:
            return jsonify({"error": "Please provide a target class name"}), 400
            
        # Parse lines
        lines = [line.strip().title() for line in raw_text.split('\n') if line.strip()]
        
        if not lines:
            return jsonify({"error": "No valid names found in pasted text"}), 400
            
        # Create DataFrame
        df = pd.DataFrame({"Name": lines})
        
        # Format Class Name for Excel Sheet
        import re
        c_clean = re.sub(r'[^A-Z0-9]', '', target_class.upper())
        match = re.match(r'([A-Z]+)(\d+.*)', c_clean)
        sheet_name = f"{match.group(1)} {match.group(2)}" if match else (target_class.upper() or "General")
        sheet_name = sheet_name[:31] # Excel sheet length limit
        
        # Load existing or create new
        sheets_dict = {}
        if os.path.exists(WORKING_EXCEL_PATH):
            try:
                 sheets_dict = pd.read_excel(WORKING_EXCEL_PATH, sheet_name=None)
            except Exception as e:
                 print(f"Warning: Could not read existing excel file, creating fresh. {e}")
        
        # Update specific sheet and save all
        sheets_dict[sheet_name] = df
        with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
            for s_name, s_df in sheets_dict.items():
                s_df.to_excel(writer, index=False, sheet_name=s_name)
                
        return jsonify({"message": f"Successfully parsed {len(lines)} names into sheet '{sheet_name}'.", "status": "success"}), 200
        
    except Exception as e:
        print(f"Error processing pasted sheet: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/export-excel', methods=['POST'])
def export_excel():
    try:
        data = request.json
        if not data or 'results' not in data:
            return jsonify({"error": "No results provided for export"}), 400
            
        results = data['results']
        assessment_type = data.get('assessmentType', 'Score').strip()
        if not assessment_type:
            assessment_type = 'Score'
            
        if not results:
             return jsonify({"error": "No data to export"}), 400
             
        # Format the incoming data
        formatted_results = []
        import re
        for r in results:
            name = str(r.get('name', '')).strip().title()
            
            # Extract letters and numbers for class formatting (e.g., JSS1Q -> JSS 1Q)
            c_raw = str(r.get('class', '')).strip().upper()
            c_cleaned = re.sub(r'[^A-Z0-9]', '', c_raw)
            match = re.match(r'([A-Z]+)(\d+.*)', c_cleaned)
            class_name = f"{match.group(1)} {match.group(2)}" if match else (c_raw or "Unknown Class")
            
            score = str(r.get('score', '')).strip()
            
            if not name:
                continue
                
            formatted_results.append({
                'Name': name,
                'Class': class_name,
                assessment_type: score
            })
            
        if not formatted_results:
            return jsonify({"error": "No valid data could be formatted"}), 400
            
        df_new = pd.DataFrame(formatted_results)
        
        # Dictionary to hold dataframes for each sheet (Class)
        sheets_dict = {}
        
        # Load existing if available
        if os.path.exists(WORKING_EXCEL_PATH):
            try:
                sheets_dict = pd.read_excel(WORKING_EXCEL_PATH, sheet_name=None)
            except Exception as e:
                print(f"Could not read existing Excel: {e}")
                
        # Group by Class and process
        classes = df_new['Class'].unique()
        
        for c in classes:
            # Safe sheet name (Excel limits to 31 chars)
            sheet_name = str(c)[:31]
            if not sheet_name:
                sheet_name = "General"
                
            # Get the new data for this specific class
            class_data_new = df_new[df_new['Class'] == c][['Name', assessment_type]]
            
            # Keep only the last scanned entry if there are duplicate names in this single batch
            class_data_new = class_data_new.drop_duplicates(subset=['Name'], keep='last')
            
            if sheet_name in sheets_dict:
                df_existing = sheets_dict[sheet_name]
                
                # Ensure Name column exists
                if 'Name' not in df_existing.columns:
                    df_existing['Name'] = ''
                
                # Iterate over new grades to insert/update smartly
                for _, row in class_data_new.iterrows():
                    new_name = str(row['Name']).strip()
                    new_score = row[assessment_type]
                    
                    if not new_name:
                        continue
                        
                    # 1. Exact Match first
                    exact_match_idx = df_existing.index[df_existing['Name'].str.strip().str.lower() == new_name.lower()].tolist()
                    
                    if exact_match_idx:
                        # Update exact match using .loc to create the column safely if it doesn't exist
                        idx = exact_match_idx[0]
                        df_existing.loc[idx, assessment_type] = new_score
                    else:
                        # 2. Fuzzy Match via thefuzz
                        existing_names = df_existing['Name'].dropna().astype(str).tolist()
                        if existing_names:
                            # Use token set ratio which is good for rearranged names e.g. "Smith, John" vs "John Smith"
                            best_match_tuple = process.extractOne(new_name, existing_names, scorer=fuzz.token_set_ratio)
                            
                            # If we find a good enough match (e.g. 85+ out of 100)
                            if best_match_tuple and best_match_tuple[1] >= 85:
                                matched_name = best_match_tuple[0]
                                matched_idx = df_existing.index[df_existing['Name'] == matched_name].tolist()[0]
                                df_existing.loc[matched_idx, assessment_type] = new_score
                                print(f"Fuzzy Matched: OCR '{new_name}' -> DB '{matched_name}' ({best_match_tuple[1]}%)")
                                continue
                                
                        # 3. No match found, append new row
                        new_row_dict = {'Name': new_name, assessment_type: new_score}
                        df_existing = pd.concat([df_existing, pd.DataFrame([new_row_dict])], ignore_index=True)
                
                sheets_dict[sheet_name] = df_existing

                # No secondary merge is needed; the loop above handles matching and appending exactly.
                
                # Sort alphabetically by default
                df_existing = df_existing.sort_values(by='Name', ascending=True)
                
                # ---- Calculate Total Score and Position ----
                if 'Total Score' in df_existing.columns:
                    df_existing = df_existing.drop(columns=['Total Score'])
                if 'Position' in df_existing.columns:
                    df_existing = df_existing.drop(columns=['Position'])
                
                def parse_score(val):
                    try:
                        val_str = str(val).strip()
                        if not val_str or val_str.lower() in ['nan', 'none']:
                            return 0.0
                        if '/' in val_str:
                            val_str = val_str.split('/')[0]
                        return float(val_str)
                    except:
                        return 0.0
                        
                score_cols = [col for col in df_existing.columns if col not in ['Name', 'Class']]
                
                # Pandas 2.1+ deprecates applymap, use map or apply depending on version, apply(lambda x: x.map(parse_score)) is safe
                df_existing['Total Score'] = df_existing[score_cols].apply(lambda x: x.map(parse_score)).sum(axis=1)
                
                # Compute Ranking (min method handles ties: 1, 1, 3)
                df_existing['Rank'] = df_existing['Total Score'].rank(method='min', ascending=False)
                
                def format_position(rank):
                    if pd.isna(rank): return ''
                    r = int(rank)
                    if 11 <= (r % 100) <= 13:
                        suffix = 'th'
                    else:
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                    return f"{r}{suffix}"
                    
                df_existing['Position'] = df_existing['Rank'].apply(format_position)
                df_existing = df_existing.drop(columns=['Rank'])
                
                # Reorder so Total and Position are at the end
                final_cols = [c for c in df_existing.columns if c not in ['Total Score', 'Position']] + ['Total Score', 'Position']
                df_existing = df_existing[final_cols]
                # --------------------------------------------
                
                sheets_dict[sheet_name] = df_existing
            else:
                # Brand new class sheet
                class_data_new = class_data_new.sort_values(by='Name', ascending=True)
                
                # ---- Calculate Total Score and Position ----
                def parse_score(val):
                    try:
                        val_str = str(val).strip()
                        if not val_str or val_str.lower() in ['nan', 'none']:
                            return 0.0
                        if '/' in val_str:
                            val_str = val_str.split('/')[0]
                        return float(val_str)
                    except:
                        return 0.0
                
                class_data_new['Total Score'] = class_data_new[assessment_type].apply(parse_score)
                class_data_new['Rank'] = class_data_new['Total Score'].rank(method='min', ascending=False)
                
                def format_position(rank):
                    if pd.isna(rank): return ''
                    r = int(rank)
                    if 11 <= (r % 100) <= 13:
                        suffix = 'th'
                    else:
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                    return f"{r}{suffix}"
                    
                class_data_new['Position'] = class_data_new['Rank'].apply(format_position)
                class_data_new = class_data_new.drop(columns=['Rank'])
                sheets_dict[sheet_name] = class_data_new
                
        # Save all sheets back to Excel
        with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
            for s_name, df_sheet in sheets_dict.items():
                df_sheet.to_excel(writer, sheet_name=s_name, index=False)
                
        return jsonify({"message": f"Successfully mapped and saved {len(results)} grades to Active Sheet."}), 200

    except Exception as e:
        print(f"Excel export error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/download-sheet', methods=['GET'])
def download_sheet():
    """Returns the compiled Excel file to the user."""
    if os.path.exists(WORKING_EXCEL_PATH):
        try:
            return send_file(
                WORKING_EXCEL_PATH, 
                as_attachment=True, 
                download_name="OCR_Graded_Results.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": "No active sheet found. Start a batch first."}), 404

if __name__ == '__main__':
    # Make sure templates folder exists
    os.makedirs('templates', exist_ok=True)
    # Run server
    app.run(debug=True, host='0.0.0.0', port=5000)
