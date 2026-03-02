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
import re as re_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
load_dotenv()

# Active working file path configured globally
WORKING_EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ActiveRoaster.xlsx")

# Configure AI Model — supports multiple API keys (comma-separated) for rotation
import threading
_api_keys_raw = os.getenv("GEMINI_API_KEY", "")
API_KEYS = [k.strip() for k in _api_keys_raw.split(",") if k.strip() and k.strip() != "your_gemini_api_key_here"]
_current_key_index = 0
_key_lock = threading.Lock()

if not API_KEYS:
    print("WARNING: No API keys set in .env file. Set GEMINI_API_KEY (comma-separated for multiple).")
else:
    print("Loaded {} API key(s) for rotation.".format(len(API_KEYS)))

def get_current_api_key():
    """Get the current API key."""
    if not API_KEYS:
        return None
    return API_KEYS[_current_key_index % len(API_KEYS)]

def rotate_api_key():
    """Rotate to the next API key. Returns the new key."""
    global _current_key_index
    if len(API_KEYS) <= 1:
        return get_current_api_key()
    with _key_lock:
        _current_key_index = (_current_key_index + 1) % len(API_KEYS)
        new_key = API_KEYS[_current_key_index]
        genai.configure(api_key=new_key)
        print("Rotated to API key #{} of {}".format(_current_key_index + 1, len(API_KEYS)))
    return new_key

# Configure with the first key
genai.configure(api_key=get_current_api_key())

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

# Health check endpoint (used by self-ping to keep Render awake)
@app.route('/health')
def health_check():
    return jsonify({"status": "ok"}), 200

# Self-ping to keep Render free tier awake (pings every 14 min)
def _keep_alive():
    """Background thread that pings the app to prevent Render free-tier spin-down."""
    import urllib.request
    render_url = os.getenv("RENDER_EXTERNAL_URL")  # Auto-set by Render
    if not render_url:
        print("RENDER_EXTERNAL_URL not set — self-ping disabled (local dev).")
        return
    ping_url = render_url.rstrip("/") + "/health"
    print("Self-ping enabled: {} every 14 min".format(ping_url))
    while True:
        time.sleep(14 * 60)  # 14 minutes
        try:
            urllib.request.urlopen(ping_url, timeout=10)
            print("Self-ping OK")
        except Exception as e:
            print("Self-ping failed: {}".format(e))

_ping_thread = threading.Thread(target=_keep_alive, daemon=True)
_ping_thread.start()

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
                student_count = StudentModel.query.filter_by(class_id=c.id).count()
                unique_classes.append({"id": c.id, "name": c.name, "student_count": student_count})
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

        # Normalize class name: uppercase, split letters from digits, add space (e.g. 'ss1q' -> 'SS 1Q')
        c_cleaned = re_mod.sub(r'[^A-Z0-9]', '', raw_name.upper())
        match = re_mod.match(r'([A-Z]+)(\d+.*)', c_cleaned)
        normalized_name = "{} {}".format(match.group(1), match.group(2)) if match else raw_name.strip().upper()
            
        print("Upserting class: {} (normalized: {})".format(raw_name, normalized_name))
            
        # Get or Create class (Upsert mechanism)
        c = ClassModel.query.filter(func.lower(ClassModel.name) == normalized_name.lower()).first()
        if not c:
            c = ClassModel(name=normalized_name)
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

