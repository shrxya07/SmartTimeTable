
import sqlite3

from app.database.db import (
    derive_class_name,
    get_connection,
    insert_entry,
    normalize_missing,
)
from app.ingestion.room_resolver import resolve_room


REQUIRED_FIELDS = {
    "subject",
    "day",
    "start_time",
    "end_time",
}


def import_timetable_records(
    records,
    default_room=None,
    semester=None,
    section=None,
    timetable_id=None,
    source_document=None,
):
    """Replace one timetable's records safely."""

    if not timetable_id:
        raise ValueError("timetable_id is required for safe replacement")
    records = list(records or [])

    if not records:
        return [{
            "status": "not_imported",
            "reason": (
                "No records supplied. "
                "Existing timetable data was left unchanged."
            ),
        }]
    prepared = []
    results = []

    # Validate every record before deleting existing entries.
    for index, record in enumerate(records, start=1):
        entry = record.copy()
        entry["semester"] = (
            semester if semester is not None else entry.get("semester")
        )
        entry["section"] = (
            section if section is not None else entry.get("section")
        )
        entry["timetable_id"] = timetable_id
        entry["source_document"] = (
            entry.get("source_document") or source_document
        )
        entry["entry_type"] = (
            normalize_missing(entry.get("entry_type")) or "class"
        )
        entry["subject"] = (
            normalize_missing(entry.get("subject"))
            or normalize_missing(entry.get("course"))
        )
        entry["teacher_name"] = normalize_missing(
            entry.get("teacher_name")
        )
        entry["room"] = normalize_missing(entry.get("room"))
        entry["class_name"] = (
            normalize_missing(entry.get("class_name"))
            or derive_class_name(
                entry.get("semester"), entry.get("section")
            )
        )

        missing = [
            field for field in REQUIRED_FIELDS
            if normalize_missing(entry.get(field)) is None
        ]
        if not entry.get("class_name"):
            missing.append("class_name")

        entry_type = str(entry["entry_type"]).casefold()
        is_activity = entry_type in {"activity", "break"}
        original_room = record.get("room")
        entry["original_room_text"] = original_room

        if is_activity:
            entry["teacher_name"] = None
            entry["teachers"] = []
            entry["room"] = None
            if missing:
                results.append({
                    "record_number": index,
                    "status": "needs_review",
                    "reason": f"Missing fields: {', '.join(sorted(missing))}",
                })
            else:
                prepared.append((index, entry))
            continue

        teachers = entry.get("teachers")
        if isinstance(teachers, (str, dict)):
            teachers = [teachers]
        elif teachers is not None:
            teachers = list(teachers)
        else:
            teachers = []
        valid_teachers = []
        for teacher in teachers:
            if isinstance(teacher, dict):
                teacher_name = normalize_missing(
                    teacher.get("teacher_name")
                    or teacher.get("name")
                    or teacher.get("initials")
                )
                if teacher_name is not None:
                    valid_teachers.append({
                        **teacher,
                        "teacher_name": teacher_name,
                        "initials": normalize_missing(
                            teacher.get("initials")
                        ),
                        "role": normalize_missing(teacher.get("role")),
                    })
            else:
                teacher_name = normalize_missing(teacher)
                if teacher_name is not None:
                    valid_teachers.append(teacher_name)
        entry["teachers"] = valid_teachers

        if entry["teacher_name"] is None and not valid_teachers:
            missing.append("teacher_name")

        if missing:
            results.append({
                "record_number": index,
                "status": "needs_review",
                "reason": f"Missing fields: {', '.join(sorted(missing))}",
            })
            continue

        resolved_room = normalize_missing(
            resolve_room(
                cell_room=entry["room"],
                timetable_default_room=normalize_missing(default_room),
            )
        )

        if not resolved_room:
            results.append({
                "record_number": index,
                "status": "needs_review",
                "reason": "No room found in record or timetable header",
            })
            continue

        entry["original_room_text"] = original_room
        entry["room"] = resolved_room
        entry.setdefault("review_status", "needs_review")
        prepared.append((index, entry))

    # Do not replace existing data if any record is invalid.
    if results:
        return results + [{
            "status": "not_imported",
            "reason": "Replacement cancelled because records need review.",
        }]

    imported_results = []

    try:
        with get_connection() as connection:
            connection.execute("BEGIN")

            connection.execute(
                "DELETE FROM timetable_entries WHERE timetable_id = ?",
                (timetable_id,),
            )

            for index, entry in prepared:
                entry_id = insert_entry(entry, connection=connection)

                imported_results.append({
                    "record_number": index,
                    "status": "imported",
                    "entry_id": entry_id,
                    "room": entry["room"],
                    "semester": semester,
                    "section": section,
                    "timetable_id": timetable_id,
                })

    except (ValueError, TypeError, sqlite3.Error) as exc:
        return [{
            "status": "not_imported",
            "reason": str(exc),
        }]

    return imported_results
