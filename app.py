import os
import uuid
import zipfile
import threading
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

# Load Environment Variables from .env file
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.utils import secure_filename

# Import Services
from services.translatorr import translate_text, get_engine_display_name, check_api_connections
from services.filehandler import translate_file
from services.audit_service import log_action, get_audit_logs, export_audit_csv
from services.glossary import get_glossary_rules

app = Flask(__name__)

# Placeholder stubs for removed services

def check_and_notify_manager(*args, **kwargs):
    """No-op stub for manager notifications."""
    pass

def send_email(to, subject, html_body):
    """No-op email sender."""
    pass

def get_simulated_mailbox():
    """Return empty simulated mailbox."""
    return []

def clear_simulated_mailbox():
    """Clear simulated mailbox (no-op)."""
    pass

app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET', 'dev-secret-key-1337-enterprise')

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
BATCH_FOLDER = os.path.join(UPLOAD_FOLDER, 'batch')
os.makedirs(BATCH_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'xls', 'xlsx', 'csv'}
MAX_FILE_SIZE = 200 * 1024 * 1024

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

import decimal

class MySQLRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class CompatibleDictCursor(pymysql.cursors.DictCursor):
    dict_type = MySQLRow

    def fetchone(self):
        row = super().fetchone()
        if row is None:
            return None
        return self._clean_row(row)

    def fetchall(self):
        rows = super().fetchall()
        return [self._clean_row(r) for r in rows]

    def fetchmany(self, size=None):
        rows = super().fetchmany(size)
        return [self._clean_row(r) for r in rows]

    def _clean_row(self, row):
        for k, v in list(row.items()):
            if isinstance(v, decimal.Decimal):
                if v % 1 == 0:
                    row[k] = int(v)
                else:
                    row[k] = float(v)
        return row

def get_db_connection(use_db=True):
    host = os.environ.get('MYSQL_HOST', '127.0.0.1')
    user = os.environ.get('MYSQL_USER', 'root')
    password = os.environ.get('MYSQL_PASSWORD', 'root')
    db = os.environ.get('MYSQL_DB', 'doctranslate')
    
    if use_db:
        return pymysql.connect(
            host=host,
            user=user,
            password=password,
            database=db,
            cursorclass=CompatibleDictCursor
        )
    else:
        return pymysql.connect(
            host=host,
            user=user,
            password=password,
            cursorclass=CompatibleDictCursor
        )

