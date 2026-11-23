
import json
import re

from app.database.db import (
    get_connection,
    get_pending_reviews,
    get_teacher_aliases,
    person_key,
)


def search_entries(
    subject=None,
    teacher=None,
    semester=None,
    section=None,
    day=None,
    room=None,
    review_status="verified",
):
    """Search timetable entries using optional filters."""

    query = """
        SELECT
            id, timetable_id, semester, section,
            teacher_name, subject, class_name,
            day, start_time, end_time, room,
            source_document, review_status
        FROM timetable_entries
        WHERE 1=1
    """

    params = []

    if subject:
        query += " AND subject LIKE ?"
        params.append(f"%{subject}%")

    if teacher:
        query += " AND teacher_name LIKE ?"
        params.append(f"%{teacher}%")

    if semester is not None:
        query += " AND semester = ?"
        params.append(semester)

    if section:
        query += " AND section = ?"
        params.append(section)

    if day:
        query += " AND day = ?"
        params.append(day)

    if room:
        query += " AND room LIKE ?"
        params.append(f"%{room}%")

    if review_status:
        query += " AND review_status = ?"
        params.append(review_status)

    query += """
        ORDER BY semester, section, day, start_time
    """

    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def get_all_verified_entries():
    """Retrieve all verified entries across uploaded timetables."""
    return search_entries(review_status="verified")


def get_verified_teacher_identities():
    """Return distinct teacher names and initials from verified entries.

    Includes primary teachers and entry_teachers associations. Entries
    without a teacher (activities, breaks) are skipped.
    """
    query = """
        SELECT TRIM(teacher_name) AS teacher_name, '' AS initials
        FROM timetable_entries
        WHERE review_status = 'verified'
          AND teacher_name IS NOT NULL
          AND TRIM(teacher_name) <> ''
        UNION
        SELECT TRIM(association.teacher_name), TRIM(association.initials)
        FROM entry_teachers AS association
        JOIN timetable_entries AS entry
          ON entry.id = association.entry_id
        WHERE entry.review_status = 'verified'
          AND association.teacher_name IS NOT NULL
          AND TRIM(association.teacher_name) <> ''
    """

    with get_connection() as connection:
        rows = connection.execute(query).fetchall()

    return [
        {
            "teacher_name": row["teacher_name"],
            "initials": row["initials"] or "",
        }
        for row in rows
    ]


def get_verified_rooms():
    """Every room spelling used by verified entries.

    A booking naming several rooms ("AB 701 & AB702") contributes each one.
    Spellings of the same room are all returned; callers group them.
    """
    from app.scheduling.engine import split_rooms

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT TRIM(room) AS room
            FROM timetable_entries
            WHERE review_status = 'verified'
              AND room IS NOT NULL AND TRIM(room) <> ''
            """
        ).fetchall()
    return sorted({part for row in rows for part in split_rooms(row["room"])})


def get_verified_classes():
    """Return distinct class names with their semester and section."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT class_name, semester, section
            FROM timetable_entries
            WHERE review_status = 'verified'
            ORDER BY semester, section, class_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _initials_key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _person_key(value):
    """Same person despite spelling: 'DR.SUSHMA B' == 'Dr Sushma B'."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def get_faculty_directory():
    """Department-wide initials -> full name, from every course table.

    Sources: the course/faculty tables read from all uploaded timetables and
    approved records that store both a name and initials. Returns
    {initials: {"names": [...], "sources": [...]}}; more than one name means
    the initials are ambiguous and must not be used to fill a name in.
    """
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT course.faculty_initials AS initials,
                   course.faculty_name AS name,
                   timetable.original_filename AS source
            FROM timetable_courses AS course
            LEFT JOIN timetables AS timetable
              ON timetable.timetable_id = course.timetable_id
            WHERE course.faculty_initials IS NOT NULL
              AND course.faculty_name IS NOT NULL
            UNION ALL
            SELECT association.initials, association.teacher_name,
                   timetable.original_filename
            FROM entry_teachers AS association
            JOIN timetable_entries AS entry ON entry.id = association.entry_id
            LEFT JOIN timetables AS timetable
              ON timetable.timetable_id = entry.timetable_id
            WHERE entry.review_status = 'verified'
              AND association.initials <> ''
              AND association.teacher_name <> association.initials
            """
        ).fetchall()

    aliases = get_teacher_aliases()
    spellings = {}
    for row in rows:
        initials = _initials_key(row["initials"])
        name = str(row["name"]).strip()
        # Spellings a person confirmed are the same teacher count as one.
        name = aliases.get(person_key(name), name)
        if not initials or not name or _initials_key(name) == initials:
            continue
        person = spellings.setdefault(initials, {}).setdefault(
            _person_key(name), {"counts": {}, "sources": set()}
        )
        person["counts"][name] = person["counts"].get(name, 0) + 1
        if row["source"]:
            person["sources"].add(row["source"])

    directory = {}
    for initials, people in spellings.items():
        names, sources = [], set()
        for person in people.values():
            # Most common spelling of each distinct person.
            names.append(max(person["counts"], key=person["counts"].get))
            sources |= person["sources"]
        directory[initials] = {"names": sorted(names), "sources": sorted(sources)}
    return directory


