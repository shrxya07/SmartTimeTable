
import re

from app.database.db import get_connection

# A booking may name several rooms: "AB 701 & AB702", "CSE LAB4, CSE LAB2".
_ROOM_SEPARATORS = re.compile(r"\s*[&,/;+]\s*")


def room_key(value):
    """Same room despite spelling: 'CSE LAB1' == 'cse lab 1' == 'CSE-Lab 1'."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def split_rooms(value):
    """Each room named in a booking's room text."""
    return [
        part.strip()
        for part in _ROOM_SEPARATORS.split(str(value or ""))
        if part.strip()
    ]


def find_room_bookings(room, day, include_needs_review=False):
    """Every entry using a room on a day, across all timetables.

    ``room`` may be one name or a list of the room's spellings; bookings
    that list several rooms count for each of them.
    """
    names = [room] if isinstance(room, str) else list(room or [])
    keys = {room_key(name) for name in names if room_key(name)}
    if not keys:
        return []
    query = """
        SELECT * FROM timetable_entries
        WHERE LOWER(day) = LOWER(?) AND room IS NOT NULL
    """
    if not include_needs_review:
        query += " AND review_status = 'verified'"
    query += " ORDER BY start_time"
    with get_connection() as conn:
        rows = conn.execute(query, (day.strip(),)).fetchall()
    return [
        dict(row) for row in rows
        if any(room_key(part) in keys for part in split_rooms(row["room"]))
    ]


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
    """Return a teacher's classes with optional semester/section filters.

    ``teacher_name`` may be one name or a list of the same person's spellings
    and initials (e.g. ["DR.SUSHMA B", "DR SUSHMA B", "SB"]), so classes from
    every timetable are found however each timetable wrote the name.
    """
    names = (
        [teacher_name] if isinstance(teacher_name, str) else list(teacher_name or [])
    )
    names = sorted({str(name).strip().lower() for name in names if str(name or "").strip()})
    if not names:
        return []
    marks = ", ".join("?" for _ in names)

    query = f"""
        SELECT entry.* FROM timetable_entries AS entry
        WHERE (
            LOWER(TRIM(entry.teacher_name)) IN ({marks})
            OR EXISTS (
                SELECT 1
                FROM entry_teachers AS association
                WHERE association.entry_id = entry.id
                  AND (
                    LOWER(TRIM(association.teacher_name)) IN ({marks})
                    OR LOWER(TRIM(association.initials)) IN ({marks})
                  )
            )
        )
          AND LOWER(entry.day) = LOWER(?)
    """
    params = [*names, *names, *names, day.strip()]

    if semester is not None:
        query += " AND entry.semester = ?"
        params.append(semester)

    if section is not None:
        query += " AND LOWER(entry.section) = LOWER(?)"
        params.append(section.strip())

    if not include_needs_review:
        query += " AND entry.review_status = 'verified'"

    query += " ORDER BY entry.start_time"

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
    class_name=None,
    day=None,
    semester=None,
    section=None,
    include_needs_review=False,
):
    """Return a class schedule using class name or semester/section."""

    if not day:
        return {"error": "A day is required."}

    if not class_name and semester is None and not section:
        return {
            "error": "Provide a class name or semester/section."
        }

    query = """
        SELECT * FROM timetable_entries
        WHERE LOWER(day) = LOWER(?)
    """
    params = [day.strip()]

    if class_name:
        query += " AND LOWER(class_name) = LOWER(?)"
        params.append(class_name.strip())

    if semester is not None:
        query += " AND semester = ?"
        params.append(semester)

    if section:
        query += " AND LOWER(section) = LOWER(?)"
        params.append(section.strip())

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    query += " ORDER BY start_time"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]
def find_room_at_time(room, day, time, include_needs_review=False):
    """Find which class uses a room at a particular time."""
    target = _to_minutes(time)

    return [
        entry
        for entry in find_room_bookings(room, day, include_needs_review)
        if _to_minutes(entry["start_time"]) <= target
        < _to_minutes(entry["end_time"])
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
        entry_ids = [row["id"] for row in rows]
        associations = {}
        for offset in range(0, len(entry_ids), 500):
            batch = entry_ids[offset:offset + 500]
            placeholders = ", ".join("?" for _ in batch)
            association_rows = conn.execute(
                f"""
                SELECT entry_id, teacher_name, initials
                FROM entry_teachers
                WHERE entry_id IN ({placeholders})
                """,
                batch,
            ).fetchall()
            for association in association_rows:
                associations.setdefault(association["entry_id"], set()).update(
                    _teacher_tokens(
                        association["teacher_name"],
                        association["initials"],
                    )
                )

    entries = [dict(row) for row in rows]
    teacher_tokens = {
        entry["id"]: associations.get(entry["id"]) or (
            _teacher_tokens(entry.get("teacher_name"))
        )
        for entry in entries
    }
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

            first_parallel = _identity_value(first.get("parallel_group"))
            second_parallel = _identity_value(second.get("parallel_group"))
            if first_parallel and first_parallel == second_parallel:
                continue

            if (
                first.get("timetable_id")
                and second.get("timetable_id")
                and first.get("timetable_id") != second.get("timetable_id")
                and _same_event(
                    first,
                    second,
                    teacher_tokens[first["id"]],
                    teacher_tokens[second["id"]],
                )
            ):
                continue

            reasons = []

            if teacher_tokens[first["id"]] & teacher_tokens[second["id"]]:
                reasons.append("teacher")

            first_class = _identity_value(first.get("class_name"))
            second_class = _identity_value(second.get("class_name"))
            if (
                first_class
                and first_class == second_class
                and _identity_value(first.get("subgroup"))
                == _identity_value(second.get("subgroup"))
            ):
                reasons.append("class")

            first_rooms = {room_key(r) for r in split_rooms(first.get("room"))}
            second_rooms = {room_key(r) for r in split_rooms(second.get("room"))}
            if first_rooms & second_rooms - {"", "none", "null", "unknown"}:
                reasons.append("room")

            if reasons:
                conflicts.append({
                    "entry_1": first,
                    "entry_2": second,
                    "conflict_types": reasons,
                })

    return conflicts


def _identity_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.casefold() in {"unknown", "none", "null"}:
        return None
    return value.casefold()


def _teacher_tokens(teacher_name, initials=None):
    return {
        token
        for token in (
            _identity_value(teacher_name),
            _identity_value(initials),
        )
        if token is not None
    }


def _same_event(first, second, first_teachers, second_teachers):
    first_course = (
        _identity_value(first.get("course_abbrev"))
        or _identity_value(first.get("subject"))
    )
    second_course = (
        _identity_value(second.get("course_abbrev"))
        or _identity_value(second.get("subject"))
    )
    return (
        _identity_value(first.get("day"))
        == _identity_value(second.get("day"))
        and _identity_value(first.get("start_time"))
        == _identity_value(second.get("start_time"))
        and _identity_value(first.get("end_time"))
        == _identity_value(second.get("end_time"))
        and first_course is not None
        and first_course == second_course
        and _identity_value(first.get("subgroup"))
        == _identity_value(second.get("subgroup"))
        and _identity_value(first.get("room"))
        == _identity_value(second.get("room"))
        and first_teachers == second_teachers
    )



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

    rows = find_room_bookings(room, day, include_needs_review)

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

    all_intervals = []

    for entity in entities:
        if entity_type == "teacher":
            entity_name = entity.strip()
            if not entity_name:
                continue
            query = """
                SELECT entry.start_time, entry.end_time
                FROM timetable_entries AS entry
                WHERE (
                    LOWER(entry.teacher_name) = LOWER(?)
                    OR EXISTS (
                        SELECT 1
                        FROM entry_teachers AS association
                        WHERE association.entry_id = entry.id
                          AND (
                            LOWER(association.teacher_name) = LOWER(?)
                            OR LOWER(association.initials) = LOWER(?)
                          )
                    )
                )
                  AND LOWER(entry.day) = LOWER(?)
            """
            params = [entity_name, entity_name, entity_name, day.strip()]
        else:
            query = """
                SELECT start_time, end_time
                FROM timetable_entries
                WHERE LOWER(class_name) = LOWER(?)
                  AND LOWER(day) = LOWER(?)
            """
            params = [entity.strip(), day.strip()]

        if semester is not None:
            query += " AND entry.semester = ?" if entity_type == "teacher" else " AND semester = ?"
            params.append(semester)

        if section is not None:
            query += (
                " AND LOWER(entry.section) = LOWER(?)"
                if entity_type == "teacher"
                else " AND LOWER(section) = LOWER(?)"
            )
            params.append(section.strip())

        if not include_needs_review:
            query += (
                " AND entry.review_status = 'verified'"
                if entity_type == "teacher"
                else " AND review_status = 'verified'"
            )

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
