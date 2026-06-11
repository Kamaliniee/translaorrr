import os
import io
import uuid
import secrets
import zipfile
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Load Environment Variables from .env file
load_dotenv()

# Import Services
from services.translatorr import translate_text, get_engine_display_name, check_api_connections
from services.filehandler import translate_file
from services.audit_service import log_action, get_audit_logs, export_audit_csv, log_login_event
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

# Generate a unique token each time the server starts.
# This invalidates all old browser session cookies automatically,
# so every server restart forces users to log in again.
_STARTUP_TOKEN = secrets.token_hex(16)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET', 'dev-secret-key-1337-enterprise') + _STARTUP_TOKEN
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

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


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('user_id') or session.get('role') != 'admin':
            return redirect(url_for('dashboard'))
        return fn(*args, **kwargs)
    return wrapper


def parse_direction(direction):
    direction = direction.lower()
    if direction in ('en-es', 'eng-spa', 'english-spanish', 'english to spanish'):
        return 'en', 'es'
    if direction in ('es-en', 'spa-eng', 'spanish-english', 'spanish to english'):
        return 'es', 'en'
    return ('en', 'es')


def record_translation(conn, user_id, username, source_language, target_language, filename, file_type, file_size, word_count, status, processing_time, translated_text, engine, confidence_score, cost):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO translations
        (user_id, username, source_language, target_language, filename, file_type, file_size,
         word_count, status, processing_time, translated_text, engine, confidence_score, cost)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, username, source_language, target_language, filename, file_type, file_size,
         word_count, status, processing_time, translated_text, engine, confidence_score, cost)
    )
    conn.commit()

    # Update analytics summary table for the day (persists permanently in MySQL)
    today = datetime.utcnow().date()
    cursor.execute("SELECT id FROM analytics_summary WHERE report_date = %s", (today,))
    row = cursor.fetchone()
    if row:
        cursor.execute(
            """
            UPDATE analytics_summary
            SET total_translations = total_translations + 1,
                total_words = total_words + %s,
                files_processed = files_processed + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_date = %s
            """,
            (word_count, today)
        )
    else:
        cursor.execute(
            """
            INSERT INTO analytics_summary (report_date, total_translations, total_words, files_processed)
            VALUES (%s, %s, %s, %s)
            """,
            (today, 1, word_count, 1)
        )
    conn.commit()


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
        full_name VARCHAR(255),
        email VARCHAR(255) UNIQUE,
        password_hash VARCHAR(255),
        role VARCHAR(50) DEFAULT 'guest',
        active BOOLEAN DEFAULT TRUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)
    
    # 2. Base Translations Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS translations(
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        username VARCHAR(255),
        source_language VARCHAR(50),
        target_language VARCHAR(50),
        filename VARCHAR(255),
        file_type VARCHAR(50),
        file_size INT DEFAULT 0,
        word_count INT DEFAULT 0,
        status VARCHAR(50) DEFAULT 'Completed',
        processing_time DOUBLE DEFAULT 0.0,
        translated_text LONGTEXT,
        engine VARCHAR(50) DEFAULT 'google',
        confidence_score DOUBLE DEFAULT 95.0,
        cost DOUBLE DEFAULT 0.0,
        translated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    )
    """)
    
    # 3. Login Audit Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS login_audit(
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        username VARCHAR(255),
        email VARCHAR(255),
        role VARCHAR(50),
        ip_address VARCHAR(100),
        login_time DATETIME,
        logout_time DATETIME,
        status VARCHAR(50),
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    )
    """)
    
    # 4. Analytics Summary Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analytics_summary(
        id INT AUTO_INCREMENT PRIMARY KEY,
        report_date DATE UNIQUE,
        total_translations INT DEFAULT 0,
        total_words INT DEFAULT 0,
        files_processed INT DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)
    
    # 5. Custom Glossary Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS glossary(
        id INT AUTO_INCREMENT PRIMARY KEY,
        source_term VARCHAR(255),
        target_term VARCHAR(255),
        direction VARCHAR(50) DEFAULT 'all'
    )
    """)
    
    # 6. Comprehensive Audit Logs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(255),
        action VARCHAR(255),
        details TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 7. Bulk Batch Jobs Table
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
        ('user_id', 'INT'),
        ('source_language', "VARCHAR(50)"),
        ('target_language', "VARCHAR(50)"),
        ('file_type', "VARCHAR(50)"),
        ('word_count', 'INT DEFAULT 0'),
        ('status', "VARCHAR(50) DEFAULT 'Completed'"),
        ('processing_time', 'DOUBLE DEFAULT 0.0'),
        ('translated_at', 'DATETIME DEFAULT CURRENT_TIMESTAMP'),
        ('confidence_score', 'DOUBLE DEFAULT 95.0'),
        ('cost', 'DOUBLE DEFAULT 0.0')
    ]:
        if col not in columns_translations:
            cursor.execute(f"ALTER TABLE translations ADD COLUMN {col} {c_type}")

    if 'department' in columns_translations:
        cursor.execute("ALTER TABLE translations DROP COLUMN department")

    cursor.execute("SHOW COLUMNS FROM users")
    columns_users = [row['Field'] for row in cursor.fetchall()]
    for col, c_type in [
        ('full_name', 'VARCHAR(255)'),
        ('password_hash', 'VARCHAR(255)'),
        ('active', 'BOOLEAN DEFAULT TRUE'),
        ('created_at', 'DATETIME DEFAULT CURRENT_TIMESTAMP'),
        ('updated_at', 'DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')
    ]:
        if col not in columns_users:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {c_type}")

    if 'department' in columns_users:
        cursor.execute("ALTER TABLE users DROP COLUMN department")

    cursor.execute("SHOW TABLES LIKE 'login_audit'")
    if not cursor.fetchone():
        cursor.execute("""
        CREATE TABLE login_audit(
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            username VARCHAR(255),
            email VARCHAR(255),
            role VARCHAR(50),
            ip_address VARCHAR(100),
            login_time DATETIME,
            logout_time DATETIME,
            status VARCHAR(50),
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """)

    cursor.execute("SHOW TABLES LIKE 'analytics_summary'")
    if not cursor.fetchone():
        cursor.execute("""
        CREATE TABLE analytics_summary(
            id INT AUTO_INCREMENT PRIMARY KEY,
            report_date DATE UNIQUE,
            total_translations INT DEFAULT 0,
            total_words INT DEFAULT 0,
            files_processed INT DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """)

    # Seed Initial Enterprise Users
    initial_users = [
        ('AdminUser', 'Admin User', 'admin@company.com', 'admin'),
        ('ManagerUser', 'Manager User', 'manager@company.com', 'manager'),
        ('StaffUser', 'Staff User', 'staff@company.com', 'staff'),
        ('JohnDoe', 'John Doe', 'john.doe@company.com', 'staff'),
        ('SalesAgent', 'Sales Agent', 'sales@company.com', 'staff'),
        ('GuestUser', 'Guest User', 'guest@company.com', 'guest'),
    ]
    for username, full_name, email, role in initial_users:
        cursor.execute(
            "INSERT IGNORE INTO users (username, full_name, email, role, password_hash, active) VALUES (%s, %s, %s, %s, %s, TRUE)",
            (username, full_name, email, role, generate_password_hash('password'))
        )
        
    # Seed Sample Glossary Terms
    sample_glossary = [
        ('WebLogic', '', 'all'),  # Do Not Translate
        ('Director General', 'Director General', 'eng-spa'),  # Fixed Translation
        ('DocTranslate', '', 'all'),  # Do Not Translate
        ('CEO', 'Director Ejecutivo', 'eng-spa'),
    ]
    cursor.execute("SELECT COUNT(*) AS count FROM glossary")
    if cursor.fetchone()['count'] == 0:
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
        'role': session.get('role', 'staff')
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
    
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_record = cursor.fetchone()
    user_id = user_record['id'] if user_record else None
    
    for idx, (original_filename, temp_filepath) in enumerate(files_list):
        safe_name = secure_filename(original_filename)
        output_filepath = os.path.join(job_dir, f"translated_{safe_name}")
        
        start_time = time.perf_counter()
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

            processing_time = round(time.perf_counter() - start_time, 3)
            source_language, target_language = parse_direction(direction)
            file_type = os.path.splitext(original_filename)[1].lower().lstrip('.') or 'unknown'
            file_size = os.path.getsize(temp_filepath) if os.path.exists(temp_filepath) else 0

            record_translation(
                conn,
                user_id,
                username,
                source_language,
                target_language,
                original_filename,
                file_type,
                file_size,
                p_words,
                'Completed',
                processing_time,
                None,
                engine,
                p_confidence,
                p_cost
            )
            
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

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        credentials = request.form
        user_input = credentials.get('username', '').strip()
        password = credentials.get('password', '')
        if not user_input or not password:
            error = 'Please enter your email/username and password.'
        else:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, full_name, email, password_hash, role, active FROM users WHERE username = %s OR email = %s",
                (user_input, user_input)
            )
            user_row = cursor.fetchone()
            ip_address = get_client_ip()
            login_time = datetime.utcnow()
            login_status = 'failed'
            login_details = None
            login_audit_id = None

            if not user_row:
                login_details = 'User not found.'
            elif not user_row['active']:
                login_details = 'Account is inactive.'
            elif not user_row['password_hash'] or not check_password_hash(user_row['password_hash'], password):
                login_details = 'Invalid password.'
            else:
                login_status = 'success'
                session['user_id'] = user_row['id']
                session['username'] = user_row['username']
                session['role'] = user_row['role']
                session['full_name'] = user_row.get('full_name') or user_row['username']

            if user_row:
                login_audit_id = log_login_event(
                    conn,
                    user_row['id'],
                    user_row['username'],
                    user_row['email'],
                    user_row['role'],
                    ip_address,
                    login_time,
                    None,
                    login_status,
                    login_details
                )
            else:
                log_login_event(
                    conn,
                    None,
                    user_input,
                    user_input,
                    'unknown',
                    ip_address,
                    login_time,
                    None,
                    'failed',
                    login_details
                )

            if login_status == 'success':
                log_action(conn, session['username'], 'login', 'User authenticated successfully.')
                conn.close()
                return redirect(url_for('dashboard'))

            error = 'Invalid login credentials or inactive account.'
            conn.close()

    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return render_template('login.html', error=error)

@app.route('/register', methods=['POST'])
def register():
    error = None
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not username or not full_name or not email or not password or not confirm_password:
        error = 'All fields are required for registration.'
    elif password != confirm_password:
        error = 'Password and confirm password do not match.'
    elif len(password) < 8:
        error = 'Password must be at least 8 characters long.'
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
        existing = cursor.fetchone()
        if existing:
            error = 'Username or email is already registered.'
        else:
            password_hash = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (username, full_name, email, password_hash, role, active) VALUES (%s, %s, %s, %s, 'guest', TRUE)",
                (username, full_name, email, password_hash)
            )
            conn.commit()
            user_id = cursor.lastrowid
            log_login_event(
                conn,
                user_id,
                username,
                email,
                'guest',
                get_client_ip(),
                datetime.utcnow(),
                None,
                'registered',
                'New user registration'
            )
            log_action(conn, username, 'register', 'New account created.')
            conn.close()
            session['user_id'] = user_id
            session['username'] = username
            session['role'] = 'guest'
            session['full_name'] = full_name
            return redirect(url_for('dashboard'))
        conn.close()

    return render_template('login.html', error=error, active_tab='register')

@app.route('/logout')
def logout():
    username = session.get('username')
    user_id = session.get('user_id')
    if username:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE login_audit SET logout_time = %s WHERE user_id = %s AND status IN ('success','failed') ORDER BY id DESC LIMIT 1",
            (datetime.utcnow(), user_id)
        )
        log_action(conn, username, 'logout', 'User logged out of active session.')
        conn.close()
    session.clear()
    return redirect(url_for('login'))

@app.route('/account/update', methods=['POST'])
@login_required
def account_update():
    user_id = session.get('user_id')
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    current_password = request.form.get('current_password', '')
    password = request.form.get('password', '')

    if not username or not email or not full_name:
        session['account_error'] = 'Username, Full Name, and Email are required.'
        return redirect(url_for('dashboard', _anchor='accountTab'))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check if username or email is already taken by another user
        cursor.execute("SELECT id FROM users WHERE (username = %s OR email = %s) AND id != %s", (username, email, user_id))
        existing = cursor.fetchone()
        if existing:
            session['account_error'] = 'Username or Email address already exists!'
            return redirect(url_for('dashboard', _anchor='accountTab'))

        if password:
            if not current_password:
                session['account_error'] = 'Current password is required to set a new password.'
                return redirect(url_for('dashboard', _anchor='accountTab'))
                
            cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            user_row = cursor.fetchone()
            if not user_row or not check_password_hash(user_row['password_hash'], current_password):
                session['account_error'] = 'Incorrect current password!'
                return redirect(url_for('dashboard', _anchor='accountTab'))

            if len(password) < 8:
                session['account_error'] = 'Password must be at least 8 characters long.'
                return redirect(url_for('dashboard', _anchor='accountTab'))
            password_hash = generate_password_hash(password)
            cursor.execute(
                "UPDATE users SET username = %s, full_name = %s, email = %s, password_hash = %s WHERE id = %s",
                (username, full_name, email, password_hash, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET username = %s, full_name = %s, email = %s WHERE id = %s",
                (username, full_name, email, user_id)
            )

        conn.commit()

        # Update session info
        session['username'] = username
        session['full_name'] = full_name

        log_action(conn, username, 'update_profile', f"User profile updated: username={username}, email={email}")
        session['account_success'] = 'Profile updated successfully!'
    except Exception as e:
        session['account_error'] = f"An error occurred: {str(e)}"
    finally:
        conn.close()

    return redirect(url_for('dashboard', _anchor='accountTab'))

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
    username = session.get('username', 'Guest')

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
            except Exception as e:
                error = f"Translation failed: {e}"

    return render_template(
        'upload.html',
        error=error,
        translated=translated,
        filename=filename_used,
        direction=direction,
        badge_text=badge_text,
        total_words=total_words
    )

@app.route('/', methods=['GET', 'POST'])
@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    username = session.get('username')
    role = session.get('role', 'staff')
    is_admin = role == 'admin'
    is_manager = role == 'manager' or is_admin
    
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
        user_id = session.get('user_id')
        
        if file and file.filename != '':
            if not allowed_file(file.filename):
                error = 'Unsupported file format! Upload DOCX, XLSX, TXT, CSV, or PDF.'
            else:
                filename_used = file.filename
                safe_name = secure_filename(file.filename)
                temp_in = os.path.join(UPLOAD_FOLDER, safe_name)
                temp_out = os.path.join(UPLOAD_FOLDER, f"translated_{safe_name}")
                
                file.save(temp_in)
                file_size_bytes = os.path.getsize(temp_in)
                
                try:
                    start_time = time.perf_counter()
                    p_words, p_masked, p_glossary, p_confidence, p_cost = translate_file(
                        temp_in, temp_out, direction, selected_engine, 'general', glossary_rules, custom_words
                    )
                    processing_time = round(time.perf_counter() - start_time, 3)
                    
                    ext = os.path.splitext(safe_name)[1].lower()
                    if ext in ('.txt', '.csv'):
                        with open(temp_out, 'r', encoding='utf-8', errors='ignore') as preview_f:
                            translated = preview_f.read(10000)
                    else:
                        translated = f"Format-preserved {ext.upper()} translation complete. File is ready for download."
                    
                    total_words = p_words
                    masked_words = p_masked
                    glossary_words = p_glossary
                    confidence_score = p_confidence
                    cost = p_cost
                    
                    source_language, target_language = parse_direction(direction)
                    record_translation(
                        conn,
                        user_id,
                        username,
                        source_language,
                        target_language,
                        filename_used,
                        ext.lstrip('.') or 'unknown',
                        file_size_bytes,
                        p_words,
                        'Completed',
                        processing_time,
                        None,
                        selected_engine,
                        p_confidence,
                        p_cost
                    )

                    log_action(conn, username, 'file_translation', f"Translated '{filename_used}' ({p_words:,} words) using {selected_engine}.")
                except Exception as e:
                    error = f"Translation engine failure: {e}"
                    print(f"Translation Crash: {e}")
        else:
            raw_text = request.form.get('raw_text', '').strip()
            if raw_text:
                start_time = time.perf_counter()
                translated, total_words, masked_words, glossary_words, confidence_score, paragraphs, cost = translate_text(
                    raw_text, direction, selected_engine, 'general', glossary_rules, custom_words
                )
                processing_time = round(time.perf_counter() - start_time, 3)
                
                source_language, target_language = parse_direction(direction)
                record_translation(
                    conn,
                    user_id,
                    username,
                    source_language,
                    target_language,
                    'Text Box Input',
                    'text',
                    len(raw_text),
                    total_words,
                    'Completed',
                    processing_time,
                    translated,
                    selected_engine,
                    confidence_score,
                    cost
                )
                log_action(conn, username, 'text_translation', f"Translated raw text input ({total_words:,} words) using {selected_engine}.")
                
                # check_and_notify_manager omitted as notifications disabled

    # 2. Retrieve DB records depending on RBAC permissions
    if is_admin or is_manager:
        cursor.execute("SELECT * FROM translations ORDER BY id DESC LIMIT 100")
    else:
        cursor.execute("SELECT * FROM translations WHERE username = %s ORDER BY id DESC LIMIT 50", (username,))
    history = cursor.fetchall()

    # 3. Retrieve glossary rules
    cursor.execute("SELECT id, source_term, target_term, direction FROM glossary ORDER BY id DESC")
    glossary = cursor.fetchall()

    # 4. Gather Dashboard Stats (Usage Analytics)
    cursor.execute("""
        SELECT DATE_FORMAT(translated_at, '%%m-%%d') as day, COUNT(*) as count, SUM(word_count) as words
        FROM translations
        GROUP BY day
        ORDER BY MIN(translated_at) DESC LIMIT 7
    """)
    daily_stats = cursor.fetchall()
    
    cursor.execute("SELECT engine, COUNT(*) as count, SUM(cost) as total_cost FROM translations GROUP BY engine")
    engine_stats = cursor.fetchall()

    cursor.execute("SELECT CONCAT(source_language, ' → ', target_language) AS pair, COUNT(*) AS count FROM translations GROUP BY source_language, target_language ORDER BY count DESC")
    language_pair_stats = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM translations")
    stat_total_translations = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(word_count) FROM translations")
    stat_total_words = cursor.fetchone()[0] or 0

    cursor.execute("SELECT AVG(file_size) FROM translations WHERE file_size > 0")
    avg_file_row = cursor.fetchone()
    stat_avg_file_size = round(float(avg_file_row[0] or 0.0) / 1024.0, 1) if avg_file_row else 0.0

    cursor.execute("SELECT source_language, target_language, COUNT(*) as count FROM translations GROUP BY source_language, target_language ORDER BY count DESC LIMIT 1")
    common_dir_row = cursor.fetchone()
    stat_common_dir = f"{common_dir_row['source_language']} → {common_dir_row['target_language']}" if common_dir_row else "None"

    emails = []  # Simulated mailbox disabled
    users = []
    if is_admin:
        cursor.execute("SELECT id, username, full_name, email, role, active FROM users ORDER BY username")
        users = cursor.fetchall()

    cursor.execute("SELECT id, username, full_name, email, role FROM users WHERE id = %s", (session.get('user_id'),))
    current_user = cursor.fetchone()
    if not current_user:
        current_user = {
            'username': username,
            'full_name': session.get('full_name') or username,
            'email': '',
            'role': role
        }

    conn.close()

    user_error = session.pop('user_error', None)
    user_success = session.pop('user_success', None)
    account_error = session.pop('account_error', None)
    account_success = session.pop('account_success', None)

    return render_template(
        'dashboard.html',
        username=username,
        role=role,
        is_admin=is_admin,
        is_manager=is_manager,
        history=history,
        glossary=glossary,
        daily_stats=daily_stats,
        engine_stats=engine_stats,
        language_pair_stats=language_pair_stats,
        stat_total_translations=stat_total_translations,
        stat_total_words=stat_total_words,
        stat_common_dir=stat_common_dir,
        stat_avg_file_size=stat_avg_file_size,
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
        filename_used=filename_used,
        user_error=user_error,
        user_success=user_success,
        current_user=current_user,
        account_error=account_error,
        account_success=account_success
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
@admin_required
def manage_users_add():
    new_username = request.form.get('new_username', '').strip()
    new_full_name = request.form.get('new_full_name', '').strip()
    new_email = request.form.get('new_email', '').strip().lower()
    new_role = request.form.get('new_role', 'guest')
    new_password = request.form.get('new_password', 'password123').strip()

    allowed_roles = ('admin', 'manager', 'staff', 'guest')
    if new_role not in allowed_roles:
        new_role = 'guest'

    if not new_username or not new_email:
        session['user_error'] = "Username and email address are required!"
        return redirect(url_for('dashboard', _anchor='usersTab'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (new_username, new_email))
    if cursor.fetchone():
        session['user_error'] = "Username or Email address already exists!"
    else:
        password_hash = generate_password_hash(new_password)
        cursor.execute(
            "INSERT INTO users (username, full_name, email, password_hash, role, active) VALUES (%s, %s, %s, %s, %s, TRUE)",
            (new_username, new_full_name or new_username, new_email, password_hash, new_role)
        )
        log_action(conn, session.get('username'), 'create_user', f"Registered new user {new_username} ({new_role}).")
        conn.commit()
        session['user_success'] = f"Employee profile for {new_username} registered successfully!"
    conn.close()
    return redirect(url_for('dashboard', _anchor='usersTab'))

@app.route('/manage-users/update-role/<int:user_id>', methods=['POST'])
@admin_required
def manage_users_update_role(user_id):
    new_role = request.form.get('new_role', '').strip().lower()
    allowed_roles = ('admin', 'manager', 'staff', 'guest')
    if not new_role or new_role not in allowed_roles:
        return redirect(url_for('dashboard', _anchor='usersTab'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, role FROM users WHERE id = %s", (user_id,))
    user_row = cursor.fetchone()
    # Admins can change any user's role except their own
    if user_row and user_row['username'] != session.get('username'):
        cursor.execute("UPDATE users SET role = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (new_role, user_id))
        log_action(conn, session.get('username'), 'update_user_role', f"Changed role for {user_row['username']} from {user_row['role']} to {new_role}.")
        conn.commit()
    conn.close()
    return redirect(url_for('dashboard', _anchor='usersTab'))

@app.route('/manage-users/delete/<int:user_id>', methods=['POST'])
@admin_required
def manage_users_delete(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    user_row = cursor.fetchone()
    if user_row:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        log_action(conn, session.get('username'), 'delete_user', f"Removed user profile for '{user_row['username']}'.")
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
        download_name=f"DocTranslate_AuditLog_{datetime.now().strftime('%Y%m%d')}.csv"
    )

@app.route('/simulated-mail/clear', methods=['POST'])
def clear_mail():
    if not session.get('username'):
        return jsonify({'error': 'Unauthorized'}), 401
    clear_simulated_mailbox()
    return jsonify({'status': 'Mailbox cleared'})

@app.route('/analytics/export', methods=['GET'])
@login_required
def export_analytics():
    if session.get('role') not in ('admin', 'manager'):
        return "Unauthorized", 403

    from_date_raw = request.args.get('from_date', '')
    to_date_raw = request.args.get('to_date', '')
    output_format = request.args.get('format', 'csv').lower()

    query = "SELECT id, username, source_language, target_language, filename, file_type, file_size, word_count, status, processing_time, engine, confidence_score, cost, translated_at FROM translations"
    params = []
    filters = []

    try:
        if from_date_raw:
            from_date = datetime.strptime(from_date_raw, '%Y-%m-%d')
            filters.append('translated_at >= %s')
            params.append(from_date.strftime('%Y-%m-%d 00:00:00'))
        if to_date_raw:
            to_date = datetime.strptime(to_date_raw, '%Y-%m-%d')
            filters.append('translated_at <= %s')
            params.append(to_date.strftime('%Y-%m-%d 23:59:59'))
    except ValueError:
        return "Invalid date range format. Use YYYY-MM-DD.", 400

    if filters:
        query += ' WHERE ' + ' AND '.join(filters)
    query += ' ORDER BY translated_at DESC'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) as total_translations, COALESCE(SUM(word_count),0) as total_words, COALESCE(AVG(file_size),0) as avg_file_size, COUNT(DISTINCT CONCAT(source_language,'-',target_language)) as lang_pairs FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else ''), params)
    stats = cursor.fetchone()

    # Language pair breakdown
    cursor.execute("SELECT CONCAT(source_language, ' → ', target_language) AS pair, COUNT(*) AS count FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY source_language, target_language ORDER BY count DESC LIMIT 5", params)
    pair_breakdown = cursor.fetchall()

    # Daily counts for report
    cursor.execute("SELECT DATE_FORMAT(translated_at, '%%Y-%%m-%%d') as day, COUNT(*) as count, COALESCE(SUM(word_count),0) as words FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY day ORDER BY day DESC LIMIT 10", params)
    daily_counts = cursor.fetchall()

    # User activity for report
    cursor.execute("SELECT username, COUNT(*) as count, COALESCE(SUM(word_count),0) as words FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY username ORDER BY count DESC LIMIT 10", params)
    user_activity = cursor.fetchall()

    conn.close()

    report_name = f"DocTranslate_AnalyticsReport_{datetime.utcnow().strftime('%Y%m%d')}." + ('xlsx' if output_format == 'xlsx' else 'csv')

    if output_format == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = Workbook()
        summary = wb.active
        summary.title = 'Summary'
        # Header styling
        header_font = Font(bold=True, size=12)
        header_fill = PatternFill('solid', fgColor='DC2626')

        summary.append(['DocTranslate Analytics Report'])
        summary.append(['Report generated', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')])
        summary.append(['Period From', from_date_raw or 'All time'])
        summary.append(['Period To', to_date_raw or 'All time'])
        summary.append([])
        summary.append(['SUMMARY METRICS', ''])
        summary.append(['Total Translations', stats['total_translations']])
        summary.append(['Total Words Processed', stats['total_words']])
        summary.append(['Files Translated', stats['total_translations']])
        summary.append(['Average File Size (KB)', round(float(stats['avg_file_size'] or 0) / 1024, 2)])
        summary.append(['Language Pairs Used', stats.get('lang_pairs', 0)])
        summary.append([])

        # Language pair breakdown
        summary.append(['LANGUAGE PAIR STATISTICS', ''])
        summary.append(['Language Pair', 'Translation Count'])
        for p in pair_breakdown:
            summary.append([p['pair'], p['count']])
        summary.append([])

        # Daily counts
        summary.append(['DAILY TRANSLATION COUNTS', ''])
        summary.append(['Date', 'Translations', 'Words'])
        for d in daily_counts:
            summary.append([d['day'], d['count'], d['words']])
        summary.append([])

        # User activity
        summary.append(['USER ACTIVITY SUMMARY', ''])
        summary.append(['Username', 'Translations', 'Words'])
        for u in user_activity:
            summary.append([u['username'], u['count'], u['words']])

        detail_sheet = wb.create_sheet('Translation Details')
        detail_sheet.append(['ID', 'Username', 'Source Lang', 'Target Lang', 'Filename', 'File Type', 'File Size (Bytes)', 'Word Count', 'Status', 'Processing Time (s)', 'Engine', 'Translated At'])
        for r in rows:
            detail_sheet.append([r['id'], r['username'], r['source_language'], r['target_language'], r['filename'], r['file_type'], r['file_size'], r['word_count'], r['status'], r['processing_time'], r['engine'], str(r['translated_at'])])

        mem_file = io.BytesIO()
        wb.save(mem_file)
        mem_file.seek(0)
        return send_file(mem_file, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=report_name)

    import csv as csv_module

    output = io.StringIO()
    writer = csv_module.writer(output)
    writer.writerow(['DocTranslate Analytics Report'])
    writer.writerow(['Report generated', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')])
    writer.writerow(['Period From', from_date_raw or 'All time'])
    writer.writerow(['Period To', to_date_raw or 'All time'])
    writer.writerow([])
    writer.writerow(['SUMMARY METRICS', ''])
    writer.writerow(['Total Translations', stats['total_translations']])
    writer.writerow(['Total Words Processed', stats['total_words']])
    writer.writerow(['Files Translated', stats['total_translations']])
    writer.writerow(['Average File Size (KB)', round(float(stats['avg_file_size'] or 0) / 1024, 2)])
    writer.writerow(['Language Pairs Used', stats.get('lang_pairs', 0)])
    writer.writerow([])
    writer.writerow(['LANGUAGE PAIR STATISTICS', ''])
    writer.writerow(['Language Pair', 'Count'])
    for p in pair_breakdown:
        writer.writerow([p['pair'], p['count']])
    writer.writerow([])
    writer.writerow(['DAILY TRANSLATION COUNTS', ''])
    writer.writerow(['Date', 'Translations', 'Words'])
    for d in daily_counts:
        writer.writerow([d['day'], d['count'], d['words']])
    writer.writerow([])
    writer.writerow(['USER ACTIVITY SUMMARY', ''])
    writer.writerow(['Username', 'Translations', 'Words'])
    for u in user_activity:
        writer.writerow([u['username'], u['count'], u['words']])
    writer.writerow([])
    writer.writerow(['TRANSLATION DETAILS', ''])
    writer.writerow(['ID', 'Username', 'Source Lang', 'Target Lang', 'Filename', 'File Type', 'File Size (Bytes)', 'Word Count', 'Status', 'Processing Time (s)', 'Engine', 'Translated At'])
    for r in rows:
        writer.writerow([r['id'], r['username'], r['source_language'], r['target_language'], r['filename'], r['file_type'], r['file_size'], r['word_count'], r['status'], r['processing_time'], r['engine'], str(r['translated_at'])])

    csv_data = output.getvalue()
    mem_file = io.BytesIO()
    mem_file.write(csv_data.encode('utf-8-sig'))  # UTF-8 BOM for Excel compatibility
    mem_file.seek(0)
    return send_file(mem_file, mimetype='text/csv', as_attachment=True, download_name=report_name)

@app.route('/analytics/export/pdf', methods=['GET'])
@login_required
def export_analytics_pdf():
    if session.get('role') not in ('admin', 'manager'):
        return "Unauthorized", 403

    from_date_raw = request.args.get('from_date', '')
    to_date_raw = request.args.get('to_date', '')
    query = "SELECT username, source_language, target_language, filename, file_type, word_count, processing_time, engine, translated_at FROM translations"
    params = []
    filters = []

    try:
        if from_date_raw:
            from_date = datetime.strptime(from_date_raw, '%Y-%m-%d')
            filters.append('translated_at >= %s')
            params.append(from_date.strftime('%Y-%m-%d 00:00:00'))
        if to_date_raw:
            to_date = datetime.strptime(to_date_raw, '%Y-%m-%d')
            filters.append('translated_at <= %s')
            params.append(to_date.strftime('%Y-%m-%d 23:59:59'))
    except ValueError:
        return "Invalid date filter format. Use YYYY-MM-DD.", 400

    if filters:
        query += ' WHERE ' + ' AND '.join(filters)
    query += ' ORDER BY translated_at DESC LIMIT 1000'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) as total_translations, COALESCE(SUM(word_count),0) as total_words, COALESCE(AVG(file_size),0) as avg_file_size FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else ''), params)
    stats = cursor.fetchone()
    cursor.execute("SELECT source_language, target_language, COUNT(*) as count FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY source_language, target_language ORDER BY count DESC LIMIT 5", params)
    pair_stats = cursor.fetchall()
    cursor.execute("SELECT username, COUNT(*) as count, COALESCE(SUM(word_count),0) as words FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY username ORDER BY count DESC LIMIT 5", params)
    user_stats = cursor.fetchall()
    cursor.execute("SELECT DATE_FORMAT(translated_at, '%%m-%%d') as day, COUNT(*) as count FROM translations" + ((' WHERE ' + ' AND '.join(filters)) if filters else '') + " GROUP BY day ORDER BY MIN(translated_at) DESC LIMIT 7", params)
    daily_stats = cursor.fetchall()
    conn.close()

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib import colors

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    pdf.setTitle('DocTranslate Usage Report')
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(50, height - 60, 'DocTranslate Usage Report')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, height - 80, f'Report Period: {from_date_raw or "All time"} to {to_date_raw or "All time"}')
    pdf.drawString(50, height - 95, f'Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}')

    pdf.setFillColor(colors.HexColor('#0f172a'))
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(50, height - 125, 'Summary Metrics')
    pdf.setFont('Helvetica', 10)
    summary_y = height - 145
    pdf.drawString(60, summary_y, f'Total translations: {stats["total_translations"]}')
    pdf.drawString(250, summary_y, f'Total words processed: {stats["total_words"]}')
    pdf.drawString(60, summary_y - 16, f'Average file size: {round(float(stats["avg_file_size"] or 0),1)} bytes')
    pdf.drawString(250, summary_y - 16, f'Files translated: {len(rows)}')

    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(50, summary_y - 50, 'Top Language Pairs')
    pdf.setFont('Helvetica', 10)
    y = summary_y - 70
    for pair in pair_stats:
        pdf.drawString(60, y, f'{pair["source_language"]} → {pair["target_language"]}: {pair["count"]}')
        y -= 14

    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(50, y - 10, 'Top Users')
    pdf.setFont('Helvetica', 10)
    y -= 30
    for user in user_stats:
        pdf.drawString(60, y, f'{user["username"]}: {user["count"]} translations, {user["words"]} words')
        y -= 14

    y -= 20
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(50, y, 'Daily Translation Counts')
    y -= 18
    pdf.setFont('Helvetica', 10)
    for day in daily_stats:
        pdf.drawString(60, y, f'{day["day"]}: {day["count"]}')
        y -= 14

    y -= 30
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(50, y, 'Recent Translation Activity')
    y -= 18
    pdf.setFont('Helvetica', 9)
    pdf.drawString(50, y, 'Username')
    pdf.drawString(150, y, 'File')
    pdf.drawString(340, y, 'Words')
    pdf.drawString(400, y, 'Engine')
    pdf.drawString(470, y, 'Date')
    y -= 14
    for row in rows[:12]:
        if y < 80:
            pdf.showPage()
            y = height - 60
        pdf.drawString(50, y, str(row['username']))
        pdf.drawString(150, y, str(row['filename'])[:25])
        pdf.drawString(340, y, str(row['word_count']))
        pdf.drawString(400, y, str(row['engine']))
        pdf.drawString(470, y, str(row['translated_at'])[:10])
        y -= 14

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}.pdf")

if __name__ == "__main__":
    # Run on port 8082 which is free from system/reserved conflicts
    app.run(debug=False, host='127.0.0.1', port=8082, use_reloader=False)