MAX_INFERRED_BREAK_MINUTES = 90


def _minutes(clock):
    hour, minute = str(clock).split(":")
    return int(hour) * 60 + int(minute)


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_day_layout(day, class_name=None):
    """Teaching hours and breaks for free-time questions on one day.

    Hours come from the verified records of that day (so a half-day Saturday
    ends early). Breaks come from the period headers read at upload; for
    older timetables without one, a gap that no verified class uses on any
    day (and is at most 90 minutes) is treated as a break.
    """
    class_filter = " AND class_name = ?" if class_name else ""
    class_params = [class_name] if class_name else []

    with get_connection() as connection:
        # College hours, not one class's: the earliest start of the week and
        # the latest end on this day across all classes (half-days end early).
        bounds = connection.execute(
            """
            SELECT
                (SELECT MIN(start_time) FROM timetable_entries
                 WHERE review_status = 'verified') AS day_start,
                MAX(end_time) AS day_end
            FROM timetable_entries
            WHERE review_status = 'verified' AND day = ?
            """,
            [day],
        ).fetchone()
        if class_name and not connection.execute(
            """
            SELECT 1 FROM timetable_entries
            WHERE review_status = 'verified' AND day = ? AND class_name = ?
            """,
            [day, class_name],
        ).fetchone():
            return None
        layouts = connection.execute(
            f"""
            SELECT DISTINCT timetable.layout_json
            FROM timetables AS timetable
            JOIN timetable_entries AS entry
              ON entry.timetable_id = timetable.timetable_id
            WHERE entry.review_status = 'verified'
              AND timetable.layout_json IS NOT NULL{class_filter.replace("class_name", "entry.class_name")}
            """,
            class_params,
        ).fetchall()
        # Breaks are college-wide, so infer them from every class.
        all_slots = connection.execute(
            """
            SELECT start_time, end_time FROM timetable_entries
            WHERE review_status = 'verified'
            """
        ).fetchall()

    if not bounds["day_end"]:
        return None

    break_sets = [
        _layout_breaks(json.loads(row["layout_json"]))
        for row in layouts
    ]
    if break_sets:
        breaks = sorted(set.intersection(*break_sets))
    else:
        breaks = _inferred_breaks(all_slots)

    day_start, day_end = _minutes(bounds["day_start"]), _minutes(bounds["day_end"])
    breaks = [
        (start, end) for start, end in breaks
        if day_start < _minutes(end) and _minutes(start) < day_end
    ]
    return {
        "working_start": bounds["day_start"],
        "working_end": bounds["day_end"],
        "breaks": breaks,
    }


