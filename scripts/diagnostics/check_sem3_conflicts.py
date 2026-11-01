
from app.scheduling.engine import detect_conflicts

conflicts = detect_conflicts(day="Monday")

print("=== Monday Conflict Check ===")
print("Total conflicts found:", len(conflicts))

for conflict in conflicts:
    first = conflict["entry_1"]
    second = conflict["entry_2"]

    print("\nConflict types:", conflict["conflict_types"])

    print(
        f"Entry 1: ID={first['id']} | {first['teacher_name']} | "
        f"{first['subject']} | {first['start_time']}-{first['end_time']} | "
        f"{first['room']} | timetable_id={first.get('timetable_id')}"
    )

    print(
        f"Entry 2: ID={second['id']} | {second['teacher_name']} | "
        f"{second['subject']} | {second['start_time']}-{second['end_time']} | "
        f"{second['room']} | timetable_id={second.get('timetable_id')}"
    )
