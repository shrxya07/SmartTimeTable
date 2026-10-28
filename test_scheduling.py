
from app.scheduling.engine import (
    find_teacher_schedule,
    find_teacher_at_time,
    find_class_schedule,
    find_room_at_time,
    detect_conflicts,
)

print("\nTeacher schedule:")
print(find_teacher_schedule("Dr Rao", "Monday"))

print("\nTeacher at 09:30:")
print(find_teacher_at_time("Dr Rao", "Monday", "09:30"))

print("\nCSE-A schedule:")
print(find_class_schedule("CSE-A", "Monday"))

print("\nRoom 301 at 09:30:")
print(find_room_at_time("Room 301", "Monday", "09:30"))

print("\nConflicts:")
print(detect_conflicts("Monday"))
