
from app.database.db import get_connection


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
    """Return distinct room names used by verified entries."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT TRIM(room) AS room
            FROM timetable_entries
            WHERE review_status = 'verified'
              AND room IS NOT NULL AND TRIM(room) <> ''
            ORDER BY room
            """
        ).fetchall()
    return [row["room"] for row in rows]


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
