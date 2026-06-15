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
from services.filehandler import translate_file, check_translatable_content
from services.audit_service import log_action, get_audit_logs, export_audit_csv, log_login_event
from services.glossary import get_glossary_rules

app = Flask(__name__)

# Placeholder stubs for removed services

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def check_and_notify_manager(*args, **kwargs):
    """No-op stub for manager notifications."""
    pass

def send_email(to, subject, html_body):
    """SMTP Email sender with support for both SSL and STARTTLS, falling back to console logging."""
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = os.environ.get('SMTP_PORT')
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    smtp_from = os.environ.get('SMTP_FROM_EMAIL', 'no-reply@doctranslate.com')
    
    def safe_print(text):
        try:
            print(text)
        except UnicodeEncodeError:
            try:
                import sys
                enc = sys.stdout.encoding or 'utf-8'
                print(text.encode(enc, errors='replace').decode(enc))
            except Exception:
                print(text.encode('ascii', errors='replace').decode('ascii'))

    # Check if not configured or using default placeholders
    if not smtp_server or 'company.com' in smtp_user or not smtp_password:
        safe_print("\n=== [SIMULATED EMAIL SENDER] ===")
        safe_print(f"To:      {to}")
        safe_print(f"Subject: {subject}")
        safe_print("Body:")
        safe_print(html_body)
        safe_print("=================================\n")
        return True

    try:
        port = int(smtp_port)
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = to
        
        part = MIMEText(html_body, 'html')
        msg.attach(part)
        
        if port == 465:
            server = smtplib.SMTP_SSL(smtp_server, port, timeout=5)
        else:
            server = smtplib.SMTP(smtp_server, port, timeout=5)
            server.starttls()
            
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [to], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        safe_print(f"Failed to send email to {to} via SMTP: {e}")
        safe_print("\n=== [FALLBACK EMAIL SENDER] ===")
        safe_print(f"To:      {to}")
        safe_print(f"Subject: {subject}")
        safe_print("Body:")
        safe_print(html_body)
        safe_print("=================================\n")
        return False

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
        role VARCHAR(50) DEFAULT 'staff',
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
    
    # 8. Password Resets Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS password_resets(
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        token VARCHAR(255) NOT NULL UNIQUE,
        expires_at DATETIME NOT NULL,
        used BOOLEAN DEFAULT FALSE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    # 9. Translations Archive Table (long-term storage for old records)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS translations_archive(
        id INT AUTO_INCREMENT PRIMARY KEY,
        original_id INT,
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
        engine VARCHAR(50) DEFAULT 'google',
        confidence_score DOUBLE DEFAULT 95.0,
        cost DOUBLE DEFAULT 0.0,
        translated_at DATETIME,
        archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 10. Application Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS app_settings(
        id INT AUTO_INCREMENT PRIMARY KEY,
        setting_key VARCHAR(100) UNIQUE NOT NULL,
        setting_value TEXT NOT NULL,
        description TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)

    conn.commit()

    # Seed default application settings (INSERT IGNORE = safe for re-runs)
    default_settings = [
        ('PDF_MAX_ROWS',         '1000',  'Maximum detail rows included in PDF export'),
        ('PDF_MAX_DAYS',         '90',    'Maximum days window for PDF detail activity table'),
        ('RETENTION_MONTHS',     '12',    'Months of detailed translation history to keep before archiving'),
        ('AUDIT_LOG_LIMIT',      '500',   'Maximum rows returned by the audit log API'),
        ('REPORT_DATE_RANGE_DAYS', '365', 'Maximum allowed date range (days) for analytics reports'),
        ('LOGO_PATH',            'prodapt_logo.png', 'Filename of the corporate branding logo in the static folder'),
    ]
    for key, value, desc in default_settings:
        cursor.execute(
            "INSERT IGNORE INTO app_settings (setting_key, setting_value, description) VALUES (%s, %s, %s)",
            (key, value, desc)
        )
    conn.commit()

    # --- Performance Indexes (safe: check before creating) ---
    index_defs = [
        ('idx_translations_date',   'translations',  'translated_at'),
        ('idx_translations_user',   'translations',  'user_id'),
        ('idx_translations_status', 'translations',  'status'),
        ('idx_audit_logs_timestamp','audit_logs',    'timestamp'),
        ('idx_audit_logs_username', 'audit_logs',    'username'),
    ]
    for idx_name, tbl, col in index_defs:
        cursor.execute("SHOW INDEX FROM `%s` WHERE Key_name = '%s'" % (tbl, idx_name))
        if not cursor.fetchone():
            try:
                cursor.execute("CREATE INDEX `%s` ON `%s`(`%s`)" % (idx_name, tbl, col))
            except Exception:
                pass  # Index may already exist under different name
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

    # Seed Initial Enterprise Users (Guest role removed — minimum role is Staff)
    initial_users = [
        ('AdminUser',   'Admin User',   'admin@company.com',    'admin'),
        ('ManagerUser', 'Manager User', 'manager@company.com',  'manager'),
        ('StaffUser',   'Staff User',   'staff@company.com',    'staff'),
        ('JohnDoe',     'John Doe',     'john.doe@company.com', 'staff'),
        ('SalesAgent',  'Sales Agent',  'sales@company.com',    'staff'),
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

# --- Application Settings Helper ---
def get_setting(key, default=None):
    """Read a value from app_settings table; return default if not found."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = %s", (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row['setting_value']
    except Exception:
        pass
    return default

# --- Role Based Access Control Helper ---
def get_current_user():
    return {
        'username': session.get('username'),
        'role': session.get('role', 'staff')
    }

# --- Batch Translation Background Thread ---
def parse_custom_terms(custom_terms_raw):
    rules = []
    if not custom_terms_raw:
        return rules
    for line in custom_terms_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = None
        for sep in ('->', '=', ':'):
            if sep in line:
                parts = line.split(sep, 1)
                break
        if parts:
            src = parts[0].strip()
            tgt = parts[1].strip()
            if src:
                rules.append({'source_term': src, 'target_term': tgt})
    return rules

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
    
    successful_files_count = 0
    failed_files_count = 0
    empty_files_count = 0
    job_start_time = time.perf_counter()
    
    cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_record = cursor.fetchone()
    user_id = user_record['id'] if user_record else None
    
    for idx, (original_filename, temp_filepath, custom_terms_raw) in enumerate(files_list):
        safe_name = secure_filename(original_filename)
        output_filepath = os.path.join(job_dir, f"translated_{safe_name}")
        
        start_time = time.perf_counter()
        try:
            # Check translatable content
            from services.filehandler import check_translatable_content
            is_valid, err_msg = check_translatable_content(temp_filepath)
            if not is_valid:
                empty_files_count += 1
                cursor.execute(
                    "UPDATE batch_jobs SET processed_files = processed_files + 1 WHERE id = %s",
                    (job_id,)
                )
                conn.commit()
                continue
            
            # Parse and merge glossary rules
            file_custom_rules = parse_custom_terms(custom_terms_raw)
            merged_glossary = file_custom_rules + glossary_rules
            
            # Dispatch to appropriate parser
            p_words, p_masked, p_glossary, p_confidence, p_cost = translate_file(
                temp_filepath, output_filepath, direction, engine, department, merged_glossary, custom_words
            )
            
            translated_paths.append((original_filename, output_filepath))
            total_words += p_words
            total_masked += p_masked
            total_glossary += p_glossary
            total_cost += p_cost
            successful_files_count += 1

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
            failed_files_count += 1
            
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
        if not translated_paths:
            # Write a placeholder file if all files in the batch are skipped/failed
            placeholder_content = "All files in this batch translation job were empty, skipped, or failed to translate."
            placeholder_path = os.path.join(job_dir, "no_files_translated.txt")
            with open(placeholder_path, 'w', encoding='utf-8') as pf:
                pf.write(placeholder_content)
            zipf.write(placeholder_path, "no_files_translated.txt")
        else:
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
    
    # Write summary JSON metrics
    import json
    duration = round(time.perf_counter() - job_start_time, 2)
    summary_data = {
        'total_files': len(files_list),
        'successful_files': successful_files_count,
        'failed_files': failed_files_count,
        'empty_files': empty_files_count,
        'duration_seconds': duration,
        'engine': engine,
        'direction': direction,
        'zip_filename': zip_filename
    }
    summary_path = os.path.join(job_dir, 'summary.json')
    try:
        with open(summary_path, 'w', encoding='utf-8') as sf:
            json.dump(summary_data, sf, indent=4)
    except Exception as e:
        print(f"Error saving summary.json: {e}")
        
    # Get user email
    cursor.execute("SELECT email FROM users WHERE username = %s", (username,))
    user_row = cursor.fetchone()
    user_email = user_row['email'] if user_row else f"{username}@example.com"
    conn.close()
    
    # Dispatch Email notification to employee
    download_url = f"http://127.0.0.1:8082/download-batch/{job_id}"
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
    send_email(user_email, subject, html_body)

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
                "INSERT INTO users (username, full_name, email, password_hash, role, active) VALUES (%s, %s, %s, %s, 'staff', TRUE)",
                (username, full_name, email, password_hash)
            )
            conn.commit()
            user_id = cursor.lastrowid
            log_login_event(
                conn,
                user_id,
                username,
                email,
                'staff',
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
            session['role'] = 'staff'
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

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    error = None
    success = None
    link_to_show = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            error = "Email address is required."
        else:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, username FROM users WHERE email = %s AND active = TRUE", (email,))
            user_row = cursor.fetchone()
            
            # Check if SMTP is configured
            smtp_server = os.environ.get('SMTP_SERVER')
            smtp_user = os.environ.get('SMTP_USER')
            smtp_password = os.environ.get('SMTP_PASSWORD')
            is_simulated = not smtp_server or 'company.com' in smtp_user or not smtp_password
            
            if user_row:
                user_id = user_row['id']
                username = user_row['username']
                # Generate a cryptographically secure token
                token = secrets.token_urlsafe(32)
                expires_at = datetime.utcnow() + timedelta(minutes=15)
                
                # Invalidate any existing unused reset tokens for this user
                cursor.execute("UPDATE password_resets SET used = TRUE WHERE user_id = %s", (user_id,))
                
                # Insert the new reset token
                cursor.execute(
                    "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s, %s, %s)",
                    (user_id, token, expires_at)
                )
                conn.commit()
                
                # Create the reset link
                reset_link = url_for('reset_password', token=token, _external=True)
                
                # Send the email
                subject = "🔒 Reset Your DocTranslate Password"
                html_body = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; border: 1.5px solid #dc2626; border-radius: 12px; padding: 24px; color: #1e293b;">
                    <h2 style="color: #dc2626; margin-top: 0;">🔒 Password Reset Request</h2>
                    <p>Hello <strong>{username}</strong>,</p>
                    <p>We received a request to reset your password for your DocTranslate account. Click the button below to set a new password:</p>
                    
                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{reset_link}" style="background: #dc2626; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block; box-shadow: 0 4px 6px rgba(220,38,38,0.2);">
                            Reset Password
                        </a>
                    </div>
                    
                    <p style="font-size: 13px; color: #475569;">This link will expire in <strong>15 minutes</strong> for security reasons. If you did not request a password reset, you can safely ignore this email.</p>
                    <p style="font-size: 12px; color: #94a3b8; border-top: 1.5px solid #e2e8f0; padding-top: 12px; margin-top: 24px;">If the button above does not work, copy and paste this URL into your browser:<br><a href="{reset_link}">{reset_link}</a></p>
                </div>
                """
                send_email(email, subject, html_body)
                log_action(conn, username, 'request_password_reset', f"Password reset link requested for email: {email}")
                
                if is_simulated:
                    link_to_show = reset_link
                
            else:
                # To prevent username enumeration, we still show a success message even if the user doesn't exist
                time.sleep(0.5) # Slight delay to simulate SMTP latency consistency
                
            success = "If the email is registered and active in our system, a password reset link has been sent."
            conn.close()
            
    return render_template('forgot_password.html', error=error, success=success, link_to_show=link_to_show)

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token') or request.form.get('token')
    if not token:
        return render_template('reset_password.html', error="Invalid or missing password reset token.")
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Validate token
    cursor.execute(
        """
        SELECT r.id, r.user_id, r.expires_at, r.used, u.username 
        FROM password_resets r
        JOIN users u ON r.user_id = u.id
        WHERE r.token = %s AND r.used = FALSE
        """,
        (token,)
    )
    reset_row = cursor.fetchone()
    
    if not reset_row:
        conn.close()
        return render_template('reset_password.html', error="This reset link is invalid or has already been used.")
        
    # Check expiration
    if datetime.utcnow() > reset_row['expires_at']:
        conn.close()
        return render_template('reset_password.html', error="This reset link has expired. Please request a new one.")
        
    error = None
    success = None
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not password or not confirm_password:
            error = "All fields are required."
        elif password != confirm_password:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters long."
        else:
            # Hash and update password
            hashed = generate_password_hash(password)
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed, reset_row['user_id']))
            # Mark token as used
            cursor.execute("UPDATE password_resets SET used = TRUE WHERE id = %s", (reset_row['id'],))
            conn.commit()
            
            log_action(conn, reset_row['username'], 'reset_password_success', "Password successfully reset via token.")
            success = "Your password has been successfully reset. You can now log in with your new password."
            
    conn.close()
    return render_template('reset_password.html', token=token, error=error, success=success)

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
                
                is_valid, err_msg = check_translatable_content(temp_in)
                if not is_valid:
                    error = err_msg
                    try:
                        os.remove(temp_in)
                    except Exception:
                        pass
                
                if not error:
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

    # 4. Gather Dashboard Stats (Usage Analytics)
    # Fetch application settings
    cursor.execute("SELECT setting_key, setting_value FROM app_settings")
    app_settings = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}

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
        account_success=account_success,
        app_settings=app_settings
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
    
    for idx, file in enumerate(files):
        if file and allowed_file(file.filename):
            safe_name = secure_filename(file.filename)
            temp_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_{safe_name}")
            file.save(temp_path)
            
            # Retrieve per-file custom terms
            custom_terms_raw = request.form.get(f'custom_terms_{idx}', '').strip()
            files_list.append((file.filename, temp_path, custom_terms_raw))
            
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
        
    job_dir = os.path.join(BATCH_FOLDER, job_id)
    summary_path = os.path.join(job_dir, 'summary.json')
    if os.path.exists(summary_path):
        import json
        try:
            with open(summary_path, 'r', encoding='utf-8') as sf:
                summary_data = json.load(sf)
            return jsonify({
                'total_files': row['total_files'],
                'processed_files': row['processed_files'],
                'status': row['status'],
                'zip_filename': row['zip_filename'],
                'successful_files': summary_data.get('successful_files', 0),
                'failed_files': summary_data.get('failed_files', 0),
                'empty_files': summary_data.get('empty_files', 0),
                'duration_seconds': summary_data.get('duration_seconds', 0),
                'summary': summary_data
            })
        except Exception as e:
            print(f"Error reading summary.json: {e}")
            
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
    new_role = request.form.get('new_role', 'staff')
    new_password = request.form.get('new_password', 'password123').strip()

    allowed_roles = ('admin', 'manager', 'staff')
    if new_role not in allowed_roles:
        new_role = 'staff'

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
    allowed_roles = ('admin', 'manager', 'staff')
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


