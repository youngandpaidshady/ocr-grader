import os
import base64
import json
from flask import Flask, request, jsonify, render_template, send_file, Response, stream_with_context, make_response
from flask_cors import CORS
import google.generativeai as genai
import pandas as pd
from thefuzz import process, fuzz
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# Load environment variables
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
load_dotenv()

# Active working file path configured globally
WORKING_EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ActiveRoaster.xlsx")

# Configure AI Model
api_key = os.getenv("GEMINI_API_KEY")
if not api_key or api_key == "your_gemini_api_key_here":
    print("WARNING: API key is not set correctly in .env file.")

genai.configure(api_key=api_key)

# Initialize Flask App
app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 # 100MB upload limit to support unlimited batches

# Configure Database
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///smartgrader.db'
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

# The prompt instructions for the AI model
SYSTEM_PROMPT = """
You are an expert OCR Assistant helping a teacher grade test scripts.
I will provide you with images of handwritten test scripts. 
For EACH image, extract the following information:
1. Student Name (usually found at the top, e.g., "Name: ..."). Names may be long and written in ALL CAPS block letters (e.g., "ALARE OLUWAPELUMI OPEYEMI").
2. Student Class (usually found near the name, e.g., "Class: ..."). Classes might contain superscript letters (like SS1^Q or cursive); normalize this to standard text (e.g., "SS1Q").
3. Score (This is typically handwritten in **red ink**, often as a fraction like "8/10". Look carefully at the left margin—the score might be written VERY LARGE, spanning multiple lines, or circled. Always combine the numerator and denominator if you find them spread out.)
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
    # Attempt to bust cache so user sees the new loading overlay
    response = make_response(render_template('index.html', v=int(time.time())))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/api/recent-sessions', methods=['GET'])
def recent_sessions():
    """Returns classes with student counts and existing assessment types for the landing page."""
    try:
        classes = ClassModel.query.order_by(ClassModel.name).all()
        sessions = []
        seen_names = set()
        for c in classes:
            normalized = c.name.lower().replace(" ", "")
            if normalized in seen_names:
                continue
            seen_names.add(normalized)
            
            student_count = StudentModel.query.filter_by(class_id=c.id).count()
            if student_count == 0:
                continue
                
            # Find unique subjects and assessment types for this class
            students = StudentModel.query.filter_by(class_id=c.id).all()
            student_ids = [s.id for s in students]
            scores = ScoreModel.query.filter(ScoreModel.student_id.in_(student_ids)).all()
            
            subjects = {}
            for score in scores:
                subj = score.subject_name or 'Uncategorized'
                if subj not in subjects:
                    subjects[subj] = set()
                subjects[subj].add(score.assessment_type)
            
            if subjects:
                for subj, assessments in subjects.items():
                    sessions.append({
                        "class_id": c.id,
                        "class_name": c.name,
                        "subject": subj,
                        "student_count": student_count,
                        "existing_assessments": sorted(list(assessments)),
                        "suggested_next": suggest_next_assessment(sorted(list(assessments)))
                    })
            else:
                sessions.append({
                    "class_id": c.id,
                    "class_name": c.name,
                    "subject": None,
                    "student_count": student_count,
                    "existing_assessments": [],
                    "suggested_next": "1st CA"
                })
        
        return jsonify(sessions), 200
    except Exception as e:
        print("Error fetching recent sessions: {}".format(e))
        return jsonify([]), 200

def suggest_next_assessment(existing):
    """Suggest the next logical assessment type based on what already exists."""
    order = ["1st CA", "2nd CA", "Exam"]
    for a in order:
        if a not in existing:
            return a
    return "Score"

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

        # Process with AI model
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
        target_classes = data.get('targetClasses', [target_class] if target_class else [])
        smart_instruction = data.get('smartInstruction', '').strip()
        
        if not images_base64:
             return jsonify({"error": "Empty images list"}), 400

        print("Received a batch of {} images for processing...".format(len(images_base64)))
        if smart_instruction:
            print(f"Smart Instruction Applied: {smart_instruction}")
        
        # Build roster pool from ALL selected classes (3-Layer Routing)
        known_names_text = ""
        known_names = []
        class_rosters = {}  # {class_name: [student_names]}
        
        for tc in (target_classes if target_classes else [target_class]):
            if not tc:
                continue
            try:
                c = ClassModel.query.filter(func.lower(ClassModel.name) == tc.lower()).first()
                if c:
                    students = StudentModel.query.filter_by(class_id=c.id).all()
                    names = [s.name for s in students]
                    class_rosters[tc] = names
                    known_names.extend(names)
            except Exception as e:
                print("Error loading roster for {}: {}".format(tc, e))
        
        if known_names:
            known_names = list(set(known_names))  # Deduplicate
            known_names_text = "\n\nCRITICAL INSTRUCTION: You are grading papers for class(es) '{}'. Here is the authoritative list of known student names across all classes: {}. If the handwritten name on the paper resembles any of these, you MUST output the exact spelling from this list. Do not invent new names.".format(
                ', '.join(target_classes if target_classes else [target_class]),
                known_names
            )
        
        # Prepare contents for AI model
        dynamic_prompt = SYSTEM_PROMPT + known_names_text

        if smart_instruction:
            dynamic_prompt += f"\n\n--- TEACHER'S CUSTOM SMART INSTRUCTION ---\n{smart_instruction}\n--- END OF CUSTOM INSTRUCTION ---\nYou MUST strictly obey the above manual instruction given by the teacher when processing these images and finalizing the output JSON."
        
        # Concurrent processing: split images into chunks and process in parallel
        CHUNK_SIZE = 5  # Increased from 3 - Gemini handles 5 images well, fewer API calls
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Attach global index to each image
        indexed_images = list(enumerate(images_base64))
        
        # Split images into chunks
        image_chunks = []
        for i in range(0, len(indexed_images), CHUNK_SIZE):
            image_chunks.append(indexed_images[i:i + CHUNK_SIZE])
        
        def process_chunk(chunk_indexed_images):
            """Process a chunk of images through the AI model."""
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
                print("Error parsing JSON from model: {}".format(e))
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
                    
                    # === 3-LAYER CLASS ROUTING ===
                    # Layer 1: OCR - try to match AI-extracted class
                    import re as re_mod
                    extracted_class = str(res.get('class', '')).strip().upper()
                    matched_class = None
                    
                    if extracted_class:
                        ec_cleaned = re_mod.sub(r'[^A-Z0-9]', '', extracted_class)
                        for tc in target_classes:
                            tc_cleaned = re_mod.sub(r'[^A-Z0-9]', '', tc.upper())
                            if ec_cleaned == tc_cleaned:
                                matched_class = tc
                                break
                    
                    # Layer 2: Roster lookup - find which class this student is in
                    if not matched_class and name and class_rosters:
                        for class_name, roster in class_rosters.items():
                            if roster:
                                best = process.extractOne(name, roster, scorer=fuzz.token_set_ratio)
                                if best and best[1] >= 85:
                                    matched_class = class_name
                                    res['name'] = best[0]  # Also fix name spelling
                                    break
                    
                    # Layer 3: Fallback to primary target class
                    if matched_class:
                        res['class'] = matched_class
                    elif target_class:
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
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(process_chunk, chunk): chunk for chunk in image_chunks}
                for future in as_completed(futures):
                    try:
                        chunk_res = future.result()
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
        subject_name = data.get('subjectType', data.get('subjectName', 'Uncategorized')).strip()
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
        class_filter = data.get('classList', [])
        if class_filter:
            classes = ClassModel.query.filter(ClassModel.name.in_(class_filter)).order_by(ClassModel.name).all()
        else:
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
                data_rows = [{'Name': s.name, 'Class': c.name, 'Total Score': 0, 'Position': ''} for s in all_students]
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
                    row = {
                        'Name': s.name,
                        'Class': c.name # Explicitly add Class column to fix the "Class column is broken" issue
                    }
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
                
                # Enforce a strictly curated column order for better readability
                pref_order = ['Name', 'Class', '1st CA', '2nd CA', 'Assignment', 'Open Day', 'Exam']
                existing_cols = list(df.columns)
                custom_cols = [c for c in existing_cols if c not in pref_order and c not in ['Total Score', 'Position', 'Rank']]
                
                final_cols = []
                # 1. Preferred known columns in exact order
                for c in pref_order:
                    if c in existing_cols:
                        final_cols.append(c)
                # 2. Any dynamically added custom assessment types
                final_cols.extend(custom_cols)
                # 3. Always pin Total Score to the end
                if 'Total Score' in existing_cols: 
                    final_cols.append('Total Score')
                
                # Apply the reordering
                df = df[final_cols]
                
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
        # Generate the Excel file with openpyxl for Nigerian Scoresheet formatting
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils.dataframe import dataframe_to_rows
        from openpyxl.utils import get_column_letter

        with pd.ExcelWriter(WORKING_EXCEL_PATH, engine='openpyxl') as writer:
            for s_name, df_sheet in sheets_dict.items():
                
                # Check if this is a general list or a specific subject
                is_general = s_name == 'General'
                
                # If specific, try to parse Class and Subject out of the sheet name
                class_str = "All"
                subj_str = "Various"
                if " - " in s_name:
                    parts = s_name.split(" - ", 1)
                    class_str = parts[0]
                    subj_str = parts[1]
                
                # We won't use pandas to_excel directly for the formatting, we write rows manually
                df_sheet.to_excel(writer, sheet_name=s_name, index=False, startrow=4)
                
                worksheet = writer.sheets[s_name]
                
                # Row 1: Main Title
                worksheet.merge_cells('A1:G1')
                title_cell = worksheet['A1']
                title_cell.value = "QSI SMART GRADER SCORESHEET"
                title_cell.font = Font(bold=True, size=16)
                title_cell.alignment = Alignment(horizontal='center', vertical='center')
                
                # Row 2: Class
                worksheet.merge_cells('A2:E2')
                class_cell = worksheet['A2']
                class_cell.value = f"CLASS: {class_str}"
                class_cell.font = Font(bold=True, size=12)
                
                # Row 3: Subject
                worksheet.merge_cells('A3:E3')
                subj_cell = worksheet['A3']
                subj_cell.value = f"SUBJECT: {subj_str}"
                subj_cell.font = Font(bold=True, size=12)
                
                # Style the Data Headers (Row 5)
                header_font = Font(bold=True)
                header_fill = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")
                for col_idx in range(1, len(df_sheet.columns) + 1):
                    cell = worksheet.cell(row=5, column=col_idx)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal='center')
                    
                    # Auto-adjust column width
                    col_letter = get_column_letter(col_idx)
                    column_header = df_sheet.columns[col_idx - 1]
                    # Give 'Name' column more space
                    if column_header == 'Name':
                        worksheet.column_dimensions[col_letter].width = 30
                    elif column_header == 'Class':
                        worksheet.column_dimensions[col_letter].width = 15
                    else:
                        worksheet.column_dimensions[col_letter].width = 12
                        
                # Center align all data cells for numbers/scores
                for row in worksheet.iter_rows(min_row=6, max_row=worksheet.max_row, min_col=3, max_col=worksheet.max_column):
                    for cell in row:
                        cell.alignment = Alignment(horizontal='center')
                
        # Build sheets_summary for frontend tab rendering
        sheets_summary = {}
        for s_name, df_sheet in sheets_dict.items():
            parts = s_name.split(" - ", 1) if " - " in s_name else [s_name, "General"]
            sheets_summary[s_name] = {
                "columns": list(df_sheet.columns),
                "rows": df_sheet.fillna('').to_dict(orient='records'),
                "class": parts[0],
                "subject": parts[1] if len(parts) > 1 else "General"
            }

        return jsonify({
            "message": "Successfully mapped grades to Database and generated Active Sheet.",
            "sheets": sheets_summary
        }), 200

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
            response = make_response(send_file(
                WORKING_EXCEL_PATH, 
                as_attachment=True, 
                download_name="OCR_Graded_Results.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ))
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": "No active sheet found. Start a batch first."}), 404

@app.route('/api/upload-excel-scorelist', methods=['POST'])
def upload_excel_scorelist():
    """Smart parser for Excel scorelists to allow teachers to resume grading or bulk upload."""
    try:
        file = request.files.get('file')
        if not file:
            return jsonify({"error": "No file provided"}), 400
            
        if not file.filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({"error": "Only Excel or CSV files are supported"}), 400

        # Read Excel/CSV
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # --- Smart Detection Logic ---
        
        # 1. Detect Name Column
        name_col = None
        for col in df.columns:
            if 'name' in str(col).lower():
                name_col = col
                break
        if not name_col and not df.empty:
            name_col = df.columns[0] # Fallback to first column
        
        if not name_col:
            return jsonify({"error": "Could not identify a 'Name' column in the sheet."}), 400

        # 2. Detect Class and Subject Columns
        class_col = None
        subj_col = None
        for col in df.columns:
            c_low = str(col).lower()
            if 'class' in c_low: class_col = col
            if 'subject' in c_low: subj_col = col

        # 3. Detect Assessment types (any column that isn't name, class, subject, total, or pos)
        exclude = [name_col, class_col, subj_col, 'Total Score', 'Position', 'Rank', 'Total']
        assessment_types = [col for col in df.columns if col not in exclude and not str(col).startswith('Unnamed')]

        # 4. Extract data
        records = []
        detected_class = str(df[class_col].iloc[0]) if class_col and not df.empty else None
        detected_subject = str(df[subj_col].iloc[0]) if subj_col and not df.empty else None

        for _, row in df.iterrows():
            name = str(row[name_col]).strip().title()
            if not name or name.lower() in ['nan', 'none']: 
                continue
            
            scores = {}
            for atype in assessment_types:
                val = str(row[atype]).strip()
                if val and val.lower() not in ['nan', 'none']:
                    scores[atype] = val
                    
            r = {
                "name": name,
                "scores": scores,
                "class": str(row[class_col]).strip() if class_col else detected_class,
                "subject": str(row[subj_col]).strip() if subj_col else detected_subject
            }
            records.append(r)

        # 5. Magic Catch DB Sync: Ensure known names exist for future camera scans
        if detected_class:
            c = ClassModel.query.filter(func.lower(ClassModel.name) == detected_class.lower()).first()
            if not c:
                c = ClassModel(name=detected_class.title())
                db.session.add(c)
                db.session.commit()
            
            existing_student_names = [s.name.lower() for s in StudentModel.query.filter_by(class_id=c.id).all()]
            for r in records:
                s_name = r['name'].strip()
                if s_name.lower() not in existing_student_names:
                    new_student = StudentModel(name=s_name.title(), class_id=c.id)
                    db.session.add(new_student)
                    existing_student_names.append(s_name.lower())
            
            db.session.commit()

        return jsonify({
            "success": True,
            "assessment_types_found": assessment_types,
            "records": records,
            "detected_class": detected_class,
            "detected_subject": detected_subject,
            "filename": file.filename
        }), 200

    except Exception as e:
        print("Excel scorelist upload error: {}".format(e))
        return jsonify({"error": str(e)}), 500

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

@app.route('/api/ai-resolve', methods=['POST'])
def ai_resolve():
    """General-purpose AI resolver for sticky situations — called when the app hits ambiguity."""
    try:
        data = request.json
        situation = data.get('situation', '')
        context = data.get('context', {})
        
        resolver_prompts = {
            'unreadable_name': """A student's name could not be read from their test script.
