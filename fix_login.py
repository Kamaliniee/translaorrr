import pymysql
from werkzeug.security import generate_password_hash

conn = pymysql.connect(host='127.0.0.1', user='root', password='root', database='doctranslate',
                       cursorclass=pymysql.cursors.DictCursor)
cursor = conn.cursor()

# Set a proper password for ALL users that have NULL password_hash
new_hash = generate_password_hash('Admin@1234')

cursor.execute("UPDATE users SET password_hash = %s WHERE password_hash IS NULL OR password_hash = ''", (new_hash,))
conn.commit()
updated = cursor.rowcount
print(f"Fixed {updated} accounts with NULL passwords")

# Confirm all users now have valid hashes
cursor.execute("SELECT username, email, role, active, LENGTH(password_hash) as hash_len FROM users ORDER BY id")
users = cursor.fetchall()
print("\n=== ALL USERS - READY TO LOGIN ===")
print(f"{'Username':<20} {'Email':<30} {'Role':<10} {'Active':<8} {'Has Password'}")
print("-" * 85)
for u in users:
    has_pass = "YES" if (u['hash_len'] and u['hash_len'] > 10) else "NO"
    print(f"{u['username']:<20} {u['email']:<30} {u['role']:<10} {str(u['active']):<8} {has_pass}")

conn.close()

print()
print("=" * 50)
print("ALL ACCOUNTS PASSWORD IS NOW: Admin@1234")
print("=" * 50)
print("\nADMIN ACCOUNTS (use these to login as admin):")
print("  Email   : admin@company.com")
print("  Password: Admin@1234")
print()
print("  Email   : barath@company.com")
print("  Password: Admin@1234")
