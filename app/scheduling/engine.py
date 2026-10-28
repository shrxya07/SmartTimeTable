
from app.database.db import get_connection


def _to_minutes(time_str):
    """Convert HH:MM to minutes after midnight."""
    hour, minute = map(int, time_str.split(":"))
    return hour * 60 + minute


def find_teacher_schedule(teacher_name, day):
    """Return all classes for a teacher on a given day."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM timetable_entries
            WHERE LOWER(teacher_name) = LOWER(?)
              AND LOWER(day) = LOWER(?)
            ORDER BY start_time
            """,
            (teacher_name.strip(), day.strip()),
        ).fetchall()

    return [dict(row) for row in rows]


def find_teacher_at_time(teacher_name, day, time):
    """Find classes a teacher is taking at a particular time."""
    target = _to_minutes(time)

    return [
        entry
        for entry in find_teacher_schedule(teacher_name, day)
        if _to_minutes(entry["start_time"]) <= target
        < _to_minutes(entry["end_time"])
    ]


def find_class_schedule(class_name, day):
    """Return all classes for a class/section on a given day."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM timetable_entries
            WHERE LOWER(class_name) = LOWER(?)
              AND LOWER(day) = LOWER(?)
            ORDER BY start_time
            """,
            (class_name.strip(), day.strip()),
        ).fetchall()

    return [dict(row) for row in rows]


def find_room_at_time(room, day, time):
    """Find which class uses a room at a particular time."""
    target = _to_minutes(time)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM timetable_entries
            WHERE LOWER(room) = LOWER(?)
              AND LOWER(day) = LOWER(?)
            ORDER BY start_time
            """,
            (room.strip(), day.strip()),
        ).fetchall()

    return [
        dict(row) for row in rows
        if _to_minutes(row["start_time"]) <= target
        < _to_minutes(row["end_time"])
    ]


def detect_conflicts(day=None):
    """
    Detect overlapping classes for the same teacher, class, or room.

    Two intervals overlap if A starts before B ends and B starts
    before A ends. Classes that meet exactly at a boundary do not clash.
    """
    query = "SELECT * FROM timetable_entries"
    params = ()

    if day:
        query += " WHERE LOWER(day) = LOWER(?)"
        params = (day.strip(),)

    query += " ORDER BY day, start_time"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    entries = [dict(row) for row in rows]
    conflicts = []

    for i, first in enumerate(entries):
        for second in entries[i + 1:]:
            if first["day"].lower() != second["day"].lower():
                continue

            overlaps = (
                _to_minutes(first["start_time"])
                < _to_minutes(second["end_time"])
                and _to_minutes(second["start_time"])
                < _to_minutes(first["end_time"])
            )

            if not overlaps:
                continue

            reasons = []

            if first["teacher_name"].casefold() == second["teacher_name"].casefold():
                reasons.append("teacher")

            if first["class_name"].casefold() == second["class_name"].casefold():
                reasons.append("class")

            if first["room"].casefold() == second["room"].casefold():
                reasons.append("room")

            if reasons:
                conflicts.append({
                    "entry_1": first,
                    "entry_2": second,
                    "conflict_types": reasons,
                })

    return conflicts

def find_free_slots(
    teacher_name,
    day,
    min_duration_minutes=60,
    working_start="09:00",
    working_end="17:00",
):
    """
    Find gaps in a teacher's schedule during working hours.

    Returns free intervals long enough to meet the minimum duration.
    Assumes classes occur within the same day.
    """
    if min_duration_minutes <= 0:
        raise ValueError("Minimum duration must be positive")

    day_start = _to_minutes(working_start)
    day_end = _to_minutes(working_end)

    if day_start >= day_end:
        raise ValueError("Working start must be earlier than working end")

    schedule = find_teacher_schedule(teacher_name, day)

    intervals = sorted(
        (
            max(_to_minutes(entry["start_time"]), day_start),
            min(_to_minutes(entry["end_time"]), day_end),
        )
        for entry in schedule
    )

    free_slots = []
    current = day_start

    for start, end in intervals:
        if start >= end:
            continue

        if start > current and start - current >= min_duration_minutes:
            free_slots.append({
                "start_time": f"{current // 60:02d}:{current % 60:02d}",
                "end_time": f"{start // 60:02d}:{start % 60:02d}",
                "duration_minutes": start - current,
            })

        current = max(current, end)

    if day_end - current >= min_duration_minutes:
        free_slots.append({
            "start_time": f"{current // 60:02d}:{current % 60:02d}",
            "end_time": f"{day_end // 60:02d}:{day_end % 60:02d}",
            "duration_minutes": day_end - current,
        })

    return free_slots