Here is the context: {context}
The known student roster for this class is: {roster}
Based on any partial characters visible and the roster, suggest the most likely student name(s).
Return JSON: {{"suggestions": ["Name1", "Name2"], "confidence": "High|Medium|Low", "reasoning": "..."}}""",
            
            'weird_score': """A score was extracted but looks unusual: "{value}".
Common formats: "8", "8/10", "80%", "eight", "8 out of 10".
Normalize this to a simple integer score.
Return JSON: {{"normalized_score": 8, "original": "{value}", "reasoning": "..."}}""",
            
            'bulk_mismatch': """Multiple scanned names could not be matched to any student roster.
Unmatched names: {unmatched}
Available rosters: {rosters}
For each unmatched name, suggest the closest match from any roster, or mark as "new_student".
Return JSON: {{"matches": [{{"scanned": "...", "suggested": "...", "class": "...", "confidence": 0.85}}]}}""",
            
            'excel_format': """A teacher uploaded an Excel file but the format is unexpected.
Column headers found: {columns}
First 3 rows of data: {sample_rows}
Figure out which column is the student name, class, subject, and scores.
Return JSON: {{"name_col": "...", "class_col": "...", "subject_col": "...", "score_cols": ["..."], "header_row": 1, "reasoning": "..."}}""",
            
            'error_explain': """An error occurred in the grading app. Explain it in simple, friendly language for a teacher (not a developer).
