
from app.scheduling.engine import (
    find_teacher_schedule,
    find_class_schedule,
    find_room_at_time,
)

print("=== Semester III-A Scheduling Checks ===")

# Check a teacher from the imported timetable.
teacher_result = find_teacher_schedule(
    "Akshatha Kamath", "Monday"
)
print("\nAkshatha Kamath on Monday:")
for row in teacher_result:
    if row.get("timetable_id") == "sem3_a_2026_odd":
        print(row)

# Check the imported class.
class_result = find_class_schedule("Semester 3 Section A", "Monday")
print("\nSemester 3 Section A on Monday:")
for row in class_result:
    if row.get("timetable_id") == "sem3_a_2026_odd":
        print(row)

# Check the lab room during a scheduled lab.
room_result = find_room_at_time("CSE LAB 1", "Monday", "11:30")
print("\nCSE LAB 1 on Monday at 11:30:")
for row in room_result:
    if row.get("timetable_id") == "sem3_a_2026_odd":
        print(row)
