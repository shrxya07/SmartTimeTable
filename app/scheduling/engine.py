
from app.database.db import get_connection


def _to_minutes(time_str):
    """Convert HH:MM to minutes after midnight."""
    hour, minute = map(int, time_str.split(":"))
    return hour * 60 + minute



def find_teacher_schedule(
    teacher_name,
    day,
    semester=None,
    section=None,
    include_needs_review=False,
):
    """Return a teacher's classes with optional semester/section filters."""

    query = """
        SELECT * FROM timetable_entries
        WHERE LOWER(teacher_name) = LOWER(?)
          AND LOWER(day) = LOWER(?)
    """
    params = [teacher_name.strip(), day.strip()]

    if semester is not None:
        query += " AND semester = ?"
        params.append(semester)

    if section is not None:
        query += " AND LOWER(section) = LOWER(?)"
        params.append(section.strip())

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    query += " ORDER BY start_time"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

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




def find_class_schedule(
    class_name,
    day,
    semester=None,
    section=None,
    include_needs_review=False,
):
    """Return a class schedule with optional semester/section filters."""

    query = """
        SELECT * FROM timetable_entries
        WHERE LOWER(class_name) = LOWER(?)
          AND LOWER(day) = LOWER(?)
    """
    params = [class_name.strip(), day.strip()]

    if semester is not None:
        query += " AND semester = ?"
        params.append(semester)

    if section is not None:
        query += " AND LOWER(section) = LOWER(?)"
        params.append(section.strip())

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    query += " ORDER BY start_time"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

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



def detect_conflicts(day=None, include_needs_review=False):
    """
    Detect overlapping classes for the same teacher, class, or room.
    Verified records are used by default.
    """
    query = "SELECT * FROM timetable_entries"
    conditions = []
    params = []

    if day:
        conditions.append("LOWER(day) = LOWER(?)")
        params.append(day.strip())

    if not include_needs_review:
        conditions.append("review_status = 'verified'")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

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

            first_teacher = first["teacher_name"].strip()
            second_teacher = second["teacher_name"].strip()

            if (
                first_teacher.casefold() != "unknown"
                and second_teacher.casefold() != "unknown"
                and first_teacher.casefold() == second_teacher.casefold()
            ):
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
    breaks=None,
    semester=None,
    section=None,
):
    """
    Find gaps in a teacher's schedule during working hours.

    Optional breaks is a list of (start_time, end_time) tuples.
    Example: [("10:50", "11:05"), ("12:55", "13:45")]
    """
    if min_duration_minutes <= 0:
        raise ValueError("Minimum duration must be positive")

    day_start = _to_minutes(working_start)
    day_end = _to_minutes(working_end)

    if day_start >= day_end:
        raise ValueError("Working start must be earlier than working end")

    schedule = find_teacher_schedule(
    teacher_name,
    day,
    semester=semester,
    section=section,
)

    intervals = [
        (
            max(_to_minutes(entry["start_time"]), day_start),
            min(_to_minutes(entry["end_time"]), day_end),
        )
        for entry in schedule
    ]

    for break_start, break_end in (breaks or []):
        intervals.append((
            max(_to_minutes(break_start), day_start),
            min(_to_minutes(break_end), day_end),
        ))

    intervals.sort()

    merged = []
    for start, end in intervals:
        if start >= end:
            continue

        if merged and start <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end),
            )
        else:
            merged.append((start, end))

    free_slots = []
    current = day_start

    for start, end in merged:
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

