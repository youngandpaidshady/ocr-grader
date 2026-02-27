import os
import base64
import json
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context
from flask_cors import CORS
import google.generativeai as genai
import pandas as pd
from thefuzz import process, fuzz
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

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

# Configure SQLite Database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smartgrader.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Database Models
class ClassModel(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    students = db.relationship('StudentModel', backref='class_obj', lazy=True, cascade="all, delete-orphan")

class StudentModel(db.Model):
    __tablename__ = 'students'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    scores = db.relationship('ScoreModel', backref='student_obj', lazy=True, cascade="all, delete-orphan")
    enrollments = db.relationship('EnrollmentModel', backref='student_obj', lazy=True, cascade="all, delete-orphan")

class EnrollmentModel(db.Model):
    __tablename__ = 'enrollments'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    subject_name = db.Column(db.String(100), nullable=False)

class ScoreModel(db.Model):
    __tablename__ = 'scores'
    id = db.Column(db.Integer, primary_key=True)
    score_value = db.Column(db.String(50), nullable=False)
    assessment_type = db.Column(db.String(100), default='Score')
    subject_name = db.Column(db.String(100), default='Uncategorized', server_default='Uncategorized')
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)

from sqlalchemy import text
with app.app_context():
    db.create_all()
    try:
        db.session.execute(text("ALTER TABLE scores ADD COLUMN subject_name VARCHAR(100) DEFAULT 'Uncategorized'"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        pass

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

@app.route('/api/classes', methods=['GET', 'POST'])
def handle_classes():
    if request.method == 'GET':
        classes = ClassModel.query.order_by(ClassModel.name).all()
        # seen = set() # Original line
        unique_classes = []
        seen_names = set() # Added as per instruction
        for c in classes:
            normalized_name = c.name.lower().replace(" ", "") # Modified as per instruction
            if normalized_name not in seen_names: # Modified as per instruction
                seen_names.add(normalized_name) # Modified as per instruction
                unique_classes.append({"id": c.id, "name": c.name})
        return jsonify(unique_classes), 200
        
    if request.method == 'POST':
        # Support both JSON (fallback) and Form Data (new UI)
        if request.is_json:
            data = request.get_json()
            raw_name = data.get('name', '').strip()
            names_text = data.get('names_text', '')
            subject_name = data.get('subject', 'Uncategorized')
            if not subject_name.strip(): subject_name = 'Uncategorized'
            file = None
        else:
            raw_name = request.form.get('name', '').strip()
            names_text = request.form.get('names_text', '')
            subject_name = request.form.get('subject', 'Uncategorized')
            if not subject_name.strip(): subject_name = 'Uncategorized'
            file = request.files.get('file')

        if not raw_name:
            return jsonify({"error": "Class name is required"}), 400
            
        print("Upserting class: {}".format(raw_name))
            
        # Get or Create class (Upsert mechanism)
        c = ClassModel.query.filter(func.lower(ClassModel.name) == raw_name.lower()).first()
        if not c:
            c = ClassModel(name=raw_name)
            db.session.add(c)
            db.session.commit()
        
        names_added = 0
        scores_imported = 0
        
        # Process Pasted Text
        if names_text:
            lines = [line.strip().title() for line in names_text.split('\n') if line.strip()]
            for name in lines:
                if not StudentModel.query.filter_by(class_id=c.id, name=name).first():
                    db.session.add(StudentModel(class_id=c.id, name=name))
                    names_added += 1
            db.session.commit()
            
        # Process Uploaded File
        if file and file.filename:
            if file.filename.lower().endswith(('.txt', '.md')):
                content = file.read().decode('utf-8')
                lines = [line.strip().title() for line in content.split('\n') if line.strip()]
                for name in lines:
                    if not StudentModel.query.filter_by(class_id=c.id, name=name).first():
                        db.session.add(StudentModel(class_id=c.id, name=name))
                        names_added += 1
                db.session.commit()
            else:
                try:
                    if file.filename.lower().endswith('.csv'):
                        df = pd.read_csv(file)
                    else:
                        df = pd.read_excel(file)
                    
                    if 'Name' in df.columns:
                        for index, row in df.iterrows():
                            # Clean Name
                            name = str(row['Name']).strip().title()
                            if not name or name.lower() == 'nan' or name.lower() == 'none':
                                continue
                                
                            student = StudentModel.query.filter_by(class_id=c.id, name=name).first()
                            if not student:
                                student = StudentModel(class_id=c.id, name=name)
                                db.session.add(student)
                                db.session.commit() # Need ID for score associations
                                names_added += 1
                                
                            # Import Excel scores
                            for col in df.columns:
                                if col.strip() in ['Name', 'Total Score', 'Position', 'Rank'] or col.startswith('Unnamed'):
                                    continue
                                    
                                val = str(row[col]).strip()
                                if val and val.lower() != 'nan' and val.lower() != 'none':
                                    s_rec = ScoreModel.query.filter_by(
                                        student_id=student.id, 
                                        assessment_type=col.strip(), 
                                        subject_name=subject_name
                                    ).first()
                                    if s_rec:
                                        s_rec.score_value = val
                                    else:
                                        s_rec = ScoreModel(
                                            student_id=student.id, 
                                            score_value=val, 
                                            assessment_type=col.strip(), 
                                            subject_name=subject_name
                                        )
                                        db.session.add(s_rec)
                                        scores_imported += 1
                                        
                        db.session.commit()
                    else:
                        return jsonify({"error": "Excel/CSV file MUST contain a 'Name' column header."}), 400
                except Exception as e:
                    return jsonify({"error": "Error parsing file: {}".format(str(e))}), 500
                    
        msg = "Class '{}' ready. Added {} new students.".format(raw_name, names_added)
        if scores_imported > 0:
            msg += " Imported {} absolute scores.".format(scores_imported)
        elif names_text and names_added == 0:
            msg = "Class '{}' recognized. All provided students were already enrolled (0 duplicates injected). You are good to go!".format(raw_name)
            
        return jsonify({
            "success": True, 
            "message": msg
        }), 201

@app.route('/api/students', methods=['GET', 'POST'])
def handle_students():
    if request.method == 'GET':
        class_id = request.args.get('class_id')
        if not class_id:
            return jsonify({"error": "class_id is required"}), 400
        students = StudentModel.query.filter_by(class_id=class_id).order_by(StudentModel.name).all()
        return jsonify([{"id": s.id, "name": s.name} for s in students]), 200
        
    if request.method == 'POST':
        data = request.json
        name = data.get('name', '').strip().title()
        class_id = data.get('class_id')
        
        if not name or not class_id:
            return jsonify({"error": "name and class_id are required"}), 400
            
        c = ClassModel.query.get(class_id)
        if not c:
            return jsonify({"error": "Class not found"}), 404
            
        # Check if student exists
        existing = StudentModel.query.filter_by(class_id=class_id, name=name).first()
        if existing:
            return jsonify({"error": "Student '{}' already exists in this class.".format(name), "student": {"id": existing.id, "name": existing.name}}), 409
            
        s = StudentModel(class_id=class_id, name=name)
        db.session.add(s)
        db.session.commit()
        
        return jsonify({"message": "Student added successfully.", "student": {"id": s.id, "name": s.name}}), 201

@app.route('/api/enrollments', methods=['GET', 'POST'])
def handle_enrollments():
    if request.method == 'GET':
        class_id = request.args.get('class_id')
        subject_name = request.args.get('subject_name')
        if not class_id or not subject_name:
            return jsonify({"error": "class_id and subject_name are required"}), 400
            
        students = StudentModel.query.filter_by(class_id=class_id).all()
        enrolled_ids = []
        for s in students:
            enr = EnrollmentModel.query.filter_by(student_id=s.id, subject_name=subject_name).first()
            if enr:
                enrolled_ids.append(s.id)
                
        return jsonify({"enrolled": enrolled_ids}), 200
        
    if request.method == 'POST':
        data = request.json
        class_id = data.get('class_id')
        subject_name = data.get('subject_name')
        student_ids = data.get('student_ids', [])
        
        if not class_id or not subject_name:
            return jsonify({"error": "class_id and subject_name are required"}), 400
            
        # Clear existing enrollments for this class+subject
        students = StudentModel.query.filter_by(class_id=class_id).all()
        for s in students:
            EnrollmentModel.query.filter_by(student_id=s.id, subject_name=subject_name).delete()
            
        # Add new enrollments
        for sid in student_ids:
            enr = EnrollmentModel(student_id=sid, subject_name=subject_name)
            db.session.add(enr)
            
        db.session.commit()
        return jsonify({"message": "Enrollments updated successfully"}), 200

@app.route('/upload-scoresheet', methods=['POST'])
def upload_scoresheet():
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({"error": "No image provided"}), 400
            
        img_b64 = data['image']
        target_class = data.get('targetClass', '').strip()
        
        # Pull known names for this class
        known_names = []
        if target_class:
            try:
                c = ClassModel.query.filter(func.lower(ClassModel.name) == target_class.lower()).first()
                if c:
                    students = StudentModel.query.filter_by(class_id=c.id).all()
                    known_names = [s.name for s in students]
            except Exception as e:
                print("Error fetching known names: {}".format(e))
                
        # Engineer the Prompt per User Request
        roster_context = ""
        if known_names:
            roster_context = "\nCRITICAL ROSTER MAPPING: The students in this class are exactly: {}. You MUST map the extracted handwritten/typed names to exactly match a name from this list whenever visually possible. Do not invent alternate spellings if a direct visual resemblance exists in this roster.".format(known_names)
            
        system_prompt = """
You are an expert OCR Assistant helping a teacher digitize an entire grading score sheet.
I will provide you with an image of a score sheet (which may be handwritten or typed).
CRITICAL CONTEXT: The teacher has noted that some score sheets contain MULTIPLE columns of distinct grades for each student (e.g., a "1st CA" column, a "2nd CA" column, and an "Exam" column all on the same paper).

Your task is to:
1. Identify the table of students and extract ALL distinct score columns present.
2. Analyze the headers, title, or surrounding context to determine the precise names for these 'Assessment Types' (e.g., 1st CA, 2nd CA, Final Exam).
3. If only one column exists, infer its assessment type from context or label it "Score".

{}

Return EXACTLY a JSON object with two keys:
1. "assessment_types_found": A JSON array of strings listing the distinct assessment columns detected.
2. "records": A JSON array of objects. Each object must have a "name" string, and a "scores" dictionary mapping each detected assessment type string to its corresponding score value.

Use the following JSON schema:
{{
  "assessment_types_found": ["1st CA", "2nd CA", "Exam"],
  "records": [
    {{ 
       "name": "...", 
       "scores": {{ "1st CA": "...", "2nd CA": "...", "Exam": "..." }}
    }},
    {{ 
       "name": "...", 
       "scores": {{ "1st CA": "...", "2nd CA": "...", "Exam": "..." }}
    }}
  ]
}}

Return ONLY the raw JSON object. DO NOT wrap it in markdown block quotes like ```json ... ```.
""".format(roster_context)

        # Process with Gemini
        model = genai.GenerativeModel('gemini-2.5-flash')
        contents = [system_prompt, {"mime_type": "image/jpeg", "data": img_b64}]
        
        response = model.generate_content(contents)
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result_json = json.loads(raw_text.strip())
        return jsonify(result_json), 200
        
    except Exception as e:
        print("Error processing score sheet: {}".format(e))
        return jsonify({"error": str(e)}), 500

@app.route('/upload-batch', methods=['POST'])
def upload_batch():
    try:
        data = request.json
        if not data or 'images' not in data:
            return jsonify({"error": "No images provided"}), 400
        
        images_base64 = data['images']
        target_class = data.get('targetClass', '').strip()
        smart_instruction = data.get('smartInstruction', '').strip()
        
        if not images_base64:
             return jsonify({"error": "Empty images list"}), 400

        print("Received a batch of {} images for processing...".format(len(images_base64)))
        if smart_instruction:
            print(f"Smart Instruction Applied: {smart_instruction}")
        
        # Pull known names for this class to improve accuracy (Smart Name Matching)
        known_names_text = ""
        known_names = []
        
        if target_class:
            try:
                # Fetch class exactly as specified in the dropdown
                c = ClassModel.query.filter(func.lower(ClassModel.name) == target_class.lower()).first()
                if c:
                    students = StudentModel.query.filter_by(class_id=c.id).all()
                    known_names = [s.name for s in students]
                    if known_names:
                        known_names_text = "\n\nCRITICAL INSTRUCTION: You are grading papers for class '{}'. Here is the authoritative list of known student names in this class: {}. If the handwritten name on the paper resembles any of these, you MUST output the exact spelling from this list. Do not invent new names.".format(c.name, known_names)
            except Exception as e:
                print("Error checking known names: {}".format(e))
        
        # Prepare contents for Gemini
        dynamic_prompt = SYSTEM_PROMPT + known_names_text

        if smart_instruction:
            dynamic_prompt += f"\n\n--- TEACHER'S CUSTOM SMART INSTRUCTION ---\n{smart_instruction}\n--- END OF CUSTOM INSTRUCTION ---\nYou MUST strictly obey the above manual instruction given by the teacher when processing these images and finalizing the output JSON."
        
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
                print("Error parsing JSON from Gemini: {}".format(e))
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
                        
                    name = str(res.get('name', '')).strip().title()
                    confidence = str(res.get('confidence', 'high')).lower()
                    if target_class:
                        res['class'] = target_class

                    if name and known_names:
                        if confidence in ['low', 'medium'] or name not in known_names:
                            best_matches = process.extract(name, known_names, scorer=fuzz.token_set_ratio, limit=3)
                            
                            # Smart auto-correction if the top match is very high confidence and distinct
                            if best_matches and best_matches[0][1] >= 85:
                                # Check for ambiguity string ties
                                if len(best_matches) == 1 or best_matches[0][1] > best_matches[1][1] + 5:
                                    res['name'] = best_matches[0][0]
                                    res['needs_resolution'] = False
                                    res['fuzzy_matches'] = []
                                else:
                                    res['needs_resolution'] = True
                                    res['fuzzy_matches'] = [(m[0], m[1]) for m in best_matches]
                            elif best_matches:
                                res['needs_resolution'] = True
                                res['fuzzy_matches'] = [(m[0], m[1]) for m in best_matches]
                        
                    paired_results.append({"index": global_idx, "result": res})
            return paired_results
        
        @stream_with_context
        def generate():
            for chunk in image_chunks:
                try:
                    chunk_res = process_chunk(chunk)
                    for r in chunk_res:
                        yield "data: {}\n\n".format(json.dumps(r))
                except Exception as exc:
                    print('Chunk generated an exception: {}'.format(exc))
            yield "data: [DONE]\n\n"
        
        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        print("Error processing batch: {}".format(e))
        return jsonify({"error": str(e)}), 500


@app.route('/export-excel', methods=['POST'])
def export_excel():
    try:
        data = request.json
        if not data or 'results' not in data:
            return jsonify({"error": "No results provided for export"}), 400
            
        results = data['results']
        assessment_type = data.get('assessmentType', 'Score').strip()
        subject_name = data.get('subjectType', 'Uncategorized').strip()
        if not assessment_type:
            assessment_type = 'Score'
        if not subject_name:
            subject_name = 'Uncategorized'
            
        if not results:
             return jsonify({"error": "No data to export"}), 400
             
        # Map incoming OCR data to DB
        import re
        for r in results:
            name = str(r.get('name', '')).strip().title()
            
            # Extract letters and numbers for class formatting (e.g., JSS1Q -> JSS 1Q)
            c_raw = str(r.get('class', '')).strip().upper()
            c_cleaned = re.sub(r'[^A-Z0-9]', '', c_raw)
            match = re.match(r'([A-Z]+)(\d+.*)', c_cleaned)
            class_name = "{} {}".format(match.group(1), match.group(2)) if match else (c_raw or "Unknown Class")
            
            score_val = str(r.get('score', '')).strip()
            
            if not name:
                continue
                
            # Find class in DB
            c = ClassModel.query.filter(func.lower(ClassModel.name) == class_name.lower()).first()
            if not c:
                # If class doesn't exist, create it (fallback mechanism if front-end failed to send valid class)
                c = ClassModel(name=class_name)
                db.session.add(c)
                db.session.commit()
                
            # Find student
            student = StudentModel.query.filter_by(class_id=c.id, name=name).first()
            if not student:
                 # Attempt fuzzy match
                 existing_students = StudentModel.query.filter_by(class_id=c.id).all()
                 existing_names = [s.name for s in existing_students]
                 if existing_names:
                     best_match_tuple = process.extractOne(name, existing_names, scorer=fuzz.token_set_ratio)
                     if best_match_tuple and best_match_tuple[1] >= 85:
                         student = StudentModel.query.filter_by(class_id=c.id, name=best_match_tuple[0]).first()
                         
                 # If still no student, create
                 if not student:
                     student = StudentModel(class_id=c.id, name=name)
                     db.session.add(student)
                     db.session.commit()
                     
            # Save score safely under the Subject tag!
            score_record = ScoreModel.query.filter_by(student_id=student.id, assessment_type=assessment_type, subject_name=subject_name).first()
            if score_record:
                score_record.score_value = score_val
            else:
                score_record = ScoreModel(student_id=student.id, score_value=score_val, assessment_type=assessment_type, subject_name=subject_name)
                db.session.add(score_record)
            db.session.commit()
            
        # Now generate Excel from DB cleanly organized by Class and Subject
        classes = ClassModel.query.order_by(ClassModel.name).all()
        sheets_dict = {}
        
        for c in classes:
            all_students = StudentModel.query.filter_by(class_id=c.id).order_by(StudentModel.name).all()
            if not all_students:
                continue
                
            # Accumulate subjects currently known for this class
            class_subjects = set()
            for s in all_students:
                for score in ScoreModel.query.filter_by(student_id=s.id).all():
                    class_subjects.add(score.subject_name)
                    
            if not class_subjects:
                # Provide a blank worksheet if the class has absolutely no grades yet
                sheet_name = c.name[:31]
                data_rows = [{'Name': s.name} for s in all_students]
                sheets_dict[sheet_name] = pd.DataFrame(data_rows)
                continue
                
            for subj in class_subjects:
                # Format specific worksheet per subject limit max Excel width 31
                sheet_name = "{} - {}".format(c.name, subj)[:31]
                data_rows = []
                
                # Enrollment Guard: If ANY Selective Enrollments exist for this Subject, lock the roster
                enrollments = EnrollmentModel.query.filter_by(subject_name=subj).filter(EnrollmentModel.student_id.in_([s.id for s in all_students])).all()
                valid_student_ids = [e.student_id for e in enrollments] if enrollments else [s.id for s in all_students]
                
                filtered_students = [s for s in all_students if s.id in valid_student_ids]
                
                for s in filtered_students:
                    row = {'Name': s.name}
                    scores = ScoreModel.query.filter_by(student_id=s.id, subject_name=subj).all()
                    
                    if not scores and enrollments:
                        # If they are selectively enrolled but have no scores, STILL show them!
                        pass
                    elif not scores:
                        # Skip strictly general students who have absolutely zero grades logged under this specific Subject
                        continue

                    total_score = 0.0
                    for score in scores:
                        row[score.assessment_type] = score.score_value
                        # Auto tally
                        try:
                            val = str(score.score_value)
                            if '/' in val: val = val.split('/')[0]
                            total_score += float(val)
                        except:
                            pass
                    row['Total Score'] = total_score
                    data_rows.append(row)
                    
                if not data_rows:
                    continue
                    
                df = pd.DataFrame(data_rows)
                
                # Ranking
                df['Rank'] = df['Total Score'].rank(method='min', ascending=False)
                
                def format_position(rank):
                    if pd.isna(rank): return ''
                    r = int(rank)
                    if 11 <= (r % 100) <= 13: return "{}th".format(r)
                    suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(r % 10, 'th')
                    return "{}{}".format(r, suffix)
                    
                df['Position'] = df['Rank'].apply(format_position)
                df = df.drop(columns=['Rank'])
                
                sheets_dict[sheet_name] = df
            
        if not sheets_dict:
             # Create an empty template if absolutely no data exists
             sheets_dict['General'] = pd.DataFrame(columns=["Name", "Class", "Total Score", "Position"])
             
        # Generate the Excel file
        with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
            for s_name, df_sheet in sheets_dict.items():
                df_sheet.to_excel(writer, sheet_name=s_name, index=False)
                
        return jsonify({"message": "Successfully mapped grades to Database and generated Active Sheet."}), 200

    except Exception as e:
        print("Excel export error: {}".format(e))
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

@app.route('/api/extract-names', methods=['POST'])
def extract_names():
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({"error": "No image provided"}), 400
            
        img_b64 = data['image']
        target_class = data.get('targetClass', '').strip()
        
        known_names = []
        if target_class:
            c = ClassModel.query.filter(func.lower(ClassModel.name) == target_class.lower()).first()
            if c:
                students = StudentModel.query.filter_by(class_id=c.id).all()
                known_names = [s.name for s in students]
                
        system_prompt = """
You are an OCR assistant. I will provide an image of a handwritten or typed list of student names.
Extract the names you see.

Return EXACTLY a JSON array of strings, where each string is an extracted name.
For example: ["John Doe", "Jane Smith"]

Return ONLY the raw JSON array. DO NOT wrap it in markdown block quotes like `json ... `.
"""
        if known_names:
            system_prompt += "\nCRITICAL CONTEXT: Here is the authoritative list of known students in this class: {}. You MUST map the extracted handwritten names to exactly match a name from this list whenever visually possible.".format(known_names)
            
        model = genai.GenerativeModel('gemini-2.5-flash')
        contents = [system_prompt, {"mime_type": "image/jpeg", "data": img_b64}]
        
        response = model.generate_content(contents)
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result_json = json.loads(raw_text.strip())
        
        final_names = []
        for name in result_json:
            name_str = str(name).strip().title()
            if known_names:
                best_matches = process.extract(name_str, known_names, scorer=fuzz.token_set_ratio, limit=1)
                if best_matches and best_matches[0][1] >= 85:
                    final_names.append(best_matches[0][0])
                else:
                    final_names.append(name_str)
            else:
                final_names.append(name_str)
                
        return jsonify({"names": list(set(final_names))}), 200
        
    except Exception as e:
        print("Error extracting names: {}".format(e))
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Make sure templates folder exists
    os.makedirs('templates', exist_ok=True)
    # Run server
    app.run(debug=True, host='0.0.0.0', port=5000)
