# services/audit_service.py
"""Audit service utilities.
Provides real database audit logging, filtering, and export.
"""

import csv
import io

def log_action(conn, username, action, details):
    """Write an audit entry to the audit_logs table."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (username, action, details) VALUES (%s, %s, %s)",
            (username, action, details)
        )
        conn.commit()
    except Exception as e:
        print(f"Audit log failed: {e}")

def get_audit_logs(conn, search=None, user_filter=None, action_filter=None, limit=100):
    """Query audit logs from the database with search and filters."""
    query = "SELECT username, action, details, timestamp FROM audit_logs WHERE 1=1"
    params = []
    
    if search:
        query += " AND (username LIKE %s OR action LIKE %s OR details LIKE %s)"
        term = f"%{search}%"
        params.extend([term, term, term])
        
    if user_filter:
        query += " AND username = %s"
        params.append(user_filter)
        
    if action_filter:
        query += " AND action = %s"
        params.append(action_filter)
        
    query += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    
    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()

def export_audit_csv(conn, search=None, user_filter=None, action_filter=None):
    """Export filtered audit logs as a CSV string."""
    logs = get_audit_logs(conn, search, user_filter, action_filter, limit=1000)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Username', 'Action', 'Details'])
    for log in logs:
        writer.writerow([log['timestamp'], log['username'], log['action'], log['details']])
        
    return output.getvalue()


def log_login_event(conn, user_id, username, email, role, ip_address, login_time, logout_time, status, details=None):
    """Write a login audit entry to the login_audit table."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO login_audit
            (user_id, username, email, role, ip_address, login_time, logout_time, status, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, username, email, role, ip_address, login_time, logout_time, status, details)
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"Login audit log failed: {e}")
        return None


def get_login_audit_logs(conn, user_filter=None, status_filter=None, limit=1000):
    query = "SELECT id, user_id, username, email, role, ip_address, login_time, logout_time, status, details, created_at FROM login_audit WHERE 1=1"
    params = []
    if user_filter:
        query += " AND username = %s"
        params.append(user_filter)
    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)
    query += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()
