# MySQL Setup Guide for DocTranslate

## Quick Start (Windows)

### Step 1: Install MySQL Server

**Option A: Using Chocolatey (Recommended)**
```powershell
# Run PowerShell as Administrator
choco install mysql
```

**Option B: Download from MySQL Official**
1. Visit: https://dev.mysql.com/downloads/mysql/
2. Select MySQL Server 8.0.x LTS
3. Download Windows installer (MSI)
4. Run installer with default settings

### Step 2: Start MySQL Service

```powershell
# Start MySQL
net start MySQL80

# Or using PowerShell
Start-Service MySQL80

# Verify it's running
Get-Service MySQL*
```

### Step 3: Create Database

```cmd
# Connect to MySQL
mysql -h 127.0.0.1 -u root -p

# When prompted for password, press Enter (default has no password)
# Then run:
CREATE DATABASE doctranslate;
SHOW DATABASES;
EXIT;
```

### Step 4: Update Environment File

Edit `.env`:
```ini
MYSQL_HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DB=doctranslate
```

### Step 5: Install Python Dependencies

```bash
pip install -r requirement.txt
```

### Step 6: Run Application

```bash
python app.py
```

Visit: `http://127.0.0.1:8082`

---

## Setup Instructions by OS

### macOS

```bash
# Install MySQL using Homebrew
brew install mysql

# Start MySQL service
brew services start mysql

# Connect and create database
mysql -u root
# Then in MySQL:
CREATE DATABASE doctranslate;
EXIT;

# Update .env file
nano .env

# Run app
python app.py
```

### Linux (Ubuntu/Debian)

```bash
# Install MySQL Server
sudo apt-get update
sudo apt-get install mysql-server

# Start service
sudo systemctl start mysql

# Secure installation (recommended)
sudo mysql_secure_installation

# Create database
sudo mysql -u root -p
# Enter your root password, then:
CREATE DATABASE doctranslate;
EXIT;

# Update .env
nano .env

# Run app
python app.py
```

### Docker (Optional)

```bash
# Run MySQL in Docker
docker run --name doctranslate-mysql \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=doctranslate \
  -p 3306:3306 \
  -d mysql:8.0

# Then update .env:
MYSQL_HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PASSWORD=root
MYSQL_DB=doctranslate
```

---

## Configuration Details

### Default MySQL Credentials

| Setting | Value | Override in .env |
|---------|-------|-----------------|
| **Host** | 127.0.0.1 | `MYSQL_HOST` |
| **Port** | 3306 | Not required (hardcoded) |
| **User** | root | `MYSQL_USER` |
| **Password** | (none) | `MYSQL_PASSWORD` |
| **Database** | doctranslate | `MYSQL_DB` |

### Secure Setup (Production)

1. **Change root password**:
   ```bash
   mysql -u root
   ALTER USER 'root'@'localhost' IDENTIFIED BY 'SecurePassword123!';
   FLUSH PRIVILEGES;
   ```

2. **Create dedicated application user**:
   ```bash
   mysql -u root -p
   CREATE USER 'doctranslate'@'127.0.0.1' IDENTIFIED BY 'AppPassword456!';
   GRANT ALL PRIVILEGES ON doctranslate.* TO 'doctranslate'@'127.0.0.1';
   FLUSH PRIVILEGES;
   ```

3. **Update .env**:
   ```ini
   MYSQL_USER=doctranslate
   MYSQL_PASSWORD=AppPassword456!
   ```

---

## Verify Installation

### Test 1: MySQL Service Running

```bash
# Windows
Get-Service MySQL80 | Select Status

# macOS
brew services list | grep mysql

# Linux
sudo systemctl status mysql
```

### Test 2: MySQL Connection

```bash
mysql -h 127.0.0.1 -u root -p
# Press Enter if no password
```

### Test 3: Application Connection

```bash
python test_db.py
```

Expected output:
```
Testing mysql.connector with user 'root' and password 'root'...
Success! Connected using mysql-connector.
```

### Test 4: Full Application Test

```bash
python app.py
```

Navigate to: `http://127.0.0.1:8082/login`

---

## Database Auto-Initialization

On first run, `app.py` will:

1. ✅ Create database `doctranslate` if it doesn't exist
2. ✅ Create all required tables:
   - `users`
   - `translations`
   - `glossary`
   - `audit_logs`
   - `settings`
   - `batch_jobs`
3. ✅ Add migration columns if they're missing
4. ✅ Seed initial data:
   - Admin user
   - Sample glossary terms
   - Default settings

You should see this in console output:
```
Tables created successfully
Database initialized with sample data
```

---

## Connection Pooling (Optional Performance Improvement)

For high-traffic production environments, use `DBUtils`:

```bash
pip install DBUtils
```

Then in `app.py`:

```python
from dbutils.pooled_db import PooledDB

pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=5,
    host='127.0.0.1',
    user='root',
    password='root',
    database='doctranslate'
)

def get_db_connection(use_db=True):
    return pool.connection()
```

