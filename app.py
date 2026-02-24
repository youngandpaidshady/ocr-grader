import os
import base64
import json
from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import google.generativeai as genai
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure Gemini AI
api_key = os.getenv("GEMINI_API_KEY")
if not api_key or api_key == "your_gemini_api_key_here":
    print("WARNING: GEMINI_API_KEY is not set correctly in .env file.")

genai.configure(api_key=api_key)

# Initialize Flask App
app = Flask(__name__)
CORS(app)

# The prompt instructions for Gemini
SYSTEM_PROMPT = """
You are an expert OCR Assistant helping a teacher grade test scripts.
I will provide you with images of handwritten test scripts. 
For EACH image, extract the following information:
1. Student Name (usually found at the top, e.g., "Name: ...")
2. Student Class (usually found near the name, e.g., "Class: ...")
3. Score (This is typically handwritten in **red ink**, often as a fraction like "7/10" or just a number circled in red on the margin).

If an image is unreadable or you cannot find a specific field, return null for that field.

Return exactly a JSON array of objects, one object for each image provided, in the exact same order as the images are provided.
Use the following JSON schema:
[
  {
    "name": "Extracted Name",
    "class": "Extracted Class",
    "score": "Extracted Score"
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
        
        if not images_base64:
             return jsonify({"error": "Empty images list"}), 400

        print(f"Received a batch of {len(images_base64)} images for processing...")
        
        # Prepare contents for Gemini
        contents = [SYSTEM_PROMPT]
        
        for index, img_b64 in enumerate(images_base64):
            # Clean base64 string if it contains the data uri prefix (e.g., data:image/jpeg;base64,...)
            if 'base64,' in img_b64:
                img_b64 = img_b64.split('base64,')[1]
                
            contents.append({
                "mime_type": "image/jpeg",
                "data": img_b64
            })
            
        # Call Gemini 2.5 Flash API
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(contents)
        
        response_text = response.text.strip()
        print("Raw Gemini Response:")
        print(response_text)
        
        # Clean up the output if Gemini still wrapped it in markdown
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
            
        results = json.loads(response_text.strip())
        
        return jsonify({"results": results})

    except Exception as e:
        print(f"Error processing batch: {e}")
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
            
            # Extract letters and numbers for class formatting (e.g., JSS1 -> JSS 1)
            c_raw = str(r.get('class', '')).strip().upper()
            c_cleaned = re.sub(r'[^A-Z0-9]', '', c_raw)
            match = re.match(r'([A-Z]+)(\d+)', c_cleaned)
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
        
        # Save to Excel
        output_filename = "Results.xlsx"
        try:
            output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_filename)
        except NameError:
            output_path = output_filename
            
        # Dictionary to hold dataframes for each sheet (Class)
        sheets_dict = {}
        
        # Load existing if available
        if os.path.exists(output_path):
            try:
                sheets_dict = pd.read_excel(output_path, sheet_name=None)
            except Exception as e:
                print(f"Could not read existing Excel: {e}")
                
        # Group by Class and process
        classes = df_new['Class'].unique()
        
        for c in classes:
            # Safe sheet name (Excel limits to 31 chars)
            sheet_name = str(c)[:31]
            if not sheet_name:
                sheet_name = "Unknown"
                
            # Get the new data for this specific class
            class_data_new = df_new[df_new['Class'] == c][['Name', assessment_type]]
            
            # Keep only the last scanned entry if there are duplicate names in this single batch
            class_data_new = class_data_new.drop_duplicates(subset=['Name'], keep='last')
            
            if sheet_name in sheets_dict:
                df_existing = sheets_dict[sheet_name]
                
                # Ensure Name column exists
                if 'Name' not in df_existing.columns:
                    df_existing['Name'] = ''
                    
                # Set index to Name for easier merging/updating
                df_existing = df_existing.set_index('Name')
                class_data_new = class_data_new.set_index('Name')
                
                if assessment_type in df_existing.columns:
                    # Update existing students' scores for this assessment, and add brand new students
                    df_existing.update(class_data_new)
                    
                    new_names = class_data_new.index.difference(df_existing.index)
                    if not new_names.empty:
                        df_existing = pd.concat([df_existing, class_data_new.loc[new_names]])
                else:
                    # New assessment type, do an outer join to keep all students and add the new column
                    df_existing = df_existing.join(class_data_new, how='outer')
                    
                # Reset index back to normal columns
                df_existing = df_existing.reset_index()
                
                # Sort alphabetically
                df_existing = df_existing.sort_values(by='Name', ascending=True)
                sheets_dict[sheet_name] = df_existing
            else:
                # Brand new class sheet
                class_data_new = class_data_new.sort_values(by='Name', ascending=True)
                sheets_dict[sheet_name] = class_data_new
                
        # Write all sheets back to Excel
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            for s_name, df_sheet in sheets_dict.items():
                df_sheet.to_excel(writer, sheet_name=s_name, index=False)
        
        return jsonify({"success": True, "message": f"Successfully merged and exported to {output_filename}", "path": output_filename})
        
    except Exception as e:
        print(f"Error exporting to Excel: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Make sure templates folder exists
    os.makedirs('templates', exist_ok=True)
    # Run server
    app.run(debug=True, host='0.0.0.0', port=5000)