Error: {error}
Context: What the teacher was doing: {action}
Return JSON: {{"friendly_message": "...", "suggestion": "...", "can_retry": true}}"""
        }
        
        template = resolver_prompts.get(situation, 
            'Analyze this situation and provide a helpful structured response: {context}')
        
        # Build the prompt with context
        prompt = template.format(**context) if context else template
        prompt += "\n\nReturn ONLY raw JSON. Do NOT wrap in markdown."
        
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result = json.loads(raw_text.strip())
        return jsonify({"success": True, "result": result}), 200
        
    except Exception as e:
        print("AI resolve error: {}".format(e))
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/smart-assistant', methods=['POST'])
def smart_assistant():
    """Smart conversational assistant with real DB data access. Designed by XO."""
    try:
        data = request.json
        message = data.get('message', '')
        context = data.get('context', {})
        current_screen = data.get('currentScreen', 'landing')
        
        # Build RICH context from actual database
        classes = ClassModel.query.order_by(ClassModel.name).all()
        class_data = {}
        for c in classes:
            students = StudentModel.query.filter_by(class_id=c.id).order_by(StudentModel.name).all()
            scores = ScoreModel.query.filter(
                ScoreModel.student_id.in_([s.id for s in students])
            ).all() if students else []
            
            # Get assessment types that exist for this class
            assessment_types = list(set(s.assessment_type for s in scores)) if scores else []
            subjects = list(set(s.subject_name for s in scores)) if scores else []
            
            class_data[c.name] = {
                "student_count": len(students),
                "students": [s.name for s in students[:15]],  # First 15 for context window
                "assessments": assessment_types,
                "subjects": subjects,
                "has_scores": len(scores) > 0
            }
        
        system_prompt = """You are the Smart Assistant for QSI Smart Grader, designed by XO.
