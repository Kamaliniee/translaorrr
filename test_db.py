import mysql.connector
import sys

print("Testing mysql.connector with user 'root' and password 'root'...")
try:
    conn = mysql.connector.connect(host='127.0.0.1', user='root', password='root')
    print("Success! Connected using mysql-connector.")
    conn.close()
    sys.exit(0)
except Exception as e:
    print("Failed to connect:", e)
    sys.exit(1)