def find_room_free_slots(
    room,
    day,
    min_duration_minutes=60,
    working_start="09:00",
    working_end="17:00",
    breaks=None,
    include_needs_review=False,
):
    """Find available time slots for a classroom."""

    if min_duration_minutes <= 0:
        raise ValueError("Minimum duration must be positive")

    day_start = _to_minutes(working_start)
    day_end = _to_minutes(working_end)

    if day_start >= day_end:
        raise ValueError("Working start must be earlier than working end")

    query = """
        SELECT start_time, end_time
        FROM timetable_entries
        WHERE LOWER(room) = LOWER(?)
          AND LOWER(day) = LOWER(?)
    """
    params = [room.strip(), day.strip()]

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    query += " ORDER BY start_time"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    intervals = [
        (
            max(_to_minutes(row["start_time"]), day_start),
            min(_to_minutes(row["end_time"]), day_end),
        )
        for row in rows
    ]

    for break_start, break_end in (breaks or []):
        intervals.append((
            max(_to_minutes(break_start), day_start),
            min(_to_minutes(break_end), day_end),
        ))

    intervals.sort()

    merged = []

    for start, end in intervals:
        if start >= end:
            continue

        if merged and start <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end),
            )
        else:
            merged.append((start, end))

    free_slots = []
    current = day_start

    for start, end in merged:
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

def find_common_free_slots(
    entities,
    entity_type,
    day,
    min_duration_minutes=60,
    working_start="09:00",
    working_end="17:00",
    breaks=None,
    semester=None,
    section=None,
    include_needs_review=False,
):
    """Find common free slots for multiple teachers or sections."""

    if not entities or len(entities) < 2:
        raise ValueError("At least two entities are required")

    if entity_type not in ("teacher", "section"):
        raise ValueError("entity_type must be 'teacher' or 'section'")

    if min_duration_minutes <= 0:
        raise ValueError("Minimum duration must be positive")

    day_start = _to_minutes(working_start)
    day_end = _to_minutes(working_end)

    if day_start >= day_end:
        raise ValueError("Working start must be earlier than working end")

    column = "teacher_name" if entity_type == "teacher" else "class_name"
    all_intervals = []

    for entity in entities:
        query = f"""
            SELECT start_time, end_time
            FROM timetable_entries
            WHERE LOWER({column}) = LOWER(?)
              AND LOWER(day) = LOWER(?)
        """
        params = [entity.strip(), day.strip()]

        if semester is not None:
            query += " AND semester = ?"
            params.append(semester)

        if section is not None:
            query += " AND LOWER(section) = LOWER(?)"
            params.append(section.strip())

        if not include_needs_review:
            query += " AND review_status = 'verified'"

        with get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        for row in rows:
            start = max(_to_minutes(row["start_time"]), day_start)
            end = min(_to_minutes(row["end_time"]), day_end)

            if start < end:
                all_intervals.append((start, end))

    for break_start, break_end in (breaks or []):
        all_intervals.append((
            max(_to_minutes(break_start), day_start),
            min(_to_minutes(break_end), day_end),
        ))

    all_intervals.sort()
    merged = []

    for start, end in all_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end),
            )
        else:
            merged.append((start, end))

    free_slots = []
    current = day_start

    for start, end in merged:
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

def find_section_free_slots(
    class_name,
    day,
    min_duration_minutes=60,
    working_start="09:00",
    working_end="17:00",
    breaks=None,
    semester=None,
    section=None,
    include_needs_review=False,
):
    """Find free slots for a class/section."""

    if min_duration_minutes <= 0:
        raise ValueError("Minimum duration must be positive")

    day_start = _to_minutes(working_start)
    day_end = _to_minutes(working_end)

    if day_start >= day_end:
        raise ValueError("Working start must be earlier than working end")

    query = """
        SELECT start_time, end_time
        FROM timetable_entries
        WHERE LOWER(class_name) = LOWER(?)
          AND LOWER(day) = LOWER(?)
    """
    params = [class_name.strip(), day.strip()]

    if semester is not None:
        query += " AND semester = ?"
        params.append(semester)

    if section is not None:
        query += " AND LOWER(section) = LOWER(?)"
        params.append(section.strip())

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    intervals = []

    for row in rows:
        start = max(_to_minutes(row["start_time"]), day_start)
        end = min(_to_minutes(row["end_time"]), day_end)

        if start < end:
            intervals.append((start, end))

    for break_start, break_end in (breaks or []):
        intervals.append((
            max(_to_minutes(break_start), day_start),
            min(_to_minutes(break_end), day_end),
        ))

    intervals.sort()
    merged = []

    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end),
            )
        else:
            merged.append((start, end))

    free_slots = []
    current = day_start

    for start, end in merged:
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