def _layout_breaks(layout):
    """Breaks marked in a period header, plus short gaps between periods.

    Many timetables print no column for a tea break; it shows only as a gap
    in the times (9.55-10.50 then 11.05-12.00).
    """
    breaks = {tuple(item) for item in layout.get("breaks") or []}
    periods = sorted(
        (tuple(item) for item in layout.get("periods") or []),
        key=lambda item: _minutes(item[0]),
    )
    for (_, end), (start, _) in zip(periods, periods[1:]):
        if 0 < _minutes(start) - _minutes(end) <= MAX_INFERRED_BREAK_MINUTES:
            breaks.add((end, start))
    return breaks


def _inferred_breaks(slots):
    """Short gaps between slots that no verified class ever occupies."""
    if not slots:
        return []
    busy = sorted((_minutes(row["start_time"]), _minutes(row["end_time"])) for row in slots)
    merged = [list(busy[0])]
    for start, end in busy[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        (_clock(previous[1]), _clock(following[0]))
        for previous, following in zip(merged, merged[1:])
        if following[0] - previous[1] <= MAX_INFERRED_BREAK_MINUTES
    ]


def get_timetable_entries(timetable_id, review_status=None):
    """Retrieve entries belonging to one timetable."""
    query = """
        SELECT *
        FROM timetable_entries
        WHERE timetable_id = ?
    """
    params = [timetable_id]

    if review_status:
        query += " AND review_status = ?"
        params.append(review_status)

    query += " ORDER BY day, start_time"

    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()

    return [dict(row) for row in rows]

def get_teacher_schedule_across_timetables(
    teacher_name,
    day=None,
    include_needs_review=False,
):
    """Find a teacher's schedule across all timetable documents."""
    if not teacher_name or not str(teacher_name).strip():
        return []

    query = """
        SELECT
            id,
            timetable_id,
            semester,
            section,
            teacher_name,
            subject,
            day,
            start_time,
            end_time,
            room,
            review_status
        FROM timetable_entries
        WHERE (
            teacher_name LIKE ?
            OR EXISTS (
                SELECT 1
                FROM entry_teachers AS association
                WHERE association.entry_id = timetable_entries.id
                  AND (
                    association.teacher_name LIKE ?
                    OR association.initials LIKE ?
                  )
            )
        )
    """

    teacher_pattern = f"%{str(teacher_name).strip()}%"
    params = [teacher_pattern, teacher_pattern, teacher_pattern]

    if day:
        query += " AND day = ?"
        params.append(day)

    if not include_needs_review:
        query += " AND review_status = 'verified'"

    query += """
        ORDER BY day, start_time, semester, section
    """

    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()

    return [dict(row) for row in rows]

def get_teacher_schedule_summary(teacher_name, day=None):
    """Return verified and unverified teacher records separately."""

    verified = get_teacher_schedule_across_timetables(
        teacher_name,
        day,
        include_needs_review=False,
    )

    needs_review = get_teacher_schedule_across_timetables(
        teacher_name,
        day,
        include_needs_review=True,
    )

    verified_ids = {entry["id"] for entry in verified}

    pending = [
        entry for entry in needs_review
        if entry["id"] not in verified_ids
    ]

    return {
        "teacher": teacher_name,
        "verified_records": verified,
        "needs_review_records": pending,
        "verified_count": len(verified),
        "needs_review_count": len(pending),
        "has_unverified_data": bool(pending),
    }


# A cropped, re-saved or PDF copy of a timetable has different file bytes but
# the same sessions, so compare what was extracted instead of the file.
SIMILAR_MIN_SHARED = 4
SIMILAR_MIN_SCORE = 0.8


_COURSE_FILLER = {"and", "of", "the", "for", "in", "to", "with"}
_COURSE_KIND = {"lab", "laboratory", "labs", "tut", "tutorial", "theory", "practical"}


def _course_variants(record):
    """Ways one course may be written: "DMS", "DMS TUT" and "Discrete
    Mathematical Structures" all give "dms"."""
    variants = set()
    for field in ("course_abbrev", "subject", "course", "course_name"):
        words = re.findall(r"[a-z0-9]+", str(record.get(field) or "").casefold())
        core = [word for word in words if word not in _COURSE_KIND]
        if not core:
            continue
        variants.add("".join(core))
        initials = "".join(word[0] for word in core if word not in _COURSE_FILLER)
        if len(initials) >= 2:
            variants.add(initials)
    return variants


