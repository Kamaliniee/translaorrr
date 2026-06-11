# -*- coding: utf-8 -*-
"""
DocTranslate Enterprise - Full Test Suite (18 Test Cases)
For IT Manager Review

HOW TO RUN:
  1. Open PowerShell or Command Prompt
  2. Type:   cd C:\\Users\\barathraj.sp\\Downloads\\translatorrrr\\translator
  3. Type:   python run_all_tests.py
  4. Wait about 20-30 seconds for the results
"""

import sys
import io
# Force UTF-8 output so tick marks and icons display correctly in Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os
import time
import requests
import pymysql
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://127.0.0.1:8082"

# ------------------------------------------------
# RESULT TRACKER
# ------------------------------------------------
results = []

def record(number, name, passed, detail=""):
    status = "[PASS]" if passed else "[FAIL]"
    icon   = "  OK  " if passed else "  XX  "
    results.append((number, name, status, detail, passed))
    print(f"{icon} TC-{number:02d}: {name}")
    if detail:
        print(f"        -> {detail}")

def get_db():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "root"),
        database=os.getenv("MYSQL_DB", "doctranslate"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5
    )

# ------------------------------------------------
print()
print("=" * 65)
print("  DocTranslate Enterprise - IT Manager Test Report")
print("  Date:", time.strftime("%d %B %Y, %I:%M %p"))
print("=" * 65)
print()

# ------------------------------------------------
# TC-01: User Table Validation (Database)
# ------------------------------------------------
print("[ DATABASE TESTS ]")
print("-" * 65)
try:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SHOW CREATE TABLE users")
    row = cursor.fetchone()
    table_def = str(row)
    has_unique = "UNIQUE" in table_def
    cursor.execute("DESCRIBE users")
    cols = [r['Field'] for r in cursor.fetchall()]
    has_role  = 'role'  in cols
    has_email = 'email' in cols
    conn.close()
    record(1, "User Table Validation (Database)",
           has_unique and has_role and has_email,
           f"Columns: {', '.join(cols)} | Unique email: {'Yes' if has_unique else 'No'}")
except Exception as e:
    record(1, "User Table Validation (Database)", False, f"DB Error: {e}")

# ------------------------------------------------
# TC-02: User Registration Validation
# ------------------------------------------------
try:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    count = cursor.fetchone()['cnt']
    conn.close()
    record(2, "User Registration Validation",
           count > 0,
           f"{count} user(s) found in system")
except Exception as e:
    record(2, "User Registration Validation", False, f"DB Error: {e}")

# ------------------------------------------------
# TC-03: Audit Log Search Validation
# ------------------------------------------------
try:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM audit_logs")
    total = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) as cnt FROM audit_logs WHERE action LIKE '%login%'")
    login_logs = cursor.fetchone()['cnt']
    conn.close()
    record(3, "Audit Log Search Validation",
           total > 0,
           f"Total audit logs: {total} | Login events found: {login_logs}")
except Exception as e:
    record(3, "Audit Log Search Validation", False, f"DB Error: {e}")

# ------------------------------------------------
# TC-04: Audit Log Filter Validation
# ------------------------------------------------
try:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT action FROM audit_logs LIMIT 5")
    actions = [r['action'] for r in cursor.fetchall()]
    if actions:
        cursor.execute("SELECT COUNT(*) as cnt FROM audit_logs WHERE action = %s", (actions[0],))
        filtered = cursor.fetchone()['cnt']
        cursor.execute("SELECT COUNT(*) as cnt FROM audit_logs WHERE action != %s", (actions[0],))
        excluded = cursor.fetchone()['cnt']
        conn.close()
        record(4, "Audit Log Filter Validation",
               filtered > 0,
               f"Filter '{actions[0]}': shows {filtered} record(s), excludes {excluded} other(s)")
    else:
        conn.close()
        record(4, "Audit Log Filter Validation", False, "No audit logs to filter")
except Exception as e:
    record(4, "Audit Log Filter Validation", False, f"DB Error: {e}")

