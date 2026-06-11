-- ================================================================
-- DocTranslate Enterprise - MySQL Demo Commands
-- Open MySQL Command Line Client and run these queries to
-- demonstrate that all data is being stored persistently.
-- ================================================================

-- STEP 1: Connect to the database
USE doctranslate;

-- ================================================================
-- DEMO 1: VIEW ALL REGISTERED USERS
-- Shows every account that has been created (via Register form
-- or Admin panel). Passwords are stored as bcrypt hashes.
-- ================================================================
SELECT
    id,
    username,
    full_name,
    email,
    role,
    active,
    created_at
FROM users
ORDER BY created_at DESC;

-- ================================================================
-- DEMO 2: VIEW LOGIN AUDIT RECORDS
-- Every login attempt is recorded here including:
-- - Who logged in
-- - When they logged in and logged out
-- - Their IP address
-- - Whether the login succeeded or failed
-- ================================================================
SELECT
    id,
    username,
    email,
    role,
    ip_address,
    login_time,
    logout_time,
    status,
    details
FROM login_audit
ORDER BY login_time DESC
LIMIT 50;

-- ================================================================
-- DEMO 3: VIEW TRANSLATION HISTORY
-- Every document and text translation is stored here permanently.
-- This data SURVIVES server restarts.
-- ================================================================
SELECT
    id,
    username,
    source_language,
    target_language,
    filename,
    file_type,
    ROUND(file_size / 1024, 2) AS file_size_kb,
    word_count,
    status,
    ROUND(processing_time, 3) AS processing_secs,
    engine,
    translated_at
FROM translations
ORDER BY translated_at DESC
LIMIT 50;

-- ================================================================
-- DEMO 4: VIEW ANALYTICS SUMMARY (Daily Aggregates)
-- This table stores daily translation totals that are updated
-- each time a translation occurs.
-- ================================================================
SELECT
    id,
    report_date,
    total_translations,
    total_words,
    files_processed,
    updated_at
FROM analytics_summary
ORDER BY report_date DESC;

-- ================================================================
-- DEMO 5: OVERALL STATISTICS
-- ================================================================
SELECT
    COUNT(*) AS total_translations,
    SUM(word_count) AS total_words_processed,
    COUNT(DISTINCT username) AS unique_users,
    COUNT(DISTINCT CONCAT(source_language, '-', target_language)) AS language_pairs,
    MIN(translated_at) AS first_translation,
    MAX(translated_at) AS latest_translation
FROM translations;

-- ================================================================
-- DEMO 6: LANGUAGE PAIR BREAKDOWN
-- ================================================================
SELECT
    CONCAT(source_language, ' -> ', target_language) AS language_pair,
    COUNT(*) AS translation_count,
    SUM(word_count) AS total_words
FROM translations
GROUP BY source_language, target_language
ORDER BY translation_count DESC;

-- ================================================================
-- DEMO 7: USER ACTIVITY REPORT
-- ================================================================
SELECT
    u.username,
    u.email,
    u.role,
    COUNT(t.id) AS translations_done,
    COALESCE(SUM(t.word_count), 0) AS total_words,
    u.created_at
FROM users u
LEFT JOIN translations t ON u.username = t.username
GROUP BY u.id, u.username, u.email, u.role, u.created_at
ORDER BY translations_done DESC;

-- ================================================================
-- DEMO 8: RECENT FAILED LOGINS (Security Audit)
-- ================================================================
SELECT
    username,
    email,
    ip_address,
    login_time,
    status,
    details
FROM login_audit
WHERE status = 'failed'
ORDER BY login_time DESC
LIMIT 20;

-- ================================================================
-- DEMO 9: DAILY TRANSLATION TREND (Last 30 days)
-- ================================================================
SELECT
    DATE(translated_at) AS translation_date,
    COUNT(*) AS translations,
    SUM(word_count) AS words_processed
FROM translations
WHERE translated_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY DATE(translated_at)
ORDER BY translation_date DESC;

-- ================================================================
-- DEMO 10: ACTIVE USERS (Logged in recently)
-- ================================================================
SELECT DISTINCT
    la.username,
    la.email,
    la.role,
    MAX(la.login_time) AS last_login,
    MAX(la.ip_address) AS last_ip
FROM login_audit la
WHERE la.status = 'success'
GROUP BY la.username, la.email, la.role
ORDER BY last_login DESC;