You help teachers manage their grading workflow. Be warm, smart, and always helpful.

CURRENT STATE:
- Teacher is on the "{screen}" screen
- Session context: {context}

DATABASE (live data):
{db_data}

Based on the teacher's message, respond with a JSON object:
{{
    "response": "A friendly, smart message (1-3 sentences). Reference real data when possible.",
    "action": One of:
        "setup_session" - Pre-fill class/subject/assessment and navigate to scanning
        "view_standings" - Show results/analytics
        "add_student" - Add student to roster (will trigger safe-add flow)
        "edit_scores" - Navigate to review section
        "add_class" - Open add class modal
        "manage_enrollment" - Open enrollment management
        "export_data" - Download Excel
        "none" - Just informational response
    "params": {{
        "class_name": "...",     // for setup_session, add_student
        "subject_name": "...",   // for setup_session
        "assessment_type": "...", // for setup_session (suggest next logical one)
        "student_name": "..."    // for add_student
    }}
}}

RULES:
- For "setup_session": figure out the NEXT assessment to grade. If 1st CA exists, suggest 2nd CA. If both exist, suggest Exam.
- For "add_student": always confirm the class and spell the name clearly.
- Always reference real data: "You have 30 students in SS 1Q with 1st CA scores already."
- If unsure, ask a clarifying question (action: "none").
- Sign off important messages with a subtle "- XO Assistant" only on first interaction.