def _same_course(first, second):
    return any(
        a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))
        for a in first
        for b in second
    )


def _session_key(record):
    day = str(record.get("day") or "").strip().casefold()
    try:
        start = _minutes(record.get("start_time"))
        end = _minutes(record.get("end_time"))
    except (TypeError, ValueError):
        return None
    if not day:
        return None
    return day, start, end


def _class_key(record_or_name):
    """'3A', 'CSE 3A' and 'Semester 3 Section A' all give '3a'."""
    if isinstance(record_or_name, dict):
        semester = str(record_or_name.get("semester") or "").strip()
        section = str(record_or_name.get("section") or "").strip()
        if semester and section:
            return f"{semester}{section}".casefold()
        record_or_name = record_or_name.get("class_name")
    words = re.findall(r"[a-z0-9]+", str(record_or_name or "").casefold())
    words = [
        word for word in words
        if word not in {"semester", "sem", "section", "sec", "cse", "class"}
    ]
    return "".join(words) or None


def _class_of(records, fallback=None):
    names = [key for key in map(_class_key, records) if key]
    if names:
        return max(set(names), key=names.count)
    return _class_key(fallback)


def _sessions(records):
    """{(day, start, end): [course variants, ...]} for the records."""
    sessions = {}
    for record in records:
        key = _session_key(record)
        if key:
            sessions.setdefault(key, []).append(_course_variants(record))
    return sessions


def _shared_sessions(new, other):
    """Time slots present in both with a matching course (or no course)."""
    shared = 0
    for key, courses in new.items():
        if key not in other:
            continue
        if any(
            not first or not second or _same_course(first, second)
            for first in courses
            for second in other[key]
        ):
            shared += 1
    return shared


def find_similar_timetables(records, class_name=None, exclude_timetable_id=None):
    """Earlier timetables whose sessions match these extracted records.

    Matches on day, times and course, so a cropped copy (a subset of the
    sessions), a screenshot or a PDF of the same timetable is still found.
    Approved and pending records are both compared.
    """
    new_sessions = _sessions(records)
    if len(new_sessions) < SIMILAR_MIN_SHARED:
        return []
    new_class = _class_of(records, class_name)

    others = {}
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT entry.timetable_id, entry.day, entry.start_time,
                   entry.end_time, entry.course_abbrev, entry.subject,
                   entry.class_name, timetable.original_filename
            FROM timetable_entries AS entry
            JOIN timetables AS timetable
              ON timetable.timetable_id = entry.timetable_id
            """
        ).fetchall()
    for row in rows:
        item = others.setdefault(
            row["timetable_id"],
            {"original_filename": row["original_filename"], "records": []},
        )
        item["records"].append(dict(row))

    for review in get_pending_reviews():
        item = others.setdefault(
            review["timetable_id"],
            {"original_filename": review["original_filename"], "records": []},
        )
        item["records"].extend(review["payload"].get("records") or [])
        item.setdefault(
            "class_name", (review["payload"].get("metadata") or {}).get("class_name")
        )

    matches = []
    for timetable_id, item in others.items():
        if timetable_id == exclude_timetable_id:
            continue
        other_class = _class_of(item["records"], item.get("class_name"))
        if new_class and other_class and new_class != other_class:
            continue
        sessions = _sessions(item["records"])
        if not sessions:
            continue
        shared = _shared_sessions(new_sessions, sessions)
        if shared < SIMILAR_MIN_SHARED:
            continue
        score = shared / min(len(new_sessions), len(sessions))
        if score >= SIMILAR_MIN_SCORE:
            matches.append({
                "timetable_id": timetable_id,
                "original_filename": item["original_filename"],
                "shared_sessions": shared,
                "new_sessions": len(new_sessions),
                "other_sessions": len(sessions),
                "score": round(score, 2),
            })
    return sorted(matches, key=lambda match: match["score"], reverse=True)