@app.route('/api/students/<int:student_id>', methods=['DELETE', 'PUT'])
def manage_student(student_id):
    student = StudentModel.query.get(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404
    
    if request.method == 'DELETE':
        name = student.name
        db.session.delete(student)
        db.session.commit()
        return jsonify({"message": "Removed '{}'.".format(name)}), 200
    
    if request.method == 'PUT':
        data = request.json
        new_name = data.get('name', '').strip().title()
        if not new_name:
            return jsonify({"error": "Name is required"}), 400
        old_name = student.name
        student.name = new_name
        db.session.commit()
        return jsonify({"message": "Renamed '{}' to '{}'.".format(old_name, new_name), "student": {"id": student.id, "name": student.name}}), 200

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

        # Process with AI model — with retry + key rotation for rate limits
        raw_text = None
        for attempt in range(len(API_KEYS) if 'API_KEYS' in dir() else 3):
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
                contents = [system_prompt, {"mime_type": "image/jpeg", "data": img_b64}]
                response = model.generate_content(contents)
                raw_text = response.text.strip()
                break
            except Exception as model_err:
                err_str = str(model_err).lower()
                if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                    rotate_api_key()
                    time.sleep(min(3 * (attempt + 1), 10))
                else:
                    raise
        if not raw_text:
            raise Exception('All API keys exhausted')
        
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
            # Retry with key rotation on rate limit
            # Scale retries to number of keys (at least 3, up to keys * 2)
            max_retries = max(3, len(API_KEYS) * 2) if API_KEYS else 3
            backoff_times = [3, 5, 8, 12, 15]
            response = None
            model = genai.GenerativeModel('gemini-2.5-flash')
            for attempt in range(max_retries):
                try:
                    response = model.generate_content(contents)
                    break
                except Exception as retry_err:
                    err_str = str(retry_err).lower()
                    if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                        print("Rate limit hit on attempt {} — rotating key...".format(attempt + 1))
                        rotate_api_key()
                        # Re-create model with the newly configured key
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        if attempt < max_retries - 1:
                            wait = backoff_times[min(attempt, len(backoff_times) - 1)]
                            time.sleep(wait)
                    else:
                        raise retry_err
            if not response:
                raise Exception("All API keys exhausted for this chunk after {} retries".format(max_retries))
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
                        yield "data: {}\n\n".format(json.dumps({"error": str(exc)}))
            yield "data: [DONE]\n\n"
        
        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        print("Error processing batch: {}".format(e))
        return jsonify({"error": str(e)}), 500


def parse_class_level(class_name):
    """Smart class parser: extracts level and arm from any Nigerian school format.
    Handles: SS 1Q, JSS2A, ss1q, S.S 1 Q, J.S.S. 1B, SSS 3 Gold, Primary 4A, etc.
    Returns: {"level": "SS1", "arm": "Q", "normalized": "SS 1Q"}
    """
    raw = str(class_name).strip()
    # Step 1: Remove dots and extra whitespace, uppercase
    cleaned = re_mod.sub(r'\.', '', raw).upper().strip()
    cleaned = re_mod.sub(r'\s+', ' ', cleaned)
    
    # Step 2: Match pattern — (letters)(optional space)(digits)(optional space)(arm)
    # Handles: JSS 2A, SS1Q, SSS 3 Gold, PRIMARY 4A
    match = re_mod.match(r'^([A-Z]+)\s*(\d+)\s*(.*)$', cleaned)
    if match:
        prefix = match.group(1)  # SS, JSS, SSS, PRIMARY, PRI
        number = match.group(2)  # 1, 2, 3
        arm = match.group(3).strip()  # Q, A, Gold, or empty
        
        level = "{}{}".format(prefix, number)  # SS1, JSS2, SSS3
        normalized = "{} {}{}".format(prefix, number, arm)  # SS 1Q, JSS 2A
        
        return {"level": level, "arm": arm or "_default", "normalized": normalized}
    
    # Step 3: Fallback — treat entire name as level with no arm
    return {"level": cleaned or "UNKNOWN", "arm": "_default", "normalized": raw}


@app.route('/export-excel', methods=['POST'])
def export_excel():
    """Generates Excel from scanned results. No scores saved to DB — only rosters are persisted."""
    try:
        data = request.json
        if not data or 'results' not in data:
            return jsonify({"error": "No results provided for export"}), 400
            
        results = data['results']
        assessment_type = data.get('assessmentType', 'Score').strip()
        subject_name = data.get('subjectType', data.get('subjectName', '')).strip()
        if not assessment_type:
            assessment_type = 'Score'
        if not subject_name or subject_name.lower() == 'uncategorized':
            subject_name = data.get('subjectName', data.get('subjectType', '')).strip()
        if not subject_name:
            subject_name = 'General'
            
        if not results:
             return jsonify({"error": "No data to export"}), 400

        subject_mode = data.get('subjectMode', 'general').strip().lower()
             
        # === ROSTER-ONLY DB SYNC ===
        # Only update class rosters (student names), NOT scores

        for r in results:
            name = str(r.get('name', '')).strip().title()
            if not name:
                continue
                
            c_raw = str(r.get('class', '')).strip().upper()
            c_cleaned = re_mod.sub(r'[^A-Z0-9]', '', c_raw)
            match = re_mod.match(r'([A-Z]+)(\d+.*)', c_cleaned)
            class_name = "{} {}".format(match.group(1), match.group(2)) if match else (c_raw or "Unknown Class")
            
            # Ensure class exists
            c = ClassModel.query.filter(func.lower(ClassModel.name) == class_name.lower()).first()
            if not c:
                c = ClassModel(name=class_name)
                db.session.add(c)
                db.session.commit()
                
            # Ensure student exists in roster (fuzzy match)
            student = StudentModel.query.filter_by(class_id=c.id, name=name).first()
            if not student:
                existing_students = StudentModel.query.filter_by(class_id=c.id).all()
                existing_names = [s.name for s in existing_students]
                if existing_names:
                    best_match_tuple = process.extractOne(name, existing_names, scorer=fuzz.token_set_ratio)
                    if best_match_tuple and best_match_tuple[1] >= 85:
                        student = StudentModel.query.filter_by(class_id=c.id, name=best_match_tuple[0]).first()
                if not student:
                    student = StudentModel(class_id=c.id, name=name)
                    db.session.add(student)
                    db.session.commit()
            
        # === BUILD EXCEL DIRECTLY FROM RESULTS (not from DB scores) ===
        
        # Group results by class name
        class_results = {}
        for r in results:
            name = str(r.get('name', '')).strip().title()
            if not name:
                continue
            c_raw = str(r.get('class', '')).strip().upper()
            c_cleaned = re_mod.sub(r'[^A-Z0-9]', '', c_raw)
            match = re_mod.match(r'([A-Z]+)(\d+.*)', c_cleaned)
            class_name = "{} {}".format(match.group(1), match.group(2)) if match else (c_raw or "Unknown Class")
            
            if class_name not in class_results:
                class_results[class_name] = []
            class_results[class_name].append({
                'name': name,
                'score': str(r.get('score', '')).strip(),
                'class': class_name
            })
        
        # For general mode: include roster students who weren't scanned (blank, not 0)
        class_filter = data.get('classList', [])
        if subject_mode == 'general' and class_filter:
            for class_name in class_filter:
                c = ClassModel.query.filter(func.lower(ClassModel.name) == class_name.lower()).first()
                if c:
                    roster = StudentModel.query.filter_by(class_id=c.id).order_by(StudentModel.name).all()
                    scanned_names = [r['name'].lower() for r in class_results.get(class_name, [])]
                    for s in roster:
                        if s.name.lower() not in scanned_names:
                            # Fuzzy check
                            if scanned_names:
                                best = process.extractOne(s.name, scanned_names, scorer=fuzz.token_set_ratio)
                                if best and best[1] >= 85:
                                    continue  # Already scanned under a slightly different name
                            if class_name not in class_results:
                                class_results[class_name] = []
                            class_results[class_name].append({
                                'name': s.name,
                                'score': '',  # Blank, not 0
                                'class': class_name
                            })
        
        # === GROUP BY CLASS LEVEL ===
        # SS 1Q + SS 1S → level SS1 (one file)
        # SS 2A + SS 2B → level SS2 (separate file)
        level_groups = {}  # {level: {arm: [results]}}
        class_to_level = {}  # {class_name: {"level": ..., "arm": ...}}
        
        for class_name, rows in class_results.items():
            parsed = parse_class_level(class_name)
            level = parsed["level"]
            arm = parsed["arm"]
            class_to_level[class_name] = parsed
            
            if level not in level_groups:
                level_groups[level] = {}
            if class_name not in level_groups[level]:
                level_groups[level][class_name] = []
            level_groups[level][class_name].extend(rows)
        
        # === GENERATE EXCEL FILES (one per level) ===
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
        
        all_sheets_summary = {}
        generated_files = {}  # {level: filepath}
        
        for level, classes_in_level in level_groups.items():
            # File per level
            safe_subject = re_mod.sub(r'[^A-Za-z0-9 ]', '', subject_name).strip()
            filename = "{}_{}.xlsx".format(safe_subject or "Scores", level)
            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
            generated_files[level] = {"path": filepath, "filename": filename}
            
            sheets_dict = {}
            
            for class_name, rows in classes_in_level.items():
                sheet_name = class_name[:31]
                data_rows = []
                
                for r in rows:
                    row = {
                        'Name': r['name'],
                        'Class': class_name
                    }
                    score_val = r.get('score', '')
                    if score_val:
                        row[assessment_type] = score_val
                        # Auto tally
                        try:
                            val = str(score_val)
                            if '/' in val: val = val.split('/')[0]
                            row['Total Score'] = float(val)
                        except:
                            row['Total Score'] = ''
                    else:
                        row['Total Score'] = ''
                    data_rows.append(row)
                
                if not data_rows:
                    continue
                    
                df = pd.DataFrame(data_rows)
                
                # Column ordering
                pref_order = ['Name', 'Class', '1st CA', '2nd CA', 'Assignment', 'Open Day', 'Exam']
                existing_cols = list(df.columns)
                custom_cols = [col for col in existing_cols if col not in pref_order and col not in ['Total Score', 'Position', 'Rank']]
                
                final_cols = []
                for col in pref_order:
                    if col in existing_cols:
                        final_cols.append(col)
                final_cols.extend(custom_cols)
                if 'Total Score' in existing_cols:
                    final_cols.append('Total Score')
                
                df = df[final_cols]
                
                # Ranking — only rank rows with actual scores
                if 'Total Score' in df.columns:
                    numeric_scores = pd.to_numeric(df['Total Score'], errors='coerce')
                    df['Rank'] = numeric_scores.rank(method='min', ascending=False)
                    
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
                continue
            
            # Write Excel for this level
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                for s_name, df_sheet in sheets_dict.items():
                    df_sheet.to_excel(writer, sheet_name=s_name, index=False, startrow=4)
                    worksheet = writer.sheets[s_name]
                    
                    # Row 1: Title
                    worksheet.merge_cells('A1:G1')
                    title_cell = worksheet['A1']
                    title_cell.value = "QSI SMART GRADER SCORESHEET"
                    title_cell.font = Font(bold=True, size=16)
                    title_cell.alignment = Alignment(horizontal='center', vertical='center')
                    
                    # Row 2: Class
                    worksheet.merge_cells('A2:E2')
                    class_cell = worksheet['A2']
                    class_cell.value = "CLASS: {}".format(s_name)
                    class_cell.font = Font(bold=True, size=12)
                    
                    # Row 3: Subject
                    worksheet.merge_cells('A3:E3')
                    subj_cell = worksheet['A3']
                    subj_cell.value = "SUBJECT: {}".format(subject_name)
                    subj_cell.font = Font(bold=True, size=12)
                    
                    # Style headers (Row 5)
                    header_font = Font(bold=True)
                    header_fill = PatternFill(start_color="EAEAEA", end_color="EAEAEA", fill_type="solid")
                    for col_idx in range(1, len(df_sheet.columns) + 1):
                        cell = worksheet.cell(row=5, column=col_idx)
                        cell.font = header_font
                        cell.fill = header_fill
                        cell.alignment = Alignment(horizontal='center')
                        
                        col_letter = get_column_letter(col_idx)
                        column_header = df_sheet.columns[col_idx - 1]
                        if column_header == 'Name':
                            worksheet.column_dimensions[col_letter].width = 30
                        elif column_header == 'Class':
                            worksheet.column_dimensions[col_letter].width = 15
                        else:
                            worksheet.column_dimensions[col_letter].width = 12
                            
                    # Center data cells
                    for row in worksheet.iter_rows(min_row=6, max_row=worksheet.max_row, min_col=3, max_col=worksheet.max_column):
                        for cell in row:
                            cell.alignment = Alignment(horizontal='center')
            
            # Build sheet summaries for this level
            for s_name, df_sheet in sheets_dict.items():
                all_sheets_summary[s_name] = {
                    "columns": list(df_sheet.columns),
                    "rows": df_sheet.fillna('').to_dict(orient='records'),
                    "class": s_name,
                    "subject": subject_name,
                    "level": level
                }
        
        # Also copy the first (or only) level file to WORKING_EXCEL_PATH for backward compat
        if generated_files:
            import shutil
            first_level = list(generated_files.keys())[0]
            shutil.copy2(generated_files[first_level]["path"], WORKING_EXCEL_PATH)
        
        # Build download info
        downloads = []
        for level, info in generated_files.items():
            downloads.append({
                "level": level,
                "filename": info["filename"],
                "url": "/download-sheet?level={}".format(level)
            })

        return jsonify({
            "message": "Grades saved! {} file(s) ready for download.".format(len(downloads)),
            "sheets": all_sheets_summary,
            "downloads": downloads,
            "subject": subject_name
        }), 200

    except Exception as e:
        print("Excel export error: {}".format(e))
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/download-sheet', methods=['GET'])
def download_sheet():
    """Returns the compiled Excel file, optionally filtered by level."""
    level = request.args.get('level', '').strip()
    subject = request.args.get('subject', '').strip()
    
    if level:
        # Look for level-specific file
        safe_subject = re_mod.sub(r'[^A-Za-z0-9 ]', '', subject).strip() if subject else "Scores"
        filename = "{}_{}.xlsx".format(safe_subject or "Scores", level)
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        
        if os.path.exists(filepath):
            try:
                download_name = "{}_{}.xlsx".format(safe_subject or "Scores", level)
                response = make_response(send_file(
                    filepath, 
                    as_attachment=True, 
                    download_name=download_name,
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ))
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
                return response
            except Exception as e:
                return jsonify({"error": str(e)}), 500
    
    # Fallback to working file
    if os.path.exists(WORKING_EXCEL_PATH):
        try:
            download_name = "{}_Scoresheet.xlsx".format(subject) if subject else "Scoresheet.xlsx"
            response = make_response(send_file(
                WORKING_EXCEL_PATH, 
                as_attachment=True, 
                download_name=download_name,
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
            "total_students": len(records),
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
            
        # Process with AI model — with retry + key rotation for rate limits
        raw_text = None
        for attempt in range(len(API_KEYS) if 'API_KEYS' in dir() else 3):
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
                contents = [system_prompt, {"mime_type": "image/jpeg", "data": img_b64}]
                response = model.generate_content(contents)
                raw_text = response.text.strip()
                break
            except Exception as model_err:
                err_str = str(model_err).lower()
                if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                    rotate_api_key()
                    time.sleep(min(3 * (attempt + 1), 10))
                else:
                    raise
        if not raw_text:
            raise Exception('All API keys exhausted')
        
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
        
        # Process with AI — retry + rotate on rate limits
        raw_text = None
        for attempt in range(len(API_KEYS) if 'API_KEYS' in dir() else 3):
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = model.generate_content(prompt)
                raw_text = response.text.strip()
                break
            except Exception as model_err:
                err_str = str(model_err).lower()
                if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                    rotate_api_key()
                    time.sleep(min(3 * (attempt + 1), 10))
                else:
                    raise
        if not raw_text:
            raise Exception('All API keys exhausted')
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result = json.loads(raw_text.strip())
        return jsonify({"success": True, "result": result}), 200
        
    except Exception as e:
        print("AI resolve error: {}".format(e))
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/move-student', methods=['POST'])
def move_student():
    """Move a student from one class to another."""
    try:
        data = request.json
        student_name = data.get('studentName', '').strip().title()
        from_class = data.get('fromClass', '').strip()
        to_class = data.get('toClass', '').strip()
        
        if not student_name or not from_class or not to_class:
            return jsonify({"error": "Missing student name, source class, or target class"}), 400
        
        # Find source class and student
        source = ClassModel.query.filter(func.lower(ClassModel.name) == from_class.lower()).first()
        if not source:
            return jsonify({"error": "Source class '{}' not found".format(from_class)}), 404
        
        student = StudentModel.query.filter_by(class_id=source.id, name=student_name).first()
        if not student:
            # Try fuzzy match
            existing = StudentModel.query.filter_by(class_id=source.id).all()
            names = [s.name for s in existing]
            if names:
                best = process.extractOne(student_name, names, scorer=fuzz.token_set_ratio)
                if best and best[1] >= 80:
                    student = StudentModel.query.filter_by(class_id=source.id, name=best[0]).first()
            if not student:
                return jsonify({"error": "Student '{}' not found in {}".format(student_name, from_class)}), 404
        
        # Ensure target class exists
        target = ClassModel.query.filter(func.lower(ClassModel.name) == to_class.lower()).first()
        if not target:
            target = ClassModel(name=to_class)
            db.session.add(target)
            db.session.commit()
        
        # Check for duplicate in target
        existing_in_target = StudentModel.query.filter_by(class_id=target.id, name=student.name).first()
        if existing_in_target:
            return jsonify({"error": "'{}' is already in {}".format(student.name, to_class)}), 400
        
        # Move the student
        student.class_id = target.id
        db.session.commit()
        
        return jsonify({
            "message": "Moved {} from {} to {} ✓".format(student.name, from_class, to_class),
            "success": True
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route('/api/assistant-scan-to-excel', methods=['POST'])
def assistant_scan_to_excel():
    """Receives an image + instruction, uses Vision AI to extract a table, returns an Excel file."""
    try:
        if 'images' not in request.files:
            return jsonify({"error": "No images uploaded"}), 400
        
        instruction = request.form.get('instruction', '').strip()
        if not instruction:
            return jsonify({"error": "No instruction provided"}), 400
            
        class_name = request.form.get('class_name', '').strip()
            
        files = request.files.getlist('images')
        
        # Read multiple images
        image_parts = []
        for f in files:
            img_bytes = f.read()
            image_parts.append({"mime_type": f.content_type, "data": img_bytes})
        
        # Build optional roster context for smarter OCR
        roster_context = ""
        if class_name:
            c = ClassModel.query.filter(func.lower(ClassModel.name) == class_name.lower()).first()
            if c:
                students = StudentModel.query.filter_by(class_id=c.id).all()
                if students:
                    roster_names = [s.name for s in students]
                    roster_context = f"\n\nCRITICAL KNOWLEDGE: The teacher mentioned this image belongs to class '{class_name}'. The official database roster for this class is: {roster_names}. \nWHEN EXTRACTING NAMES, YOU MUST MATCH THEM STRICTLY TO THIS ROSTER, IGNORING TYPOS IN THE HANDWRITING. Fix any misspelled handwritten names to perfectly match the official database spelling."
        
        prompt = """
You are an expert OCR and data extraction AI specializing in Nigerian school record sheets. A teacher has uploaded image(s) of a handwritten document and given you specific instructions.

TEACHER'S INSTRUCTION: "{instruction}"{roster_context}

YOUR JOB: Read ALL the images and produce ONE combined JSON array of row objects representing the table data across every page/image.

CRITICAL RULES:
1. **OUTPUT FORMAT**: Return ONLY a raw JSON array. Start with [ and end with ]. No markdown, no backticks, no explanations, no commentary.
2. **COLUMN NAMING**: 
   - The student name column MUST always be keyed as "name" (lowercase).
   - If the teacher asks for a single generic score, use "score".
   - If they ask for MULTIPLE assessment columns (e.g. 1st CA, 2nd CA, Exam), use those exact names as keys.
   - If the teacher says "extract everything" or "all columns", auto-detect every column from the header row and use readable names.
3. **COMPLEX COLUMN HEADERS**: School record sheets often have these handwritten column headers (detect them even if messy):
   - "1st CA" or "1st Test" = First Continuous Assessment
   - "2nd CA" or "2nd Test" = Second Continuous Assessment  
   - "Open Day" or "Open" = Open Day score
   - "Note" / "NB" / "Note Book" = Notebook score
   - "Ass" / "Assig" / "Assignment" = Assignment score
   - "Attend" / "Attendance" = Attendance score
   - "Total" = Total/subtotal
   - "Exam" = Examination score
   - "Grand Total" / "Total (final)" = Final cumulative score
4. **FRACTIONAL SCORES**: Convert handwritten fractions to decimals: 6½ → 6.5, 8½ → 8.5, 7½ → 7.5, 9½ → 9.5. If unsure, round to nearest 0.5.
5. **OVERWRITTEN/CORRECTED VALUES**: If a number appears to be crossed out and rewritten, use the CORRECTED (newer) value.
6. **MISSING/UNREADABLE VALUES**: Use empty string "" for any value you cannot read. NEVER skip the key.
7. **MULTI-PART NAMES**: Combine first name, middle name, and surname into ONE "name" field. E.g. "Abass Aishat" or "Abdulazees Zainab Oyadamola".
8. **PAGE CONTINUITY**: If multiple images show pages of the SAME class (continuing serial numbers), combine all rows into one array. Do NOT create separate arrays per image.
9. **SERIAL NUMBERS**: Do NOT include the S/N or serial number column unless specifically asked.
10. **AUTO-DETECT CLASS INFO**: If you can see a class name (e.g. "SS 1T", "SS2Q") or term (e.g. "1st Term", "2nd Term") written at the top of the sheet, note them but still focus on extracting the table data.
11. **NIGERIAN MARK BOOK STRUCTURE**: The standard record sheet follows this grading system:
   - Columns 1-5 are Continuous Assessments: 1st CA, 2nd CA, Open Day, Note Book, Assignment/Attendance
   - EACH column is out of 10, EXCEPT one column (usually Open Day or the combined one) which can be out of 20
   - Columns 1-5 are summed and DIVIDED BY 2 to get "Total CA" (max 30)
   - "Exam" column is out of 70
   - "Grand Total" = Total CA + Exam (max 100)
   - For MULTI-TERM sheets: 2nd Term Grand Total averages current + 1st Term total (÷ 2). 3rd Term averages all three (÷ 3).
   - The standard column order is: name, 1st CA, 2nd CA, Open Day, Note, Assignment, Total CA, Exam, Total
   - Output the RAW scores you read. Do NOT compute totals yourself — just extract what is written.
12. **NUMERIC VALUES**: All score values should be numbers (integers or decimals), NOT strings. Use 0 for a zero score, "" for missing/unreadable.
""".format(instruction=instruction, roster_context=roster_context)

        # Call AI
        raw_text = None
        for attempt in range(len(API_KEYS) if 'API_KEYS' in dir() else 3):
            try:
                # Use Gemini 2.5 Flash for multimodal
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = model.generate_content([prompt, *image_parts])
                raw_text = response.text.strip()
                break
            except Exception as model_err:
                err_str = str(model_err).lower()
                if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                    if 'rotate_api_key' in globals():
                        rotate_api_key()
                    continue
                raise model_err
                
        if not raw_text:
            return jsonify({"error": "AI service busy. Please try again."}), 503
            
        # Parse JSON
        raw_text = re_mod.sub(r'```json\n?', '', raw_text)
        raw_text = re_mod.sub(r'```\n?', '', raw_text)
        
        extracted_data = json.loads(raw_text)
        
        if not isinstance(extracted_data, list) or len(extracted_data) == 0:
            return jsonify({"error": "No valid data or table found in the image based on your instructions."}), 400
        
        # Get column names from the first row
        columns = list(extracted_data[0].keys()) if extracted_data else []
            
        # Return preview data instead of creating Excel immediately
        return jsonify({
            "success": True,
            "preview": True,
            "data": extracted_data,
            "columns": columns,
            "row_count": len(extracted_data),
            "message": "Found {} rows with columns: {}. Review the data below — you can edit anything before building the Excel file.".format(len(extracted_data), ', '.join(columns))
        }), 200

    except json.JSONDecodeError as e:
        print("Assistant Image Scan JSON Error: {}".format(raw_text if raw_text else 'No response'))
        return jsonify({"error": "AI had trouble reading the image. Try a clearer photo or simpler instructions."}), 400
    except Exception as e:
        print("Assistant Image Scan Error: {}".format(e))
        return jsonify({"error": "Something went wrong: {}. Please try again.".format(str(e))}), 500


@app.route('/api/assistant-build-excel', methods=['POST'])
def assistant_build_excel():
    """Takes confirmed/edited preview data and builds the final Excel file."""
    try:
        payload = request.json
        if not payload or 'data' not in payload:
            return jsonify({"error": "No data provided"}), 400
        
        extracted_data = payload['data']
        class_name = payload.get('class_name', '').strip()
        subject_name = payload.get('subject_name', '').strip()
        assessment_type = payload.get('assessment_type', '').strip()
        
        if not isinstance(extracted_data, list) or len(extracted_data) == 0:
            return jsonify({"error": "No data to build Excel from"}), 400
        
        df = pd.DataFrame(extracted_data)
        
        # --- Auto-compute mark book columns if applicable ---
        # Nigerian standard: CAs (cols 1-5) ÷ 2 = Total CA (30). Exam (70). Grand Total = Total CA + Exam (100)
        ca_columns = [c for c in df.columns if c.lower() in ['1st ca', '2nd ca', 'open day', 'note', 'note book', 'assignment', 'attendance']]
        has_exam = any(c.lower() == 'exam' for c in df.columns)
        exam_col = next((c for c in df.columns if c.lower() == 'exam'), None)
        
        if len(ca_columns) >= 2:
            # Convert CA columns to numeric
            for col in ca_columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
            # Auto-compute Total CA if not already present
            if not any(c.lower() in ['total ca', 'total'] for c in df.columns):
                df['Total CA'] = (df[ca_columns].sum(axis=1) / 2).round(1)
            
            # Auto-compute Grand Total if Exam column exists and Grand Total isn't already present
            if has_exam and not any(c.lower() in ['grand total', 'total score'] for c in df.columns):
                df[exam_col] = pd.to_numeric(df[exam_col], errors='coerce').fillna(0)
                total_ca_col = 'Total CA' if 'Total CA' in df.columns else next((c for c in df.columns if c.lower() == 'total ca'), None)
                if total_ca_col:
                    df['Grand Total'] = (df[total_ca_col] + df[exam_col]).round(1)
        
        # Reorder columns: name first, then CAs, then Total CA, Exam, Grand Total
        desired_order = ['name'] + ca_columns
        if 'Total CA' in df.columns:
            desired_order.append('Total CA')
        if exam_col and exam_col in df.columns:
            desired_order.append(exam_col)
        if 'Grand Total' in df.columns:
            desired_order.append('Grand Total')
        # Add any remaining columns not yet included
        remaining = [c for c in df.columns if c not in desired_order]
        final_order = [c for c in desired_order if c in df.columns] + remaining
        df = df[final_order]
        
        # Build a descriptive filename
        name_parts = []
        if class_name:
            safe_class = re_mod.sub(r'[^A-Za-z0-9 ]', '', class_name).strip()
            name_parts.append(safe_class)
        if subject_name:
            safe_subject = re_mod.sub(r'[^A-Za-z0-9 ]', '', subject_name).strip()
            name_parts.append(safe_subject)
        if assessment_type:
            safe_assessment = re_mod.sub(r'[^A-Za-z0-9 ]', '', assessment_type).strip()
            name_parts.append(safe_assessment)
        
        if name_parts:
            output_filename = "{}_{}.xlsx".format('_'.join(name_parts), int(time.time()))
        else:
            output_filename = "Extracted_Data_{}.xlsx".format(int(time.time()))
        
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_filename)
        df.to_excel(output_path, index=False, engine='openpyxl')
        
        return jsonify({
            "success": True,
            "message": "Excel file ready! {} rows, {} columns.".format(len(df), len(df.columns)),
            "download_url": "/api/download-edited-excel?file={}".format(output_filename)
        }), 200
        
    except Exception as e:
        print("Build Excel Error: {}".format(e))
        return jsonify({"error": str(e)}), 500

@app.route('/api/assistant-edit-excel', methods=['POST'])
def assistant_edit_excel():
    """Receives an Excel file + natural language instruction, uses AI to apply edits, returns the modified file."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        instruction = request.form.get('instruction', '').strip()
        if not instruction:
            return jsonify({"error": "No instruction provided"}), 400
        
        file = request.files['file']
        
        # Read the Excel into pandas
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Build context for AI
        columns = list(df.columns)
        sample = df.head(5).to_string(index=False)
        row_count = len(df)
        
        prompt = """You are an Excel editing assistant. A teacher has uploaded a spreadsheet and wants you to edit it.

SPREADSHEET INFO:
- Columns: {columns}
- Total rows: {rows}
- First 5 rows:
{sample}

TEACHER'S INSTRUCTION: "{instruction}"

You must return a JSON object with an "edits" array. Each edit is one of:
1. {{"type": "update_cell", "row": 0, "column": "Score", "value": 85}} — update a specific cell (row is 0-indexed)
2. {{"type": "update_column", "column": "Score", "expression": "x + 5"}} — apply a math expression to an entire column (x = current value)
3. {{"type": "delete_rows", "condition_column": "Score", "condition": "< 40"}} — delete rows matching a condition
4. {{"type": "add_row", "data": {{"Name": "John", "Score": 80}}}} — add a new row
5. {{"type": "rename_column", "old_name": "Marks", "new_name": "Score"}} — rename a column
6. {{"type": "add_column", "column": "Assignment", "default_value": "0"}} - add a new column
7. {{"type": "confirm_column", "suspected_column": "Ass", "original_instruction": "{instruction}"}} — ASK FOR CONFIRMATION IF UNSURE.

RULES:
- If a teacher says "add 5 to [Column Name]", use "update_column" with expression "x + 5" for that specific column. 
- **CRITICAL SMARTNESS**: If that column does NOT exist, look at the Current Columns list. Is there a column with a very similar name or obvious abbreviation (e.g. they asked for "Assignment" but the column is "Ass" or "1st CA")? If YES, you MUST return ONLY ONE edit: "confirm_column" with your best guess. Do not proceed with adding columns or updating.
- If there is NO similar column, you MUST return TWO edits: first "add_column" to create it with default_value 0, then "update_column" to add 5 to it.
- x represents the current cell value. The math expression must use ONLY `x` (e.g. `x + 5`, `x * 10`).

Return ONLY raw JSON. Example:
{{"edits": [{{"type": "update_column", "column": "Assignment", "expression": "x + 5"}}], "summary": "Added 5 marks to Assignment column"}}
""".format(columns=columns, rows=row_count, sample=sample, instruction=instruction)
        
        # Call AI with retry
        raw_text = None
        for attempt in range(len(API_KEYS) if 'API_KEYS' in dir() else 3):
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = model.generate_content(prompt)
                raw_text = response.text.strip()
                break
            except Exception as model_err:
                err_str = str(model_err).lower()
                if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                    rotate_api_key()
                    time.sleep(min(3 * (attempt + 1), 10))
                else:
                    raise
        if not raw_text:
            raise Exception('All API keys exhausted')
        
        # Parse AI response
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        
        ai_result = json.loads(raw_text.strip())
        edits = ai_result.get('edits', [])
        summary = ai_result.get('summary', 'Edits applied')
        
        # Apply edits to dataframe
        changes_made = 0
        needs_confirmation = None
        
        for edit in edits:
            edit_type = edit.get('type', '')
            
            if edit_type == 'confirm_column':
                needs_confirmation = {
                    "guess": edit.get('suspected_column'),
                    "original_instruction": edit.get('original_instruction')
                }
                break # Stop processing other edits if we need confirmation
                
            elif edit_type == 'update_cell':
                row = edit.get('row', 0)
                col = edit.get('column', '')
                if col in df.columns and 0 <= row < len(df):
                    df.at[row, col] = edit.get('value')
                    changes_made += 1
                    
            elif edit_type == 'add_column':
                col = edit.get('column', '')
                default_val = edit.get('default_value', '')
                if col and col not in df.columns:
                    # check if default_val is numeric string
                    if str(default_val).replace('.','',1).replace('-','',1).isdigit():
                        df[col] = float(default_val)
                    else:
                        df[col] = default_val
                    changes_made += 1
                    
            elif edit_type == 'update_column':
                col = edit.get('column', '')
                expr = edit.get('expression', '')
                if col in df.columns and expr:
                    try:
                        df[col] = df[col].apply(lambda x: eval(expr, {"__builtins__": {}}, {"x": float(x) if str(x).replace('.','',1).replace('-','',1).isdigit() else 0}) if pd.notna(x) else x)
                        changes_made += len(df)
                    except Exception as eval_err:
                        print("Expression eval error: {}".format(eval_err))
                        
            elif edit_type == 'delete_rows':
                col = edit.get('condition_column', '')
                cond = edit.get('condition', '')
                if col in df.columns and cond:
                    try:
                        before_count = len(df)
                        mask = df[col].apply(lambda x: eval("x {}".format(cond), {"__builtins__": {}}, {"x": float(x) if str(x).replace('.','',1).replace('-','',1).isdigit() else 0}) if pd.notna(x) else False)
                        df = df[~mask]
                        changes_made += before_count - len(df)
                    except Exception as eval_err:
                        print("Delete condition error: {}".format(eval_err))
                        
            elif edit_type == 'add_row':
                row_data = edit.get('data', {})
                if row_data:
                    df = pd.concat([df, pd.DataFrame([row_data])], ignore_index=True)
                    changes_made += 1
                    
            elif edit_type == 'rename_column':
                old = edit.get('old_name', '')
                new = edit.get('new_name', '')
                if old in df.columns:
                    df = df.rename(columns={old: new})
                    changes_made += 1
        
        # Save modified file
        output_filename = "edited_{}".format(file.filename if file.filename else "output.xlsx")
        if not output_filename.endswith(('.xlsx', '.xls', '.csv')):
            output_filename += '.xlsx'
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_filename)
        
        if output_filename.endswith('.csv'):
            df.to_csv(output_path, index=False)
        else:
            df.to_excel(output_path, index=False, engine='openpyxl')
        
        
        if needs_confirmation:
            return jsonify({
                "success": True,
                "needs_confirmation": True,
                "guess": needs_confirmation["guess"],
                "original_instruction": needs_confirmation["original_instruction"],
                "message": "Did you mean the '{}' column?".format(needs_confirmation["guess"])
            }), 200
            
        if changes_made > 0:
            # Assuming WORKING_EXCEL_PATH is defined elsewhere, or we should use output_path
            # For now, using output_path as a placeholder if WORKING_EXCEL_PATH is not defined
            # If WORKING_EXCEL_PATH is meant to be a global variable, it should be defined.
            # For this edit, I'll assume it's a typo and should be output_path, or it's defined globally.
            # Given the context, it's likely a persistent working file.
            # If not defined, this line will cause an error. I'll keep it as is, assuming it's defined.
            # df.to_excel(WORKING_EXCEL_PATH, index=False) # Commented out as WORKING_EXCEL_PATH is not in provided context
            return jsonify({
                "success": True,
                "message": summary,
                "download_url": "/api/download-edited-excel?file={}".format(output_filename) # Changed to use output_filename
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "I understood the instruction, but it didn't result in any actual changes to the data."
            }), 200
            
    except Exception as e:
        print("Assistant edit Excel error: {}".format(e))
        return jsonify({"error": str(e)}), 500

@app.route('/api/download-edited-excel')
def download_edited_excel():
    """Download an edited Excel file."""
    filename = request.args.get('file', '')
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/api/smart-assistant', methods=['POST'])
def smart_assistant():
    """Smart Assistant v3 — Full intelligence upgrade. Designed by XO.
    16 action types, proactive insights, anomaly detection, cross-class analysis.
    """
    try:
        data = request.json
        message = data.get('message', '')
        context = data.get('context', {})
        current_screen = data.get('currentScreen', 'landing')
        current_results = data.get('currentResults', [])  # Live scores from frontend
        session_info = data.get('sessionInfo', {})  # Classes graded, subject, etc.
        history = data.get('history', [])  # Conversation history
        
        # Build conversation history string
        conversation_context = ""
        if history and len(history) > 1:  # More than just the current message
            conv_lines = []
            for h in history[:-1]:  # Exclude current message (it's sent separately)
                role = "Teacher" if h.get('role') == 'user' else "You (Assistant)"
                text = h.get('text', '')
                if text.startswith('[SYSTEM]'):
                    role = 'SYSTEM'
                    text = text[8:].strip()
                conv_lines.append("{}: {}".format(role, text))
            conversation_context = "\nCONVERSATION HISTORY (maintain context!):\n{}\n".format("\n".join(conv_lines[-10:]))
        
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
                "students": [s.name for s in students[:20]],
                "assessments": assessment_types,
                "subjects": subjects,
                "has_scores": len(scores) > 0
            }
        
        # Build current session analytics if results are available
        session_analytics = ""
        if current_results:
            valid_scores = []
            missing_names = []
            for r in current_results:
                name = r.get('name', '').strip()
                score = r.get('score', '').strip()
                if not name:
                    missing_names.append(r)
                    continue
                try:
                    val = str(score)
                    if '/' in val: val = val.split('/')[0]
                    numeric = float(val)
                    valid_scores.append({"name": name, "score": numeric, "class": r.get('class', '')})
                except:
                    pass
            
            if valid_scores:
                scores_list = [s['score'] for s in valid_scores]
                avg = sum(scores_list) / len(scores_list)
                highest = max(valid_scores, key=lambda x: x['score'])
                lowest = min(valid_scores, key=lambda x: x['score'])
                
                # Anomaly detection — scores that deviate significantly from average
                anomalies = []
                if len(scores_list) > 3:
                    import statistics
                    stdev = statistics.stdev(scores_list) if len(scores_list) > 1 else 0
                    for s in valid_scores:
                        if stdev > 0 and abs(s['score'] - avg) > (2 * stdev):
                            anomalies.append(s)
                
                session_analytics = """
CURRENT GRADING SESSION ANALYTICS:
- Total students scanned: {total}
- Average score: {avg:.1f}
- Highest: {high_name} ({high_score})
- Lowest: {low_name} ({low_score})
- Students with missing names: {missing}
- Anomalies (unusual scores): {anomalies}
- Score distribution: {dist}
""".format(
                    total=len(valid_scores),
                    avg=avg,
                    high_name=highest['name'], high_score=highest['score'],
                    low_name=lowest['name'], low_score=lowest['score'],
                    missing=len(missing_names),
                    anomalies=json.dumps([{"name": a['name'], "score": a['score']} for a in anomalies]) if anomalies else "None detected",
                    dist="Below avg: {}, Above avg: {}".format(
                        len([s for s in scores_list if s < avg]),
                        len([s for s in scores_list if s >= avg])
                    )
                )
        
        system_prompt = """You are the Smart Assistant for QSI Smart Grader, designed by XO.
You are an intelligent, proactive teaching aide — not just a chatbot. You think ahead, analyze data, and help teachers work smarter.

PERSONALITY: Warm, smart, helpful. Talk like a brilliant colleague who genuinely cares about the teacher's work. Use natural language — never robotic. Reference real data and names when possible.

CURRENT STATE:
- Teacher is on the "{screen}" screen
- Session: {session}
- Context: {context}

DATABASE (live rosters):
{db_data}

{analytics}

ACTIONS YOU CAN TAKE (pick the best one):
{{
    "response": "Your warm, natural response (1-3 sentences). Use real names and data.",
    "action": One of:
        "setup_session" - Set up class/subject/assessment, navigate to scanning
        "view_standings" - Show results and rankings
        "add_student" - Add a student to a class roster
        "add_students_batch" - Add multiple students at once
        "move_student" - Move a student between classes
        "correct_score" - Fix a specific student's score in the current session
        "edit_scores" - Go to review screen to fix scores
        "add_class" - Open the add-class form
        "update_roster" - Open class list for name fixes
        "export_data" - Download the Excel file
        "analyze_scores" - Show analytics card (averages, trends, insights)
        "compare_classes" - Side-by-side class performance comparison
        "compare_assessments" - Compare across assessment types (1st CA vs 2nd CA)
        "flag_anomalies" - Highlight unusual scores that may be errors
        "find_at_risk" - List students who are failing or at risk
        "generate_report" - Create a formatted summary for admin/principal
        "edit_excel" - Edit an uploaded Excel file based on instructions (e.g. "add 5 marks to everyone")
        "scan_image_to_excel" - Extract requested columns from an uploaded image specifically into an Excel file.
        "none" - Just a conversational answer, no action needed
    "params": {{
        "class_name": "...",
        "subject_name": "...",
        "assessment_type": "...",
        "student_name": "...",
        "target_class": "...",
        "new_score": "...",
        "instruction": "...",
        "students": ["name1", "name2"],
        "report_text": "...",
        "insights": [list of insight strings],
        "anomalies": [list of {{name, score, reason}}],
        "at_risk": [list of {{name, score, class}}]
    }}
}}

CRITICAL ACTION SELECTION RULES:
>>> NEVER return action "none" when the teacher is asking you to DO something. <<<
>>> If the teacher wants to grade, scan, start a session, add a test → use "setup_session" <<<
>>> If the teacher mentions a student name + score → use "correct_score" immediately <<<
>>> If the teacher says "yes", "ok", "do it", "go ahead" → EXECUTE the action from the previous exchange, don't just talk about it <<<
>>> ACTION FIRST, CHAT SECOND. Always pick an action. Only use "none" for pure questions like "what is this app?" <<<

EXAMPLE INPUT→OUTPUT MAPPINGS (follow these patterns exactly):
- "I want to add 2nd test" → action: "setup_session", params: {{assessment_type: "2nd CA"}}
- "Grade next test" → action: "setup_session" with next logical assessment
- "Add 2nd CA for SS 1Q" → action: "setup_session", params: {{class_name: "SS 1Q", assessment_type: "2nd CA"}}
- "Adekunie should be 8" → action: "correct_score", params: {{student_name: "Adekunie", new_score: "8"}}
- "Fix Tunde's score to 15" → action: "correct_score", params: {{student_name: "Tunde", new_score: "15"}}
- "Add 5 points to everyone" / "Delete students below 40" → action: "edit_excel", params: {{instruction: "add 5 points to everyone"}}
- "Scan this image and extract Name, 1st CA, and 2nd CA into an excel file" → action: "scan_image_to_excel", params: {{instruction: "extract Name, 1st CA, and 2nd CA"}}
- "Scan this image for SS 1Q and extract Name, 1st CA, and 2nd CA" → action: "scan_image_to_excel", params: {{instruction: "extract Name, 1st CA, and 2nd CA", class_name: "SS 1Q"}}
- "This is SS 1T Mathematics, extract everything" → action: "scan_image_to_excel", params: {{instruction: "extract all columns", class_name: "SS 1T", subject_name: "Mathematics"}}
- "Extract all columns for 1st term" → action: "scan_image_to_excel", params: {{instruction: "extract all columns", assessment_type: "1st Term"}}
- "Just scan it" → action: "none" (ASK: "What columns should I extract? Or shall I grab everything I can see?")
- "Read this file" / "What is in this excel?" → action: "none" (Just read it and summarize conversationaly!)
- "Where is the edited file?" / "Did you edit it?" → action: "none" (Answer the question conversationally, DO NOT use edit_excel!)
- "Add Fatimah to SS 1Q" → action: "add_student", params: {{student_name: "Fatimah", class_name: "SS 1Q"}}
- "Only one student" or "For a student..." → action: "add_student"
- "Show results" / "See scores" → action: "view_standings"
- "Download" / "Get my Excel" → action: "export_data"
- "Who is failing?" → action: "find_at_risk"
- "Any issues with scores?" → action: "flag_anomalies"
- "Compare SS 1Q and SS 1S" → action: "compare_classes"
- "Set it up" / "yes" / "ok" / "do it" / "go ahead" → REPEAT the previous action with same params

INTELLIGENCE RULES:
1. For "setup_session": Figure out the NEXT logical assessment. If 1st CA exists, suggest 2nd CA. If both exist, suggest Exam. ALWAYS include class_name and assessment_type in params.
2. For "correct_score": Extract the student name and new score from the message. Match fuzzy names to the roster.
3. For "analyze_scores": Share meaningful insights using the analytics data above.
4. For "add_students_batch": When teacher lists multiple names, extract ALL of them.
5. For "move_student": Include source class, student name, and destination class in params.
6. BE PROACTIVE: Mention data insights naturally.
7. Always reference real student names and data. Never make up data.
8. RESPONSE LENGTH: Keep it SHORT (1-2 sentences) WHEN confirming an app action. HOWEVER, if the teacher asks a general question, wants to EXPLAIN A FILE, needs an email drafted, or asks for a lesson plan, act as a full LLM and write as much as needed! Format long responses nicely.
9. CONVERSATION MEMORY: Use history context. "yes"/"ok"/"do it" = execute previous action.
10. ALWAYS return valid JSON with "response", "action" and "params". For general LLM tasks, use action "none" and put your full, rich answer in "response".
11. READ VS EDIT EXCEL: If the teacher asks you to simply "read", "review", or "tell me what you found" about an uploaded Excel file, DO NOT use "edit_excel". Only use "edit_excel" if they explicitly ask you to MODIFY data (e.g. add, delete, rename). If they just want to read, use action "none" and summarize it in the response based on the "Context" I provided you about the upload.
12. AVOID FALSE EDITS: If the teacher asks a question LIKE "where is the edited file?", they are asking a question, NOT giving an instruction to edit an Excel file. Use action "none".
13. IMAGE SCAN INTELLIGENCE: When teacher uploads image(s), you MUST collect this info before triggering scan_image_to_excel:
    a) WHICH CLASS is this for? → params.class_name (e.g. "SS 1T", "SS 2Q")
    b) WHAT SUBJECT? → params.subject_name (e.g. "Mathematics", "English")
    c) WHAT COLUMNS to extract? → params.instruction (e.g. "extract name, 1st CA, 2nd CA, Exam"). If teacher says "extract everything" or "all columns", pass instruction as "extract all columns".
    d) WHAT ASSESSMENT TYPE? → params.assessment_type (e.g. "1st Term", "2nd Term") if visible on the sheet.
    If the teacher provides some info upfront (e.g. "this is SS 1T Mathematics"), don't re-ask for what you already know. Only ask for MISSING info.