def init_db():
    # Connect to MySQL server without selecting a database first
    conn = get_db_connection(use_db=False)
    cursor = conn.cursor()
    db_name = os.environ.get('MYSQL_DB', 'doctranslate')
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
    conn.commit()
    conn.close()
    
    # Reconnect with database selected
    conn = get_db_connection(use_db=True)
    cursor = conn.cursor()
    
    # 1. Base Users Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255) UNIQUE,
        email VARCHAR(255),
        role VARCHAR(50),
        department VARCHAR(100) DEFAULT 'Engineering'
    )
    """)
    
    # 2. Base Translations Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS translations(
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255),
        filename VARCHAR(255),
        file_size INT DEFAULT 0,
        direction VARCHAR(50) DEFAULT 'eng-spa',
        total_words INT DEFAULT 0,
        masked_words INT DEFAULT 0,
        glossary_words INT DEFAULT 0,
        translated_text LONGTEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        engine VARCHAR(50) DEFAULT 'google',
        department VARCHAR(100) DEFAULT 'Engineering',
        confidence_score DOUBLE DEFAULT 95.0,
        cost DOUBLE DEFAULT 0.0
    )
    """)
    
    # 3. Custom Glossary Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS glossary(
        id INT AUTO_INCREMENT PRIMARY KEY,
        source_term VARCHAR(255),
        target_term VARCHAR(255),
        direction VARCHAR(50) DEFAULT 'all'
    )
    """)
    
    # 4. Comprehensive Audit Logs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255),
        action VARCHAR(255),
        details TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 5. Bulk Batch Jobs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS batch_jobs(
        id VARCHAR(255) PRIMARY KEY,
        username VARCHAR(255),
        total_files INT DEFAULT 0,
        processed_files INT DEFAULT 0,
        status VARCHAR(50) DEFAULT 'Pending',
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        zip_filename VARCHAR(255)
    )
    """)
    
    conn.commit()
    
    # Check table columns / Migration support for legacy database schema
    cursor.execute("SHOW COLUMNS FROM translations")
    columns_translations = [row['Field'] for row in cursor.fetchall()]
    for col, c_type in [
        ('file_size', 'INT DEFAULT 0'),
        ('direction', "VARCHAR(50) DEFAULT 'eng-spa'"),
        ('glossary_words', 'INT DEFAULT 0'),
        ('engine', "VARCHAR(50) DEFAULT 'google'"),
        ('department', "VARCHAR(100) DEFAULT 'Engineering'"),
        ('confidence_score', 'DOUBLE DEFAULT 95.0'),
        ('cost', 'DOUBLE DEFAULT 0.0')
    ]:
        if col not in columns_translations:
            cursor.execute(f"ALTER TABLE translations ADD COLUMN {col} {c_type}")
            
    cursor.execute("SHOW COLUMNS FROM users")
    columns_users = [row['Field'] for row in cursor.fetchall()]
    if 'department' not in columns_users:
        cursor.execute("ALTER TABLE users ADD COLUMN department VARCHAR(100) DEFAULT 'Engineering'")
        
    # Clear translation records on startup to start from 0
    cursor.execute("DELETE FROM translations")
    cursor.execute("DELETE FROM batch_jobs")
    conn.commit()

    # 7. Seed Initial Enterprise Users
    initial_users = [
        ('AdminUser', 'admin@company.com', 'admin', 'Operations'),
        ('ManagerUser', 'manager@company.com', 'manager', 'Engineering'),
        ('StaffUser', 'staff@company.com', 'staff', 'Engineering'),
        ('JohnDoe', 'john.doe@company.com', 'staff', 'Legal'),
        ('SalesAgent', 'sales@company.com', 'staff', 'Sales'),
        ('GuestUser', 'guest@company.com', 'guest', 'Engineering'),
    ]
    for username, email, role, dept in initial_users:
        cursor.execute(
            "INSERT IGNORE INTO users (username, email, role, department) VALUES (%s, %s, %s, %s)",
            (username, email, role, dept)
        )
        
    # 8. Seed Sample Glossary Terms
    sample_glossary = [
        ('WebLogic', '', 'all'),  # Do Not Translate
        ('Director General', 'Director General', 'eng-spa'),  # Fixed Translation
        ('DocTranslate', '', 'all'),  # Do Not Translate
        ('CEO', 'Director Ejecutivo', 'eng-spa'),
    ]
    cursor.execute("SELECT COUNT(*) FROM glossary")
    if cursor.fetchone()[0] == 0:
        for src, tgt, direction in sample_glossary:
            cursor.execute(
                "INSERT INTO glossary (source_term, target_term, direction) VALUES (%s, %s, %s)",
                (src, tgt, direction)
            )

    conn.commit()
    conn.close()

# Start Database setup
init_db()

# --- Role Based Access Control Helper ---
def get_current_user():
    return {
        'username': session.get('username'),
        'role': session.get('role', 'staff'),
        'department': session.get('department', 'Engineering')
    }

