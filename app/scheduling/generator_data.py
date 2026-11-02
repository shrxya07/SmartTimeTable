"""Load generator inputs from the approved timetable data."""

import json

from app.database.db import get_connection
from app.scheduling.engine import split_rooms
from app.scheduling.generator import Requirement


def _minutes(clock):
    hour, minute = str(clock).split(":")
    return int(hour) * 60 + int(minute)


def _verified_entries(class_name=None, exclude_class=None):
    query = "SELECT * FROM timetable_entries WHERE review_status = 'verified'"
    params = []
    if class_name:
        query += " AND class_name = ?"
        params.append(class_name)
    if exclude_class:
        query += " AND (class_name IS NULL OR class_name <> ?)"
        params.append(exclude_class)
    with get_connection() as connection:
        entries = [dict(row) for row in connection.execute(query, params)]
        teachers = {}
        for row in connection.execute(
            "SELECT entry_id, teacher_name FROM entry_teachers"
        ):
            teachers.setdefault(row["entry_id"], []).append(row["teacher_name"])
    for entry in entries:
        names = list(teachers.get(entry["id"], []))
        if entry.get("teacher_name") and entry["teacher_name"] not in names:
            names.insert(0, entry["teacher_name"])
        entry["all_teachers"] = names
    return entries


def existing_commitments(exclude_class=None):
    """Teacher and room bookings of every other class (approved records)."""
    busy = {"teacher": {}, "room": {}}
    for entry in _verified_entries(exclude_class=exclude_class):
        span = (entry["day"], (_minutes(entry["start_time"]), _minutes(entry["end_time"])))
        for name in entry["all_teachers"]:
            busy["teacher"].setdefault(name, []).append(span)
        if entry.get("room"):
            for room in split_rooms(entry["room"]):
                busy["room"].setdefault(room, []).append(span)
    return busy


def teaching_days():
    """Days with approved classes and each day's teaching hours."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT day, MAX(end_time) AS day_end,
                   (SELECT MIN(start_time) FROM timetable_entries
                    WHERE review_status = 'verified') AS week_start
            FROM timetable_entries
            WHERE review_status = 'verified'
            GROUP BY day
            """
        ).fetchall()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
    # Same start every day; each day ends when its last class ends.
    bounds = {row["day"]: (row["week_start"], row["day_end"]) for row in rows}
    return [day for day in order if day in bounds], bounds


def teaching_periods(class_name=None):
    """Teaching periods from the stored period header, else from the data.

    Inferred periods are the gaps between consecutive start/end times that
    some approved class actually occupies, so breaks drop out.
    """
    # Classes share the college's period grid, so infer from all of them.
    entries = _verified_entries()
    with get_connection() as connection:
        layouts = [
            json.loads(row["layout_json"])
            for row in connection.execute(
                """
                SELECT DISTINCT t.layout_json FROM timetables AS t
                JOIN timetable_entries AS e ON e.timetable_id = t.timetable_id
                WHERE e.review_status = 'verified' AND t.layout_json IS NOT NULL
                """ + (" AND e.class_name = ?" if class_name else ""),
                [class_name] if class_name else [],
            )
        ]
    for layout in layouts:
        if layout.get("periods"):
            return [tuple(period) for period in layout["periods"]]

    boundaries = sorted(
        {entry["start_time"] for entry in entries}
        | {entry["end_time"] for entry in entries},
        key=_minutes,
    )
    periods = []
    for start, end in zip(boundaries, boundaries[1:]):
        if any(
            _minutes(entry["start_time"]) <= _minutes(start)
            and _minutes(end) <= _minutes(entry["end_time"])
            for entry in entries
        ):
            periods.append((start, end))
    return periods


def _periods_spanned(entry, periods):
    start, end = _minutes(entry["start_time"]), _minutes(entry["end_time"])
    return max(1, sum(
        1 for period_start, period_end in periods
        if start <= _minutes(period_start) and _minutes(period_end) <= end
    ))


def requirements_from_class(class_name, periods=None):
    """Weekly requirements of a class, read from its approved timetable.

    Parallel sections/batches taught at the same time become one requirement
    that needs all their teachers and rooms together. Activities are
    returned separately as fixed (day, start, end) events.
    """
    periods = periods or teaching_periods(class_name)
    entries = _verified_entries(class_name=class_name)

    occurrences = {}
    fixed = []
    for entry in entries:
        if entry.get("entry_type") in ("activity", "break"):
            fixed.append((entry["day"], entry["start_time"], entry["end_time"]))
            continue
        key = (
            entry.get("parallel_group")
            or f"{entry['day']} {entry['start_time']}-{entry['end_time']} #{entry['id']}"
        )
        occurrence = occurrences.setdefault(key, {
            "courses": [], "abbrevs": [], "teachers": [], "rooms": [],
            "kinds": set(), "length": _periods_spanned(entry, periods),
        })
        course = entry.get("subject") or entry.get("course_abbrev")
        if course not in occurrence["courses"]:
            occurrence["courses"].append(course)
        abbrev = entry.get("course_abbrev")
        if abbrev and abbrev not in occurrence["abbrevs"]:
            occurrence["abbrevs"].append(abbrev)
        for name in entry["all_teachers"]:
            if name not in occurrence["teachers"]:
                occurrence["teachers"].append(name)
        if entry.get("room") and entry["room"] not in occurrence["rooms"]:
            occurrence["rooms"].append(entry["room"])
        occurrence["kinds"].add(entry.get("entry_type") or "class")

    grouped = {}
    for occurrence in occurrences.values():
        kind = next(
            (k for k in ("lab", "tutorial", "elective") if k in occurrence["kinds"]),
            "class",
        )
        key = (
            " / ".join(occurrence["courses"]),
            kind,
            occurrence["length"],
            tuple(occurrence["teachers"]),
            tuple(occurrence["rooms"]),
        )
        if key not in grouped:
            grouped[key] = Requirement(
                course=key[0],
                sessions_per_week=0,
                periods_per_session=occurrence["length"],
                kind=kind,
                teachers=list(occurrence["teachers"]),
                rooms=list(occurrence["rooms"]),
                course_abbrev="/".join(occurrence["abbrevs"]) or None,
            )
        grouped[key].sessions_per_week += 1

    requirements = sorted(
        grouped.values(),
        key=lambda r: (-r.periods_per_session, r.course),
    )
    return requirements, sorted(set(fixed))