@app.route('/manage-users/change-password/<int:user_id>', methods=['POST'])
@admin_required
def manage_users_change_password(user_id):
    """Admin-only: Reset/change any user's password."""
    new_password = request.form.get('new_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    admin_username = session.get('username')

    if not new_password or not confirm_password:
        session['pw_error'] = "Both password fields are required."
        return redirect(url_for('dashboard', _anchor='usersTab'))
    if new_password != confirm_password:
        session['pw_error'] = "Passwords do not match."
        return redirect(url_for('dashboard', _anchor='usersTab'))
    if len(new_password) < 8:
        session['pw_error'] = "Password must be at least 8 characters."
        return redirect(url_for('dashboard', _anchor='usersTab'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    target_user = cursor.fetchone()
    if not target_user:
        conn.close()
        session['pw_error'] = "User not found."
        return redirect(url_for('dashboard', _anchor='usersTab'))

    target_username = target_user['username']
    new_hash = generate_password_hash(new_password)
    cursor.execute(
        "UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (new_hash, user_id)
    )
    log_action(
        conn, admin_username, 'PASSWORD_RESET',
        f"Admin '{admin_username}' reset password for user '{target_username}'."
    )
    conn.commit()
    conn.close()
    session['pw_success'] = f"Password for '{target_username}' updated successfully."
    return redirect(url_for('dashboard', _anchor='usersTab'))


@app.route('/admin/archive-old-translations', methods=['POST'])
@admin_required
def archive_old_translations():
    """Move translations older than RETENTION_MONTHS to translations_archive."""
    retention_months = int(get_setting('RETENTION_MONTHS', '12'))
    conn = get_db_connection()
    cursor = conn.cursor()
    # Copy old records to archive
    cursor.execute("""
        INSERT INTO translations_archive
            (original_id, user_id, username, source_language, target_language,
             filename, file_type, file_size, word_count, status, processing_time,
             engine, confidence_score, cost, translated_at)
        SELECT id, user_id, username, source_language, target_language,
               filename, file_type, file_size, word_count, status, processing_time,
               engine, confidence_score, cost, translated_at
        FROM translations
        WHERE translated_at < DATE_SUB(NOW(), INTERVAL %s MONTH)
    """, (retention_months,))
    moved = cursor.rowcount
    # Delete the originals that were just archived
    cursor.execute(
        "DELETE FROM translations WHERE translated_at < DATE_SUB(NOW(), INTERVAL %s MONTH)",
        (retention_months,)
    )
    log_action(conn, session.get('username'), 'archive_translations',
               f"Archived {moved} translation records older than {retention_months} months.")
    conn.commit()
    conn.close()
    session['archive_msg'] = f"Successfully archived {moved} translation records older than {retention_months} months."
    return redirect(url_for('dashboard', _anchor='reportsTab'))


@app.route('/settings/app', methods=['GET', 'POST'])
@admin_required
def app_settings_view():
    """Get or update configurable application settings."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'POST':
        updatable_keys = ('PDF_MAX_ROWS', 'PDF_MAX_DAYS', 'RETENTION_MONTHS',
                          'AUDIT_LOG_LIMIT', 'REPORT_DATE_RANGE_DAYS')
        for key in updatable_keys:
            val = request.form.get(key, '').strip()
            if val and val.isdigit():
                cursor.execute(
                    "UPDATE app_settings SET setting_value = %s WHERE setting_key = %s",
                    (val, key)
                )
        
        # Handle corporate logo upload (Feature 8)
        if 'logo_file' in request.files:
            logo_file = request.files['logo_file']
            if logo_file and logo_file.filename != '':
                ext = os.path.splitext(logo_file.filename)[1].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.gif'):
                    logo_file.seek(0, os.SEEK_END)
                    file_size = logo_file.tell()
                    logo_file.seek(0)
                    if file_size <= 2 * 1024 * 1024:
                        filename = f"logo_{int(time.time())}{ext}"
                        logo_path = os.path.join(app.root_path, 'static', filename)
                        logo_file.save(logo_path)
                        cursor.execute(
                            "INSERT INTO app_settings (setting_key, setting_value, description) "
                            "VALUES ('LOGO_PATH', %s, 'Filename of the corporate branding logo in the static folder') "
                            "ON DUPLICATE KEY UPDATE setting_value = %s",
                            (filename, filename)
                        )
                        log_action(conn, session.get('username'), 'upload_logo', f"Uploaded new corporate branding logo '{filename}'.")

        log_action(conn, session.get('username'), 'update_settings', 'Updated application configuration settings.')
        conn.commit()
        conn.close()
        session['settings_success'] = 'Configuration settings updated successfully.'
        return redirect(url_for('dashboard', _anchor='accountTab'))

    cursor.execute("SELECT setting_key, setting_value, description FROM app_settings ORDER BY id")
    settings = cursor.fetchall()
    conn.close()
    return jsonify({s['setting_key']: {'value': s['setting_value'], 'description': s['description']} for s in settings})


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

    # --- Safety limits for details table ---
    pdf_max_rows = int(get_setting('PDF_MAX_ROWS', '1000'))
    pdf_max_days = int(get_setting('PDF_MAX_DAYS', '90'))

    filters = []
    params = []
    try:
        if from_date_raw:
            filters.append('translated_at >= %s')
            params.append(datetime.strptime(from_date_raw, '%Y-%m-%d').strftime('%Y-%m-%d 00:00:00'))
        if to_date_raw:
            filters.append('translated_at <= %s')
            params.append(datetime.strptime(to_date_raw, '%Y-%m-%d').strftime('%Y-%m-%d 23:59:59'))
    except ValueError:
        return "Invalid date range format. Use YYYY-MM-DD.", 400

    where_clause = (' WHERE ' + ' AND '.join(filters)) if filters else ''

    conn = get_db_connection()
    cursor = conn.cursor()

    # Full-dataset summary metrics
    cursor.execute(
        f"SELECT COUNT(*) as total_translations, COALESCE(SUM(word_count),0) as total_words, "
        f"COALESCE(AVG(file_size),0) as avg_file_size FROM translations{where_clause}", params)
    stats = cursor.fetchone()

    # Files translated
    cursor.execute(
        f"SELECT COUNT(*) as files_translated FROM translations "
        f"{where_clause if where_clause else ' WHERE '} "
        f"{' AND ' if where_clause else ''} filename IS NOT NULL AND filename != 'Text Box Input' AND filename != 'Text Input'", params)
    files_translated = cursor.fetchone()["files_translated"]

    # Most common translation direction
    cursor.execute(
        f"SELECT CONCAT(source_language,' -> ',target_language) as direction, COUNT(*) as cnt "
        f"FROM translations{where_clause} GROUP BY source_language, target_language "
        f"ORDER BY cnt DESC LIMIT 1", params)
    common_dir_row = cursor.fetchone()
    common_direction = common_dir_row['direction'] if common_dir_row else 'N/A'

    # Detailed activity query with safety limits
    details_filters = list(filters)
    details_params = list(params)
    details_filters.append("translated_at >= DATE_SUB(NOW(), INTERVAL %s DAY)")
    details_params.append(pdf_max_days)

    details_where = ' WHERE ' + ' AND '.join(details_filters)
    details_query = (
        f"SELECT DATE_FORMAT(translated_at, '%%Y-%%m-%%d %%H:%%i') as date_str, "
        f"username, source_language, target_language, filename, word_count, status "
        f"FROM translations{details_where} ORDER BY translated_at DESC LIMIT %s"
    )
    details_params.append(pdf_max_rows)
    cursor.execute(details_query, tuple(details_params))
    rows = cursor.fetchall()
    conn.close()

    period_str = f"{from_date_raw or 'Start'} to {to_date_raw or 'End'}"
    if not from_date_raw and not to_date_raw:
        period_str = "All Time"
    now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    username = session.get('username', 'System')

    if output_format == 'xlsx':
        # Excel branded sheet (non-zipped)
        from openpyxl import Workbook
        wb = Workbook()
        summary = wb.active
        summary.title = 'Analytics Summary'
        
        summary.append(['PRODAPT'])
        summary.append(['Translation Analytics Report'])
        summary.append(['Generated On', now_str])
        summary.append(['Generated By', username])
        summary.append(['Period', period_str])
        summary.append([])
        
        summary.append(['SUMMARY METRICS'])
        summary.append(['Total Translations', stats['total_translations']])
        summary.append(['Total Words Processed', stats['total_words']])
        summary.append(['Files Translated', files_translated])
        summary.append(['Average File Size (KB)', round(float(stats['avg_file_size'] or 0) / 1024, 2)])
        summary.append(['Most Common Direction', common_direction])
        summary.append([])
        
        summary.append(['DETAILED TRANSLATION ACTIVITY'])
        summary.append(['Date', 'User', 'Source Lang', 'Target Lang', 'Filename', 'Word Count', 'Status'])
        for r in rows:
            summary.append([r['date_str'], r['username'], r['source_language'], r['target_language'], r['filename'], r['word_count'], r['status']])
            
        mem_file = io.BytesIO()
        wb.save(mem_file)
        mem_file.seek(0)
        return send_file(mem_file, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}.xlsx")

    # Generate text report for CSV or TXT
    if output_format == 'csv':
        output = io.StringIO()
        import csv as csv_module
        writer = csv_module.writer(output)
        writer.writerow(['PRODAPT'])
        writer.writerow(['Translation Analytics Report'])
        writer.writerow(['Generated On', now_str])
        writer.writerow(['Generated By', username])
        writer.writerow(['Period', period_str])
        writer.writerow([])
        writer.writerow(['SUMMARY METRICS'])
        writer.writerow(['Total Translations', stats['total_translations']])
        writer.writerow(['Total Words Processed', stats['total_words']])
        writer.writerow(['Files Translated', files_translated])
        writer.writerow(['Average File Size (KB)', round(float(stats['avg_file_size'] or 0) / 1024, 2)])
        writer.writerow(['Most Common Direction', common_direction])
        writer.writerow([])
        writer.writerow(['DETAILED TRANSLATION ACTIVITY'])
        writer.writerow(['Date', 'User', 'Source Lang', 'Target Lang', 'Filename', 'Word Count', 'Status'])
        for r in rows:
            writer.writerow([r['date_str'], r['username'], r['source_language'], r['target_language'], r['filename'], r['word_count'], r['status']])
        report_content = output.getvalue().encode('utf-8-sig')
        report_filename = f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    else:
        # Default to TXT format
        output = io.StringIO()
        output.write("==================================================\n")
        output.write("PRODAPT\n")
        output.write("Translation Analytics Report\n")
        output.write(f"Generated On: {now_str}\n")
        output.write(f"Generated By: {username}\n")
        output.write(f"Period: {period_str}\n")
        output.write("==================================================\n\n")
        output.write("SUMMARY METRICS:\n")
        output.write("--------------------------------------------------\n")
        output.write(f"Total Translations: {stats['total_translations']:,}\n")
        output.write(f"Total Words Processed: {stats['total_words']:,}\n")
        output.write(f"Files Translated: {files_translated:,}\n")
        output.write(f"Average File Size: {round(float(stats['avg_file_size'] or 0) / 1024, 2):,} KB\n")
        output.write(f"Most Common Direction: {common_direction}\n\n")
        output.write("DETAILED TRANSLATION ACTIVITY:\n")
        output.write("--------------------------------------------------\n")
        output.write("Date | User | Source | Target | File Name | Word Count | Status\n")
        for r in rows:
            output.write(f"{r['date_str']} | {r['username']} | {r['source_language']} | {r['target_language']} | {r['filename']} | {r['word_count']:,} | {r['status']}\n")
        report_content = output.getvalue().encode('utf-8')
        report_filename = f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}.txt"

    # Fetch branding logo
    logo_filename = get_setting('LOGO_PATH', 'prodapt_logo.png')
    logo_path = os.path.join(app.root_path, 'static', logo_filename)

    # Package as ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(report_filename, report_content)
        if os.path.exists(logo_path):
            try:
                zip_file.write(logo_path, logo_filename)
            except Exception:
                pass
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name=f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}_{output_format}.zip")

@app.route('/analytics/export/pdf', methods=['GET'])
@login_required
def export_analytics_pdf():
    if session.get('role') not in ('admin', 'manager'):
        return "Unauthorized", 403

    from_date_raw = request.args.get('from_date', '')
    to_date_raw   = request.args.get('to_date', '')
    filters = []
    params  = []

    try:
        if from_date_raw:
            filters.append('translated_at >= %s')
            params.append(datetime.strptime(from_date_raw, '%Y-%m-%d').strftime('%Y-%m-%d 00:00:00'))
        if to_date_raw:
            filters.append('translated_at <= %s')
            params.append(datetime.strptime(to_date_raw, '%Y-%m-%d').strftime('%Y-%m-%d 23:59:59'))
    except ValueError:
        return "Invalid date filter format. Use YYYY-MM-DD.", 400

    where_clause = (' WHERE ' + ' AND '.join(filters)) if filters else ''

    conn   = get_db_connection()
    cursor = conn.cursor()

    # Full-dataset summary stats (no row limit)
    cursor.execute(
        f"SELECT COUNT(*) as total_translations, COALESCE(SUM(word_count),0) as total_words, "
        f"COALESCE(AVG(file_size),0) as avg_file_size FROM translations{where_clause}", params)
    stats = cursor.fetchone()

    # Files translated
    cursor.execute(
        f"SELECT COUNT(*) as files_translated FROM translations "
        f"{where_clause if where_clause else ' WHERE '} "
        f"{' AND ' if where_clause else ''} filename IS NOT NULL AND filename != 'Text Box Input' AND filename != 'Text Input'", params)
    files_translated = cursor.fetchone()["files_translated"]

    # Most common direction
    cursor.execute(
        f"SELECT CONCAT(source_language,' -> ',target_language) as direction, COUNT(*) as cnt "
        f"FROM translations{where_clause} GROUP BY source_language, target_language "
        f"ORDER BY cnt DESC LIMIT 1", params)
    common_dir_row = cursor.fetchone()
    common_direction = common_dir_row['direction'] if common_dir_row else 'N/A'

    # 7-day activity trend
    cursor.execute(
        f"SELECT DATE_FORMAT(translated_at,'%%Y-%%m-%%d') as day, COUNT(*) as count "
        f"FROM translations{where_clause} GROUP BY day ORDER BY day DESC LIMIT 7", params)
    daily_stats = cursor.fetchall()

    # Read safe limits
    pdf_max_rows = int(get_setting('PDF_MAX_ROWS', '1000'))
    pdf_max_days = int(get_setting('PDF_MAX_DAYS', '90'))

    # Fetch rows with safe limits
    details_filters = list(filters)
    details_params = list(params)
    details_filters.append("translated_at >= DATE_SUB(NOW(), INTERVAL %s DAY)")
    details_params.append(pdf_max_days)

    details_where = ' WHERE ' + ' AND '.join(details_filters)
    details_query = (
        f"SELECT DATE_FORMAT(translated_at, '%%Y-%%m-%%d %%H:%%i') as date_str, "
        f"username, source_language, target_language, filename, word_count, status "
        f"FROM translations{details_where} ORDER BY translated_at DESC LIMIT %s"
    )
    details_params.append(pdf_max_rows)
    cursor.execute(details_query, tuple(details_params))
    rows = cursor.fetchall()
    conn.close()

    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor('#0f172a'),
        alignment=1,
        spaceAfter=12
    )
    meta_style = ParagraphStyle(
        'DocMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        textColor=colors.HexColor('#475569'),
        alignment=1,
        spaceAfter=6
    )
    section_style = ParagraphStyle(
        'DocSection',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=12,
        textColor=colors.HexColor('#0f172a'),
        spaceBefore=12,
        spaceAfter=6
    )
    footnote_style = ParagraphStyle(
        'DocFootnote',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8,
        textColor=colors.HexColor('#64748b'),
        spaceBefore=10
    )

    story = []

    # Center horizontal branding logo
    logo_filename = get_setting('LOGO_PATH', 'prodapt_logo.png')
    logo_path = os.path.join(app.root_path, 'static', logo_filename)
    if os.path.exists(logo_path):
        try:
            logo_img = Image(logo_path, width=150, height=50)
            logo_img.hAlign = 'CENTER'
            story.append(logo_img)
        except Exception:
            logo_text = ParagraphStyle('LogoText', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=26, textColor=colors.HexColor('#DC2626'), alignment=1)
            story.append(Paragraph("PRODAPT", logo_text))
    else:
        logo_text = ParagraphStyle('LogoText', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=26, textColor=colors.HexColor('#DC2626'), alignment=1)
        story.append(Paragraph("PRODAPT", logo_text))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Translation Analytics Report", title_style))

    now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    period_str = f"Period: {from_date_raw or 'Start'} to {to_date_raw or 'End'}"
    if not from_date_raw and not to_date_raw:
        period_str = "Period: All Time"
    generated_by = f"Generated By: {session.get('username', 'System')}"

    story.append(Paragraph(f"Generated On: {now_str}", meta_style))
    story.append(Paragraph(period_str, meta_style))
    story.append(Paragraph(generated_by, meta_style))
    story.append(Spacer(1, 15))

    # Summary table
    metrics_data = [
        [Paragraph("<b>Metric</b>", styles['Normal']), Paragraph("<b>Value</b>", styles['Normal'])],
        ["Total Translations", f"{stats['total_translations']:,}"],
        ["Total Words Processed", f"{stats['total_words']:,}"],
        ["Files Translated", f"{files_translated:,}"],
        ["Average File Size", f"{round(float(stats['avg_file_size'] or 0) / 1024, 2):,} KB"],
        ["Most Common Direction", common_direction]
    ]
    metrics_table = Table(metrics_data, colWidths=[200, 200])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')])
    ]))
    story.append(Paragraph("Summary Metrics", section_style))
    story.append(metrics_table)
    story.append(Spacer(1, 15))

    # Activity trends table
    trend_data = [[Paragraph("<b>Date</b>", styles['Normal']), Paragraph("<b>Translations Count</b>", styles['Normal'])]]
    for d in daily_stats:
        trend_data.append([d['day'], str(d['count'])])
    trend_table = Table(trend_data, colWidths=[200, 200])
    trend_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')])
    ]))
    story.append(Paragraph("Translation Activity Trends (Last 7 Days)", section_style))
    story.append(trend_table)
    story.append(Spacer(1, 15))

    # Detailed table
    detail_data = [[
        "Date",
        "User",
        "Source",
        "Target",
        "File Name",
        "Words",
        "Status"
    ]]
    for r in rows:
        detail_data.append([
            r['date_str'],
            r['username'],
            r['source_language'],
            r['target_language'],
            str(r['filename'])[:24],
            f"{r['word_count']:,}",
            r['status']
        ])
    detail_table = Table(detail_data, colWidths=[85, 55, 45, 45, 150, 50, 60])
    detail_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('BOTTOMPADDING', (0,0), (-1,0), 5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')])
    ]))
    story.append(Paragraph("Recent Translation Activity", section_style))
    story.append(detail_table)
    story.append(Paragraph(f"* Note: Detailed activity rows are capped to a maximum of last {pdf_max_rows} records or {pdf_max_days} days (whichever is smaller) for performance safety.", footnote_style))

    def draw_page_decorations(canvas_obj, doc):
        canvas_obj.saveState()
        canvas_obj.setFont('Helvetica', 8)
        canvas_obj.setFillColor(colors.HexColor('#64748b'))
        canvas_obj.drawString(36, 20, "DocTranslate Enterprise - Confidential")
        canvas_obj.drawRightString(612 - 36, 20, f"Page {doc.page}")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=draw_page_decorations, onLaterPages=draw_page_decorations)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f"DocTranslate_Report_{datetime.utcnow().strftime('%Y%m%d')}.pdf")

if __name__ == "__main__":
    # Run on port 8082 which is free from system/reserved conflicts
    app.run(debug=False, host='127.0.0.1', port=8082, use_reloader=False)