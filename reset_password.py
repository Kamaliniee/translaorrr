from werkzeug.security import generate_password_hash

new_password = 'Admin@1234'
hashed = generate_password_hash(new_password)

print('=== Copy and run this in MySQL command line ===')
print()
print('USE doctranslate;')
print("UPDATE users SET password_hash = '" + hashed + "' WHERE email = 'barath@company.com';")
print("SELECT username, email, role FROM users WHERE email = 'barath@company.com';")
print()
print('=== Your new login details ===')
print('Email   : barath@company.com')
print('Password: ' + new_password)