---

## Backup & Recovery

### Backup Database

```bash
# Full backup
mysqldump -u root -p doctranslate > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup all databases
mysqldump -u root -p --all-databases > full_backup.sql
```

### Restore Database

```bash
# Restore single database
mysql -u root -p doctranslate < backup_20260609_120000.sql

# Restore all databases
mysql -u root -p < full_backup.sql
```

### Automated Backup (Windows Task Scheduler)

Create `backup.bat`:
```batch
@echo off
set BACKUP_DIR=C:\MySQL\Backups
set DATE=%date:/=%

mkdir %BACKUP_DIR% 2>nul

mysqldump -h 127.0.0.1 -u root -p"root" doctranslate > %BACKUP_DIR%\doctranslate_%DATE%.sql

echo Backup completed: %DATE%
```

Schedule in Task Scheduler to run daily.

---

## Troubleshooting

### Problem: "Connection refused"
```
Error: (2003): Can't connect to MySQL server on '127.0.0.1' (10061)
```

**Solutions:**
```bash
# Check if MySQL is running
Get-Service MySQL80

# Start MySQL
net start MySQL80

# Check port 3306
netstat -ano | findstr :3306
```

### Problem: "Access denied for user 'root'@'localhost'"
```
Error: (1045): Access denied for user 'root'@'localhost' (using password: NO)
```

**Solutions:**
```bash
# Verify .env has correct password
cat .env | grep MYSQL_PASSWORD

# Reset root password if forgotten
mysql -u root -p
ALTER USER 'root'@'localhost' IDENTIFIED BY '';
FLUSH PRIVILEGES;
```

### Problem: "Unknown database 'doctranslate'"
```
Error: (1049): Unknown database 'doctranslate'
```

**Solutions:**
```bash
# Create database manually
mysql -u root -p
CREATE DATABASE doctranslate;

# Or restart app to trigger auto-creation
python app.py
```

### Problem: Port 3306 Already in Use
```
Error: (1040): Too many connections
```

**Solutions:**
```bash
# Find process using port 3306
netstat -ano | findstr :3306

# Kill process (replace PID)
taskkill /PID <PID> /F

# Or change MySQL port in my.ini
```

---

## Performance Tuning

### Recommended my.ini Settings

Location: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`

```ini
[mysqld]
# Connection pool
max_connections = 100
max_allowed_packet = 256M

# InnoDB buffer (50-80% of RAM for MySQL)
innodb_buffer_pool_size = 2G

# Query cache (MySQL 5.7)
query_cache_size = 64M
query_cache_type = 1

# Slow query logging (for debugging)
slow_query_log = 1
slow_query_log_file = slow.log
long_query_time = 2
```

After changes, restart MySQL:
```bash
net stop MySQL80
net start MySQL80
```

---

## Monitoring & Health Check

### Check Database Size

```sql
SELECT 
    table_name, 
    ROUND(((data_length + index_length) / 1024 / 1024), 2) as size_mb
FROM information_schema.TABLES 
WHERE table_schema = 'doctranslate'
ORDER BY size_mb DESC;
```

### Monitor Active Connections

```sql
SHOW PROCESSLIST;
```

### Check Slow Queries

```sql
SELECT * FROM mysql.slow_log LIMIT 10;
```

### Health Check Script (Python)

```python
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

try:
    conn = pymysql.connect(
        host=os.environ.get('MYSQL_HOST'),
        user=os.environ.get('MYSQL_USER'),
        password=os.environ.get('MYSQL_PASSWORD'),
        database=os.environ.get('MYSQL_DB')
    )
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM translations")
    count = cursor.fetchone()[0]
    print(f"✅ Database healthy. Total translations: {count}")
    conn.close()
except Exception as e:
    print(f"❌ Database error: {e}")
```

---

## Remote MySQL Access (Production)

To allow connections from remote servers:

```sql
mysql -u root -p
GRANT ALL PRIVILEGES ON doctranslate.* TO 'doctranslate'@'%' IDENTIFIED BY 'password';
FLUSH PRIVILEGES;
```

Update firewall rules:
- Allow port 3306 inbound (production: restrict by IP)

Update .env:
```ini
MYSQL_HOST=your.production.server.com
MYSQL_USER=doctranslate
MYSQL_PASSWORD=password
MYSQL_DB=doctranslate
```

---

## Additional Resources

- [MySQL Official Documentation](https://dev.mysql.com/doc/)
- [PyMySQL Documentation](https://pymysql.readthedocs.io/)
- [MySQL Workbench GUI Tool](https://dev.mysql.com/downloads/workbench/)
- [DBeaver SQL IDE](https://dbeaver.io/)

---

**Setup Completed!** 🎉

Your DocTranslate application is now ready to use with MySQL.

For issues, check the troubleshooting section or review application logs:
```bash
python app.py 2>&1 | tee app.log
```
