"""Safe, transactional timetable import."""

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

UNKNOWN_VALUES = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL"}


def _clean_value(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned.upper() in UNKNOWN_VALUES:
        return None
    return cleaned


def _derive_class_name(semester, section):
    if semester is None or section is None:
        return None
    return f"Semester {semester} Section {section}"


def import_timetable_records(
    records,
    default_room=None,
    semester=None,
    section=None,
    timetable_id=None,
    source_document=None,
):
    """Replace one timetable's records safely after validating all records."""
    if not timetable_id:
        raise ValueError("timetable_id is required for safe replacement")

    records = list(records or [])
    if not records:
        return [{
            "status": "not_imported",
            "reason": (
                "No records supplied. Existing timetable data was left unchanged."
            ),
        }]

    prepared = []
    results = []

    for index, record in enumerate(records, start=1):
        entry = dict(record)
        entry["semester"] = semester
        entry["section"] = section
        entry["timetable_id"] = timetable_id
        entry["source_document"] = entry.get("source_document") or source_document

        # The class label is deterministic metadata, not an LLM extraction.
        entry["class_name"] = (
            _clean_value(entry.get("class_name"))
            or _derive_class_name(semester, section)
        )

        # Preserve raw OCR room evidence separately. UNKNOWN is not a room.
        original_room = entry.get("original_room_text")
        if original_room is None:
            original_room = entry.get("room")
        entry["original_room_text"] = original_room

        for field in ("teacher_name", "subject", "class_name", "day",
                      "start_time", "end_time"):
            entry[field] = _clean_value(entry.get(field))

        missing = [field for field in REQUIRED_FIELDS if not entry.get(field)]
        if missing:
            results.append({
                "record_number": index,
                "status": "needs_review",
                "reason": f"Missing or unresolved fields: {', '.join(sorted(missing))}",
            })
            continue

        cell_room = _clean_value(entry.get("room"))
        resolved_room = resolve_room(
            cell_room=cell_room,
            timetable_default_room=_clean_value(default_room),
        )
        if not resolved_room:
            results.append({
                "record_number": index,
                "status": "needs_review",
                "reason": "No usable room in record or timetable header",
            })
            continue

        entry["room"] = resolved_room
        entry.setdefault("review_status", "needs_review")
        prepared.append((index, entry))

    # Never delete existing data unless every supplied record is importable.
    if results or len(prepared) != len(records):
        return results + [{
            "status": "not_imported",
            "reason": "Replacement cancelled because one or more records need review.",
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
        return [{"status": "not_imported", "reason": str(exc)}]

    return imported_results