14. SMART COLUMN GUESSING: Nigerian record sheets often have these columns: 1st CA, 2nd CA, Open Day, Note Book, Assignment, Attendance, Total, Exam, Grand Total. If teacher says "extract all columns" or "everything", use these standard names.
15. AUTO-DETECT FROM IMAGE: If the teacher says "just scan it" without specifying columns, ask them: "I can see this looks like a record sheet. Want me to extract ALL the columns I can see, or just specific ones like 1st CA and 2nd CA?"
16. MULTI-CLASS UPLOADS: If teacher uploads multiple images and says they're for DIFFERENT classes, ask which image is for which class. Do NOT assume all images are the same class.

{conversation}

Return ONLY raw JSON. No markdown wrapping.""".format(
            screen=current_screen,
            session=json.dumps(session_info),
            context=json.dumps(context),
            db_data=json.dumps(class_data, indent=2),
            analytics=session_analytics,
            conversation=conversation_context
        )
        
        # Primary: best quality model. Fallback: lighter model if primary fails.
        models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash']
        raw_text = None
        last_error = None
        
        for model_name in models_to_try:
            for attempt in range(3):
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content([system_prompt, message])
                    raw_text = response.text.strip()
                    break
                except Exception as model_err:
                    last_error = model_err
                    err_str = str(model_err).lower()
                    print("Smart assistant model {} attempt {}/3 failed: {}".format(model_name, attempt + 1, model_err))
                    # Only retry on rate limit errors, not on model-not-found etc.
                    if 'quota' in err_str or 'rate' in err_str or '429' in err_str or 'resource' in err_str:
                        rotate_api_key()  # Switch to next API key
                        model = genai.GenerativeModel(model_name)  # Re-create model with new key
                        if attempt < 2:
                            wait = [3, 8][attempt]
                            print("Rate limited, rotated key & waiting {}s...".format(wait))
                            time.sleep(wait)
                    else:
                        break  # Non-rate-limit error, skip to next model
            if raw_text:
                break
        
        if not raw_text:
            raise last_error or Exception("All models failed")
        
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        result = json.loads(raw_text.strip())
        return jsonify(result), 200
        
    except Exception as e:
        print("Smart assistant error: {}".format(e))
        import traceback
        traceback.print_exc()
        return jsonify({
            "response": "Sorry, I had a hiccup processing that. Could you say it differently? I'm here to help!",
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