Return ONLY raw JSON. No markdown.""".format(
            screen=current_screen,
            context=json.dumps(context),
            db_data=json.dumps(class_data, indent=2)
        )
        
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content([system_prompt, message])
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result = json.loads(raw_text.strip())
        return jsonify(result), 200
        
    except Exception as e:
        print("Smart assistant error: {}".format(e))
        return jsonify({
            "response": "Sorry, I had trouble with that. Could you rephrase? - XO Assistant",
            "action": "none",
            "params": {}
        }), 200


@app.route('/api/safe-add-student', methods=['POST'])
def safe_add_student():
    """Safely add a student to a class roster with validation guards."""
    try:
        data = request.json
        student_name = str(data.get('studentName', '')).strip().title()
        class_name = str(data.get('className', '')).strip()
        force = data.get('force', False)  # Skip fuzzy check if user confirmed
        
        if not student_name or not class_name:
            return jsonify({"error": "Student name and class are required."}), 400
        
        # Guard 1: Validate class exists
        c = ClassModel.query.filter(func.lower(ClassModel.name) == class_name.lower()).first()
        if not c:
            return jsonify({
                "error": "Class '{}' not found. Please create the class first.".format(class_name),
                "suggestion": "add_class"
            }), 404
        
        # Guard 2: Check for exact duplicate
        existing = StudentModel.query.filter_by(class_id=c.id, name=student_name).first()
        if existing:
            return jsonify({
                "error": "{} is already in {}.".format(student_name, class_name),
                "duplicate": True
            }), 409
        
        # Guard 3: Fuzzy duplicate check - catch near-matches (skip if forced)
        all_students = StudentModel.query.filter_by(class_id=c.id).all()
        all_names = [s.name for s in all_students]
        if all_names and not force:
            best_match = process.extractOne(student_name, all_names, scorer=fuzz.token_set_ratio)
            if best_match and best_match[1] >= 88:
                return jsonify({
                    "warning": "A similar name exists: '{}' ({}% match). Is this the same student?".format(
                        best_match[0], best_match[1]
                    ),
                    "similar_name": best_match[0],
                    "similarity": best_match[1],
                    "needs_confirmation": True
                }), 200
        
        # All guards passed - safe to add
        new_student = StudentModel(class_id=c.id, name=student_name)
        db.session.add(new_student)
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "{} has been added to {}.".format(student_name, class_name),
            "student_id": new_student.id,
            "class_name": class_name,
            "total_students": len(all_students) + 1
        }), 201
        
    except Exception as e:
        print("Safe add student error: {}".format(e))
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



if __name__ == '__main__':
    # Make sure templates folder exists
    os.makedirs('templates', exist_ok=True)
    # Run server
    app.run(debug=True, host='0.0.0.0', port=5000)