# --- Batch Translation Background Thread ---
def process_batch_job(job_id, files_list, direction, engine, department, username, role, glossary_rules, custom_words=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    job_dir = os.path.join(BATCH_FOLDER, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    translated_paths = []
    
    total_words = 0
    total_masked = 0
    total_glossary = 0
    total_cost = 0.0
    
    for idx, (original_filename, temp_filepath) in enumerate(files_list):
        safe_name = secure_filename(original_filename)
        output_filepath = os.path.join(job_dir, f"translated_{safe_name}")
        
        try:
            # Dispatch to appropriate parser
            p_words, p_masked, p_glossary, p_confidence, p_cost = translate_file(
                temp_filepath, output_filepath, direction, engine, department, glossary_rules, custom_words
            )
            
            translated_paths.append((original_filename, output_filepath))
            total_words += p_words
            total_masked += p_masked
            total_glossary += p_glossary
            total_cost += p_cost
            
        except Exception as e:
            print(f"Error in batch translating {original_filename}: {e}")
            
        # Increment progress in DB
        cursor.execute(
            "UPDATE batch_jobs SET processed_files = processed_files + 1 WHERE id = %s",
            (job_id,)
        )
        conn.commit()
        
    # Zip all files together
    zip_filename = f"{job_id}.zip"
    zip_filepath = os.path.join(BATCH_FOLDER, zip_filename)
    
    with zipfile.ZipFile(zip_filepath, 'w') as zipf:
        for orig_name, translated_path in translated_paths:
            zipf.write(translated_path, f"translated_{orig_name}")
            
    # Mark batch job complete
    cursor.execute(
        "UPDATE batch_jobs SET status = 'Completed', zip_filename = %s WHERE id = %s",
        (zip_filename, job_id)
    )
    
    # Audit log
    if role != 'guest':
        details = f"Bulk batch translation of {len(files_list)} files completed. Total words: {total_words:,}."
        log_action(conn, username, "bulk_translate", details)
    
    conn.commit()
    
    # Get user email
    cursor.execute("SELECT email FROM users WHERE username = %s", (username,))
    user_row = cursor.fetchone()
    user_email = user_row['email'] if user_row else f"{username}@example.com"
    conn.close()
    
    # Dispatch Email notification to employee
    download_url = f"http://127.0.0.1:5000/download-batch/{job_id}"
    subject = f"✅ Batch translation job complete! ({len(files_list)} files)"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; border: 1.5px solid #dc2626; border-radius: 12px; padding: 24px; color: #1e293b;">
        <h2 style="color: #dc2626; margin-top: 0;">🎉 Bulk Translation Job Completed!</h2>
        <p>Hello <strong>{username}</strong>,</p>
        <p>Good news! Your bulk translation job containing <strong>{len(files_list)} files</strong> has successfully processed.</p>
        
        <div style="background: #fef2f2; border-radius: 8px; padding: 16px; margin: 20px 0; text-align: center;">
            <p style="margin: 0 0 12px 0; font-size: 14px; font-weight: bold; color: #b91c1c;">All translations are zipped and ready for review.</p>
            <a href="{download_url}" style="background: #dc2626; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block; box-shadow: 0 4px 6px rgba(220,38,38,0.2);">
                Download Zipped Output
            </a>
        </div>
        
        <h4 style="margin: 18px 0 8px 0; color: #1e293b; border-bottom: 1.5px solid #e2e8f0; padding-bottom: 6px;">Job Cost & Summary</h4>
        <ul style="font-size: 13.5px; line-height: 1.6; color: #475569; padding-left: 20px;">
            <li><strong>Engine Used:</strong> {get_engine_display_name(engine)}</li>
            <li><strong>Language Direction:</strong> {direction.upper()}</li>
            <li><strong>Total Words:</strong> {total_words:,}</li>
            <li><strong>PII Masked:</strong> {total_masked} sensitive fields</li>
            <li><strong>Glossary Matches:</strong> {total_glossary} rules applied</li>
            <li><strong>Simulated Job Cost:</strong> ${total_cost:0.4f}</li>
        </ul>
        <p style="font-size: 11px; color: #94a3b8; margin-top: 24px;">This notification was dispatched automatically. Do not reply to this email.</p>
    </div>
    """
    pass

# --- ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Default fallback is StaffUser
        username = request.form.get('username', '').strip() or 'StaffUser'
        selected_role = request.form.get('role', 'staff')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user exists by username or email
        cursor.execute("SELECT username, email, role, department FROM users WHERE username = %s OR email = %s", (username, username))
        user_row = cursor.fetchone()
        
        if user_row:
            db_username = user_row['username']
            # Update role dynamically based on login demo panel
            cursor.execute("UPDATE users SET role = %s WHERE username = %s", (selected_role, db_username))
            role = selected_role
            email = user_row['email']
            department = user_row['department']
            username = db_username
        else:
            # Register new demo user
            db_username = username.split('@')[0] if '@' in username else username
            email = username if '@' in username else f"{username}@company.com"
            department = 'Engineering'
            if selected_role == 'admin':
                department = 'Operations'
            elif selected_role == 'manager':
                department = 'Engineering'
            cursor.execute(
                "INSERT INTO users (username, email, role, department) VALUES (%s, %s, %s, %s)",
                (db_username, email, selected_role, department)
            )
            role = selected_role
            username = db_username
            
        conn.commit()
        
        # Set session details
        session['username'] = username
        session['role'] = role
        session['department'] = department
        
        log_action(conn, username, "login", f"User logged in as {role} inside the {department} department.")
        conn.close()
        
        return redirect(url_for('dashboard'))
        
    if session.get('username'):
        return redirect(url_for('dashboard'))
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        conn = get_db_connection()
        log_action(conn, username, "logout", "User logged out of active session.")
        conn.close()
    session.clear()
    return redirect(url_for('login'))

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    """Render and process the upload page."""
    error = None
    translated = None
    filename_used = None
    direction = request.form.get('direction', 'en-es')
    badge_text = "English → Spanish" if direction == 'en-es' else "Spanish → English"
    total_words = 0
    selected_engine = 'google'
    department = session.get('department', 'Engineering')
    confidence_score = None

    if request.method == 'POST':
        file = request.files.get('document')
        if not file or file.filename == '':
            error = 'Please choose a file to translate.'
        elif not allowed_file(file.filename):
            error = 'Unsupported file format. Upload TXT, CSV, DOCX, XLSX, PDF or DOC files.'
        else:
            filename_used = secure_filename(file.filename)
            temp_in = os.path.join(UPLOAD_FOLDER, filename_used)
            temp_out = os.path.join(UPLOAD_FOLDER, f"translated_{filename_used}")
            file.save(temp_in)
            file_size_bytes = os.path.getsize(temp_in)
            glossary_rules = []

            try:
                p_words, p_masked, p_glossary, p_confidence, p_cost = translate_file(
                    temp_in, temp_out, direction, selected_engine, department, glossary_rules
                )

                ext = os.path.splitext(filename_used)[1].lower()
                if ext in ('.txt', '.csv'):
                    with open(temp_out, 'r', encoding='utf-8', errors='ignore') as preview_f:
                        translated = preview_f.read(10000)
                else:
                    translated = f"Translation completed for {filename_used}. Download your translated file below."

                total_words = p_words
                confidence_score = p_confidence
            except Exception as e:
                error = f"Translation failed: {e}"

    return render_template(
        'upload.html',
        error=error,
        translated=translated,
        filename=filename_used,
        direction=direction,
        badge_text=badge_text,
        total_words=total_words,
        confidence_score=confidence_score
    )

@app.route('/', methods=['GET', 'POST'])
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not session.get('username'):
        return redirect(url_for('login'))
        
    username = session.get('username')
    role = session.get('role', 'staff')
    department = session.get('department', 'Engineering')
    is_admin = (role == 'admin')
    is_manager = (role == 'manager' or is_admin)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Default engine
    user_engine = 'google'
    
    error = None
    translated = ""
    total_words = 0
    masked_words = 0
    glossary_words = 0
    confidence_score = 95.0
    paragraphs = []
    cost = 0.0
    filename_used = "Text Input"
    
    # 1. Single Translation Form Handler (POST)
    if request.method == 'POST' and 'direction' in request.form:
        direction = request.form.get('direction')
        selected_engine = request.form.get('engine', user_engine)
        glossary_rules = get_glossary_rules(conn, direction)
        
        # Parse optional custom words to mask
        custom_words_raw = request.form.get('custom_mask_words', '').strip()
        custom_words = [w.strip() for w in custom_words_raw.split(',') if w.strip()] if custom_words_raw else []
        
        # Determine if file or raw text
        file = request.files.get('document')
        
        if file and file.filename != '':
            if not allowed_file(file.filename):
                error = 'Unsupported file format! Upload docx, xlsx, txt or csv.'
            else:
                filename_used = file.filename
                safe_name = secure_filename(file.filename)
                temp_in = os.path.join(UPLOAD_FOLDER, safe_name)
                temp_out = os.path.join(UPLOAD_FOLDER, f"translated_{safe_name}")
                
                file.save(temp_in)
                file_size_bytes = os.path.getsize(temp_in)
                
                try:
                    # Parse document and translate
                    p_words, p_masked, p_glossary, p_confidence, p_cost = translate_file(
                        temp_in, temp_out, direction, selected_engine, department, glossary_rules, custom_words
                    )
                    
                    # Read the final translated text preview (up to 10kb) for display
                    ext = os.path.splitext(safe_name)[1].lower()
                    if ext in ('.txt', '.csv'):
                        with open(temp_out, 'r', encoding='utf-8', errors='ignore') as preview_f:
                            translated = preview_f.read(10000)
                    else:
                        translated = f"Format-Preserved {ext.upper()} Translation complete. File is ready for download."
                        
                    total_words = p_words
                    masked_words = p_masked
                    glossary_words = p_glossary
                    confidence_score = p_confidence
                    cost = p_cost
                    
                    # Log translation to table if not guest
                    if role != 'guest':
                        cursor.execute(
                            """INSERT INTO translations 
                            (username, filename, file_size, direction, total_words, masked_words, glossary_words, translated_text, engine, department, confidence_score, cost) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (username, filename_used, file_size_bytes, direction, total_words, masked_words, glossary_words, translated, selected_engine, department, confidence_score, cost)
                        )
                        
                        # Write in audit log
                        log_action(conn, username, "file_translation", f"Translated '{filename_used}' ({p_words:,} words) using {selected_engine} engine.")
                        conn.commit()
                    
                    # check_and_notify_manager omitted as notifications disabled
                    
                except Exception as e:
                    error = f"Translation engine failure: {e}"
                    print(f"Translation Crash: {e}")
        else:
            # Raw text translation
            raw_text = request.form.get('raw_text', '').strip()
            if raw_text:
                translated, total_words, masked_words, glossary_words, confidence_score, paragraphs, cost = translate_text(
                    raw_text, direction, selected_engine, department, glossary_rules, custom_words
                )
                
                # Log translation if not guest
                if role != 'guest':
                    cursor.execute(
                        """INSERT INTO translations 
                        (username, filename, file_size, direction, total_words, masked_words, glossary_words, translated_text, engine, department, confidence_score, cost) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (username, "Text Box Input", len(raw_text), direction, total_words, masked_words, glossary_words, translated, selected_engine, department, confidence_score, cost)
                    )
                    conn.commit()
                
                # Check manager sensitivity notifications
                # Pass placeholders by parsing raw paragraph details
                pii_payload = {}
                for p in paragraphs:
                    if p.get('masked_count', 0) > 0:
                        # Extract salary/ID flags
                        for flg in p.get('flags', []):
                            if "salary" in flg.lower():
                                pii_payload['[SALARY_1]'] = "$120,000"
                            if "ID" in flg.lower():
                                pii_payload['[ID_1]'] = "111-222-3333"
                                pii_payload['[ID_2]'] = "444-555-6666"
                                pii_payload['[ID_3]'] = "777-888-9999"
                                
                # check_and_notify_manager omitted as notifications disabled

    # 2. Retrieve DB records depending on RBAC permissions
    # Staff: only their history
    # Manager: full department history
    # Admin: absolute global history
    if role == 'admin':
        cursor.execute("SELECT * FROM translations ORDER BY id DESC LIMIT 100")
    elif role == 'manager':
        cursor.execute("SELECT * FROM translations WHERE department = %s ORDER BY id DESC LIMIT 100", (department,))
    else:
        cursor.execute("SELECT * FROM translations WHERE username = %s ORDER BY id DESC LIMIT 50", (username,))
    history = cursor.fetchall()

    # 3. Retrieve glossary rules
    cursor.execute("SELECT id, source_term, target_term, direction FROM glossary ORDER BY id DESC")
    glossary = cursor.fetchall()

    # 4. Gather Dashboard Stats (Usage Analytics)
    # A. Translations volume by day
    cursor.execute("""
        SELECT DATE_FORMAT(timestamp, '%%m-%%d') as day, COUNT(*) as count, SUM(total_words) as words 
        FROM translations 
        GROUP BY day 
        ORDER BY MIN(timestamp) DESC LIMIT 7
    """)
    daily_stats = cursor.fetchall()
    
    # B. Engine breakdown
    cursor.execute("SELECT engine, COUNT(*) as count, SUM(cost) as total_cost FROM translations GROUP BY engine")
    engine_stats = cursor.fetchall()
    
    # C. Department breakdown
    cursor.execute("SELECT department, COUNT(*) as count, SUM(total_words) as words FROM translations GROUP BY department")
    department_stats = cursor.fetchall()
    
    # D. Quick summary counters
    cursor.execute("SELECT COUNT(*) FROM translations WHERE filename != 'Text Box Input'")
    stat_total_translations = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(total_words) FROM translations")
    stat_total_words = cursor.fetchone()[0] or 0

    # E. Active Department, Common Direction, Avg File Size
    cursor.execute("SELECT department, COUNT(*) as count FROM translations GROUP BY department ORDER BY count DESC LIMIT 1")
    active_dept_row = cursor.fetchone()
    stat_active_dept = active_dept_row['department'] if active_dept_row else "None"

    cursor.execute("SELECT direction, COUNT(*) as count FROM translations GROUP BY direction ORDER BY count DESC LIMIT 1")
    common_dir_row = cursor.fetchone()
    stat_common_dir = common_dir_row['direction'].upper() if common_dir_row else "None"

    cursor.execute("SELECT AVG(file_size) FROM translations WHERE file_size > 0")
    avg_file_row = cursor.fetchone()
    stat_avg_file_size = round(float(avg_file_row[0] or 0.0) / 1024.0, 1) if avg_file_row else 0.0

    cursor.execute("SELECT AVG(confidence_score) FROM translations")
    avg_conf_row = cursor.fetchone()
    stat_avg_confidence = round(float(avg_conf_row[0] or 0.0), 1) if avg_conf_row else 0.0

    # Retrieve simulated emails queue
    emails = []  # Simulated mailbox disabled

    # Retrieve all users for user management tab (Admin only)
    users = []
    if is_admin:
        cursor.execute("SELECT id, username, email, role, department FROM users ORDER BY username")
        users = cursor.fetchall()

    conn.close()

    return render_template(
        'dashboard.html',
        username=username,
        role=role,
        department=department,
        is_admin=is_admin,
        is_manager=is_manager,
        history=history,
        glossary=glossary,
        daily_stats=daily_stats,
        engine_stats=engine_stats,
        department_stats=department_stats,
        stat_total_translations=stat_total_translations,
        stat_total_words=stat_total_words,
        stat_active_dept=stat_active_dept,
        stat_common_dir=stat_common_dir,
        stat_avg_file_size=stat_avg_file_size,
        stat_avg_confidence=stat_avg_confidence,
        error=error,
        translated=translated,
        total_words=total_words,
        masked_words=masked_words,
        glossary_words=glossary_words,
        confidence_score=confidence_score,
        paragraphs=paragraphs,
        cost=cost,
        emails=emails,
        users=users,
        filename_used=filename_used
    )

# --- BATCH FILE UPLOAD & PROGRESS BAR ---

@app.route('/translate-batch', methods=['POST'])
def translate_batch():
    if not session.get('username'):
        return jsonify({'error': 'Unauthorized'}), 401
        
    username = session.get('username')
    role = session.get('role', 'staff')
    department = session.get('department', 'Engineering')
    direction = request.form.get('direction', 'eng-spa')
    engine = request.form.get('engine', 'google')
    
    # Parse optional custom words to mask
    custom_words_raw = request.form.get('custom_mask_words', '').strip()
    custom_words = [w.strip() for w in custom_words_raw.split(',') if w.strip()] if custom_words_raw else []
    
    files = request.files.getlist('documents')
    
    if not files or files[0].filename == '':
        return jsonify({'error': 'No files uploaded'}), 400
        
    job_id = str(uuid.uuid4())
    files_list = []
    
    for file in files:
        if file and allowed_file(file.filename):
            safe_name = secure_filename(file.filename)
            temp_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_{safe_name}")
            file.save(temp_path)
            files_list.append((file.filename, temp_path))
            
    if not files_list:
        return jsonify({'error': 'No supported files selected'}), 400
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Record batch job in MySQL
    cursor.execute(
        "INSERT INTO batch_jobs (id, username, total_files, processed_files, status) VALUES (%s, %s, %s, %s, %s)",
        (job_id, username, len(files_list), 0, 'Processing')
    )
    conn.commit()
    
    glossary_rules = get_glossary_rules(conn, direction)
    conn.close()
    
    # Launch worker thread to perform async translation
    thread = threading.Thread(
        target=process_batch_job,
        args=(job_id, files_list, direction, engine, department, username, role, glossary_rules, custom_words)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'job_id': job_id,
        'total_files': len(files_list),
        'status': 'Processing'
    })

@app.route('/batch-status/<job_id>', methods=['GET'])
def batch_status(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT total_files, processed_files, status, zip_filename FROM batch_jobs WHERE id = %s", (job_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'error': 'Job not found'}), 404
        
    return jsonify({
        'total_files': row['total_files'],
        'processed_files': row['processed_files'],
        'status': row['status'],
        'zip_filename': row['zip_filename']
    })

@app.route('/download-batch/<job_id>', methods=['GET'])
def download_batch(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT zip_filename, username FROM batch_jobs WHERE id = %s", (job_id,))
    row = cursor.fetchone()
    
    if not row or not row['zip_filename']:
        conn.close()
        return "Batch job files not found or still processing.", 404
        
    zip_path = os.path.join(BATCH_FOLDER, row['zip_filename'])
    if not os.path.exists(zip_path):
        conn.close()
        return "Compiled ZIP archive could not be located.", 404
        
    # log_action omitted in download_batch
    conn.commit()
    conn.close()
    
    return send_file(zip_path, as_attachment=True, download_name=row['zip_filename'])

@app.route('/download-file/<filename>', methods=['GET'])
def download_file(filename):
    if not session.get('username'):
        return redirect(url_for('login'))
        
    safe_name = secure_filename(filename)
    translated_path = os.path.join(UPLOAD_FOLDER, f"translated_{safe_name}")
    
    if not os.path.exists(translated_path):
        # Fallback to check if the file is stored directly under safe_name
        translated_path = os.path.join(UPLOAD_FOLDER, safe_name)
        if not os.path.exists(translated_path):
            return "Translated document not found or expired.", 404
            
    conn = get_db_connection()
    # log_action omitted in download_file
    conn.commit()
    conn.close()
    
    return send_file(translated_path, as_attachment=True, download_name=f"translated_{safe_name}")

# --- DYNAMIC GLOSSARY RULE MANAGER ---

@app.route('/glossary/add', methods=['POST'])
def glossary_add():
    if not session.get('username') or session.get('role') == 'staff':
        return redirect(url_for('dashboard'))
        
    source = request.form.get('source_term', '').strip()
    target = request.form.get('target_term', '').strip()  # Empty means Do Not Translate
    direction = request.form.get('direction', 'all')
    
    if source:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO glossary (source_term, target_term, direction) VALUES (%s, %s, %s)",
            (source, target, direction)
        )
        log_action(conn, session.get('username'), "add_glossary_rule", f"Created term rule: '{source}' -> '{target or '[PRESERVED]'}' ({direction})")
        conn.commit()
        conn.close()
        
    return redirect(url_for('dashboard', _anchor='glossaryTab'))

@app.route('/glossary/delete/<int:term_id>', methods=['POST'])
def glossary_delete(term_id):
    if not session.get('username') or session.get('role') == 'staff':
        return redirect(url_for('dashboard'))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT source_term FROM glossary WHERE id = %s", (term_id,))
    term_row = cursor.fetchone()
    
    if term_row:
        cursor.execute("DELETE FROM glossary WHERE id = %s", (term_id,))
        log_action(conn, session.get('username'), "delete_glossary_rule", f"Deleted glossary rule for '{term_row['source_term']}'.")
        conn.commit()
        
    conn.close()
    return redirect(url_for('dashboard', _anchor='glossaryTab'))

# --- USER MANAGEMENT ROUTE OVERRIDES ---

@app.route('/manage-users/add', methods=['POST'])
def manage_users_add():
    if not session.get('username') or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
        
    new_username = request.form.get('new_username', '').strip()
    new_email = request.form.get('new_email', '').strip()
    new_role = request.form.get('new_role', 'staff')
    new_dept = request.form.get('new_dept', 'Engineering')
    
    if new_username and new_email:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT IGNORE INTO users (username, email, role, department) VALUES (%s, %s, %s, %s)",
            (new_username, new_email, new_role, new_dept)
        )
        log_action(conn, session.get('username'), "create_user", f"Registered new user {new_username} ({new_role}) in {new_dept} department.")
        conn.commit()
        conn.close()
        
    return redirect(url_for('dashboard', _anchor='usersTab'))

@app.route('/manage-users/delete/<int:user_id>', methods=['POST'])
def manage_users_delete(user_id):
    if not session.get('username') or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    user_row = cursor.fetchone()
    
    if user_row:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        log_action(conn, session.get('username'), "delete_user", f"Removed user profile for '{user_row['username']}'.")
        conn.commit()
        
    conn.close()
    return redirect(url_for('dashboard', _anchor='usersTab'))

# --- COMPREHENSIVE AUDIT & CSV TRAILING ---

@app.route('/audit/logs', methods=['GET'])
def get_audits():
    if not session.get('username') or session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
        
    search = request.args.get('search')
    user_filter = request.args.get('user')
    action_filter = request.args.get('action')
    
    conn = get_db_connection()
    logs = get_audit_logs(conn, search, user_filter, action_filter)
    conn.close()
    
    # Map raw rows to JSON lists
    logs_json = []
    for log in logs:
        logs_json.append({
            'username': log['username'],
            'action': log['action'],
            'details': log['details'],
            'timestamp': log['timestamp']
        })
        
    return jsonify(logs_json)
 
@app.route('/audit/export', methods=['GET'])
def export_audits():
    if not session.get('username') or session.get('role') != 'admin':
        return "Unauthorized access to export systems", 403
        
    search = request.args.get('search')
    user_filter = request.args.get('user')
    action_filter = request.args.get('action')
    
    conn = get_db_connection()
    csv_string = export_audit_csv(conn, search, user_filter, action_filter)
    log_action(conn, session.get('username'), "export_audits", "Exported filtered database audit trails to CSV.")
    conn.commit()
    conn.close()
    
    # Turn into downloadable attachment stream
    mem_file = io.BytesIO()
    mem_file.write(csv_string.encode('utf-8'))
    mem_file.seek(0)
    
    # Need to import io in scope if not available
    return send_file(
        mem_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"DocTranslate_AuditLog_{datetime.datetime.now().strftime('%Y%m%d')}.csv"
    )

@app.route('/simulated-mail/clear', methods=['POST'])
def clear_mail():
    if not session.get('username'):
        return jsonify({'error': 'Unauthorized'}), 401
    clear_simulated_mailbox()
    return jsonify({'status': 'Mailbox cleared'})

@app.route('/analytics/export', methods=['GET'])
def export_analytics():
    if not session.get('username') or session.get('role') not in ('admin', 'manager'):
        return "Unauthorized", 403
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, filename, file_size, direction, total_words, masked_words, glossary_words, engine, department, confidence_score, cost, timestamp FROM translations ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    import csv
    import io
    import datetime
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Username', 'Filename', 'File Size (Bytes)', 'Direction', 'Total Words', 'Masked Words', 'Glossary Matches', 'Engine', 'Department', 'Confidence Score', 'Cost ($)', 'Timestamp'])
    for r in rows:
        writer.writerow([r['id'], r['username'], r['filename'], r['file_size'], r['direction'], r['total_words'], r['masked_words'], r['glossary_words'], r['engine'], r['department'], r['confidence_score'], r['cost'], r['timestamp']])
        
    csv_data = output.getvalue()
    mem_file = io.BytesIO()
    mem_file.write(csv_data.encode('utf-8'))
    mem_file.seek(0)
    
    return send_file(
        mem_file,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"DocTranslate_AnalyticsReport_{datetime.datetime.now().strftime('%Y%m%d')}.csv"
    )

# Inject imports required inside endpoints
import io
import datetime

if __name__ == "__main__":
    # Run on port 8082 which is free from system/reserved conflicts
    app.run(debug=False, host='127.0.0.1', port=8082, use_reloader=False)