
import sqlite3

from app.database.db import get_connection, insert_entry
from app.ingestion.room_resolver import resolve_room


REQUIRED_FIELDS = {
    "teacher_name",
    "subject",
    "class_name",
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

    prepared = []
    results = []

    # Validate every record before deleting existing entries.
    for index, record in enumerate(records, start=1):
        entry = record.copy()
        entry["semester"] = semester
        entry["section"] = section
        entry["timetable_id"] = timetable_id
        entry["source_document"] = (
            entry.get("source_document") or source_document
        )

        missing = [
            field for field in REQUIRED_FIELDS
            if not entry.get(field)
        ]

        if missing:
            results.append({
                "record_number": index,
                "status": "needs_review",
                "reason": f"Missing fields: {', '.join(sorted(missing))}",
            })
            continue

        original_room = entry.get("room")
        resolved_room = resolve_room(
            cell_room=original_room,
            timetable_default_room=default_room,
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
