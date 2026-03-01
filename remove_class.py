import sqlite3

conn = sqlite3.connect('qsi_grades.db')
c = conn.cursor()

classes_to_remove = ('ss1j', 'ss 1j', 'ss1q', 'ss 1q')

# Remove from students table
c.execute("DELETE FROM students WHERE LOWER(class) IN (?, ?, ?, ?)", classes_to_remove)
students_deleted = c.rowcount

# Remove from classes table
try:
    c.execute("DELETE FROM classes WHERE LOWER(name) IN (?, ?, ?, ?)", classes_to_remove)
    classes_deleted = c.rowcount
except sqlite3.OperationalError:
    classes_deleted = 0

conn.commit()
conn.close()

print(f"Deleted {students_deleted} students and {classes_deleted} class records for SS1J and SS1Q.")
