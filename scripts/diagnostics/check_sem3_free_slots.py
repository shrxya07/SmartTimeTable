
import sqlite3
from app.scheduling.engine import find_free_slots

DB_PATH = "data/timetable.db"
TIMETABLE_ID = "sem3_a_2026_odd"

teacher = "Akshatha Kamath"
day = "Monday"

with sqlite3.connect(DB_PATH) as conn:
    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM timetable_entries
        WHERE timetable_id = ?
          AND teacher_name = ?
          AND day = ?
        """,
        (TIMETABLE_ID, teacher, day),
    ).fetchone()[0]

print("=== Semester III-A Free Slot Check ===")
print("Teacher:", teacher)
print("Day:", day)
print("Matching imported records:", count)

if count == 0:
    print("No matching imported timetable records found.")
else:
    slots = find_free_slots(
        teacher_name=teacher,
        day=day,
        min_duration_minutes=60,
        working_start="09:00",
        working_end="16:30",
        breaks=[
            ("10:50", "11:05"),
            ("12:55", "13:45"),
        ],
    )

    print("Available slots:")
    for slot in slots:
        print(slot)