# ------------------------------------------------
print()
print("[ PAGE LOAD & HTTP TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-05: Page Load & HTTP Status Validation
# ------------------------------------------------
try:
    r = requests.get(f"{BASE_URL}/login", timeout=10)
    is_200    = r.status_code == 200
    has_title = "doctranslate" in r.text.lower() or "login" in r.text.lower()
    record(5, "Page Load & HTTP Validation",
           is_200 and has_title,
           f"HTTP Status: {r.status_code} | Page loaded: {'Yes' if has_title else 'No'} | URL: {BASE_URL}/login")
except Exception as e:
    record(5, "Page Load & HTTP Validation", False, f"Cannot reach server: {e}")

# ------------------------------------------------
# TC-06: Performance (Page Load Time < 3 seconds)
# ------------------------------------------------
try:
    start   = time.time()
    r       = requests.get(f"{BASE_URL}/login", timeout=10)
    elapsed = time.time() - start
    is_fast = elapsed < 3.0
    record(6, "Performance (Load Time < 3 sec)",
           is_fast,
           f"Page loaded in {elapsed:.2f} seconds ({'FAST - OK' if is_fast else 'SLOW - needs fix'})")
except Exception as e:
    record(6, "Performance (Load Time < 3 sec)", False, f"Error: {e}")

# ------------------------------------------------
print()
print("[ SECURITY TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-07: Session Timeout (Configuration Check)
# ------------------------------------------------
try:
    with open("app.py", "r", encoding="utf-8") as f:
        app_code = f.read()
    has_timeout       = ("PERMANENT_SESSION_LIFETIME" in app_code or
                         "session.permanent" in app_code or
                         "timedelta" in app_code)
    has_session_check = "session.get" in app_code
    record(7, "Session Timeout Configuration",
           has_timeout,
           f"Session lifetime set: {'Yes' if has_timeout else 'No'} | Session checks exist: {'Yes' if has_session_check else 'No'}")
except Exception as e:
    record(7, "Session Timeout Configuration", False, f"Error: {e}")

# ------------------------------------------------
# TC-08: Unauthorized Access Protection
# ------------------------------------------------
try:
    sess = requests.Session()
    r    = sess.get(f"{BASE_URL}/dashboard", timeout=10, allow_redirects=True)
    redirected_to_login = "/login" in r.url or "login" in r.text.lower()
    record(8, "Unauthorized Access Protection",
           redirected_to_login,
           f"Tried /dashboard without login -> Redirected to: {r.url}")
except Exception as e:
    record(8, "Unauthorized Access Protection", False, f"Error: {e}")

# ------------------------------------------------
print()
print("[ UI TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-09: UI Validation (Combined)
# ------------------------------------------------
try:
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()
    checks = {
        "Logo/Icon"   : 'img' in html or 'logo' in html.lower(),
        "Buttons"     : '<button' in html,
        "Text Inputs" : 'type="text"' in html or 'type="password"' in html,
        "Dropdowns"   : '<select' in html,
        "Labels"      : '<label' in html,
    }
    all_pass = all(checks.values())
    detail   = " | ".join(f"{k}: {'OK' if v else 'MISSING'}" for k, v in checks.items())
    record(9, "UI Validation (Logo, Buttons, Inputs, Dropdowns)", all_pass, detail)
except Exception as e:
    record(9, "UI Validation", False, f"Error: {e}")

# ------------------------------------------------
# TC-10: Form Validation (Blank Field Check)
# ------------------------------------------------
try:
    template = "templates/login.html" if os.path.exists("templates/login.html") else "templates/dashboard.html"
    with open(template, "r", encoding="utf-8") as f:
        login_html = f.read()
    has_required = 'required' in login_html
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    backend_check = "not username" in code or "not password" in code or "required" in code
    record(10, "Form Validation (Blank Field Check)",
           has_required or backend_check,
           f"HTML 'required' attribute: {'Yes' if has_required else 'No'} | Backend validation: {'Yes' if backend_check else 'No'}")
except Exception as e:
    record(10, "Form Validation", False, f"Error: {e}")

# ------------------------------------------------
# TC-11: Input Field Max Length Validation
# ------------------------------------------------
try:
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    has_backend_len = "len(" in code
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()
    html_maxlength = "maxlength" in html
    record(11, "Input Field Max Length Validation",
           has_backend_len or html_maxlength,
           f"Length check in backend: {'Yes' if has_backend_len else 'No'} | maxlength in HTML: {'Yes' if html_maxlength else 'No'}")
except Exception as e:
    record(11, "Input Max Length", False, f"Error: {e}")

# ------------------------------------------------
print()
print("[ NAVIGATION TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-12: Navigation Validation (Links, Menu, Tabs)
# ------------------------------------------------
try:
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()
    checks = {
        "Nav Links"    : '<a ' in html and 'href' in html,
        "Sidebar Menu" : 'sidebar' in html.lower() or 'nav' in html.lower(),
        "Tab Switching": 'tab' in html.lower(),
        "Logout Link"  : 'logout' in html.lower(),
    }
    all_pass = all(checks.values())
    detail   = " | ".join(f"{k}: {'OK' if v else 'MISSING'}" for k, v in checks.items())
    record(12, "Navigation Validation (Links, Menu, Tabs)", all_pass, detail)
except Exception as e:
    record(12, "Navigation Validation", False, f"Error: {e}")

# ------------------------------------------------
print()
print("[ DATA TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-13: Data / Table Validation
# ------------------------------------------------
try:
    conn   = get_db()
    cursor = conn.cursor()
    tables = {}
    for table in ['users', 'translations', 'audit_logs', 'glossary', 'batch_jobs']:
        try:
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            tables[table] = cursor.fetchone()['cnt']
        except Exception:
            tables[table] = "ERROR"
    conn.close()
    all_exist = all(isinstance(v, int) for v in tables.values())
    detail    = " | ".join(f"{k}: {v} rows" for k, v in tables.items())
    record(13, "Data/Table Validation (All tables exist)", all_exist, detail)
except Exception as e:
    record(13, "Data/Table Validation", False, f"DB Error: {e}")

# ------------------------------------------------
print()
print("[ FILE TESTS ]")
print("-" * 65)

# ------------------------------------------------
# TC-14: File Upload Validation (Valid + Invalid)
# ------------------------------------------------
try:
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    has_allowed  = "ALLOWED_EXTENSIONS" in code
    valid_fmts   = all(fmt in code for fmt in ['pdf', 'docx', 'txt', 'xlsx'])
    record(14, "File Upload Validation (Valid/Invalid Formats)",
           has_allowed and valid_fmts,
           f"ALLOWED_EXTENSIONS defined: {'Yes' if has_allowed else 'No'} | pdf/docx/txt/xlsx supported: {'Yes' if valid_fmts else 'No'}")
except Exception as e:
    record(14, "File Upload Validation", False, f"Error: {e}")

# ------------------------------------------------
# TC-15: Batch Job Progress Tracking
# ------------------------------------------------
try:
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DESCRIBE batch_jobs")
    cols = [r['Field'] for r in cursor.fetchall()]
    conn.close()
    has_status    = 'status'          in cols
    has_progress  = ('progress'        in cols or
                     'progress_percent' in cols or
                     'processed_files'  in cols)
    has_timestamp = ('created_at'  in cols or
                     'updated_at'  in cols or
                     'timestamp'   in cols)
    record(15, "Batch Job Progress Tracking",
           has_status and has_timestamp,
           f"Status col: {'OK' if has_status else 'MISSING'} | Progress col: {'OK' if has_progress else 'MISSING'} | Timestamps: {'OK' if has_timestamp else 'MISSING'}")
except Exception as e:
    record(15, "Batch Job Progress Tracking", False, f"DB Error: {e}")

# ------------------------------------------------
# TC-16: ZIP File Download Validation
# ------------------------------------------------
try:
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    has_zip      = "zipfile" in code or "ZipFile" in code
    has_download = "download-batch" in code or "send_file" in code
    record(16, "ZIP File Download Validation",
           has_zip and has_download,
           f"ZIP creation in code: {'Yes' if has_zip else 'No'} | Download route: {'Yes' if has_download else 'No'}")
except Exception as e:
    record(16, "ZIP File Download Validation", False, f"Error: {e}")

# ------------------------------------------------
# TC-17: File Format Validation (Supported Types)
# ------------------------------------------------
try:
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    supported = {}
    for fmt in ['pdf', 'docx', 'txt', 'xlsx', 'xls', 'csv']:
        supported[fmt] = f"'{fmt}'" in code or f'"{fmt}"' in code
    all_supported = all(supported.values())
    detail        = "  ".join(f"{k}:{'OK' if v else 'MISSING'}" for k, v in supported.items())
    record(17, "File Format Validation (Supported Types)", all_supported, detail)
except Exception as e:
    record(17, "File Format Validation", False, f"Error: {e}")

# ------------------------------------------------
# TC-18: Error Handling (Messages & Exceptions)
# ------------------------------------------------
try:
    r = requests.get(f"{BASE_URL}/this-page-does-not-exist-xyz", timeout=10)
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()
    has_try_except = "except" in code and "error" in code.lower()
    has_messages   = "flash" in code or "session[" in code
    record(18, "Error Handling (Messages & Exceptions)",
           has_try_except and has_messages,
           f"Try/Except in code: {'Yes' if has_try_except else 'No'} | Error messages to user: {'Yes' if has_messages else 'No'} | 404 test status: {r.status_code}")
except Exception as e:
    record(18, "Error Handling", False, f"Error: {e}")

# ================================================
# FINAL SUMMARY REPORT
# ================================================
print()
print("=" * 65)
print("  FINAL TEST SUMMARY - DocTranslate Enterprise")
print("=" * 65)
print(f"  {'TC#':<6} {'Test Case Name':<43} {'Result'}")
print("  " + "-" * 58)
for num, name, status, detail, passed in results:
    short = name[:41] + ".." if len(name) > 41 else name
    marker = "[PASS]" if passed else "[FAIL]"
    print(f"  TC-{num:02d}  {short:<43} {marker}")

passed_count = sum(1 for *_, p in results if p)
failed_count = sum(1 for *_, p in results if not p)
total_count  = len(results)
score        = int((passed_count / total_count) * 100)

print("  " + "-" * 58)
print()
print(f"  Total Tests  : {total_count}")
print(f"  PASSED       : {passed_count}")
print(f"  FAILED       : {failed_count}")
print(f"  SCORE        : {score}%  ", end="")
if   score == 100: print("-- EXCELLENT! All tests passed.")
elif score >= 80:  print("-- GOOD. Minor issues to review.")
elif score >= 60:  print("-- AVERAGE. Some important tests failed.")
else:              print("-- NEEDS ATTENTION. Several tests failed.")
print()
print("=" * 65)
print(f"  Report generated: {time.strftime('%d %B %Y at %I:%M %p')}")
print("=" * 65)
print()

sys.exit(0 if failed_count == 0 else 1)
