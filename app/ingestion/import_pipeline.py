"""Safe handoff from GenAI timetable extraction to the database importer."""

from app.ingestion.extraction_pipeline import run_extraction_pipeline
from app.ingestion.importer import import_timetable_records


REQUIRED_FIELDS = (
    "day",
    "start_time",
    "end_time",
    "subject",
    "teacher_name",
)

UNKNOWN_VALUES = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL"}


def _usable(value):
    return value is not None and str(value).strip().upper() not in UNKNOWN_VALUES


def validate_import_record(record):
    """Return fields that are missing or unresolved for database insertion."""
    return [field for field in REQUIRED_FIELDS if not _usable(record.get(field))]


def run_import_pipeline(
    image_path,
    llm_client,
    timetable_id,
    approved_record_indices=None,
    metadata=None,
    default_room=None,
    semester=None,
    section=None,
    tesseract_cmd=None,
):
    """
    Extract records and import only explicitly approved, complete records.

    Importer replacement is all-or-nothing: a partial approval must not erase
    previously imported rows from the same timetable.
    """
    if not timetable_id or not str(timetable_id).strip():
        raise ValueError("A timetable_id is required.")

    extraction = run_extraction_pipeline(
        image_path=image_path,
        llm_client=llm_client,
        metadata=metadata,
        tesseract_cmd=tesseract_cmd,
    )

    records = extraction.get("records", [])
    if not isinstance(records, list):
        raise ValueError("Extraction returned an invalid records collection.")

    approved_indices = set(approved_record_indices or [])
    invalid_indices = []
    pending_indices = []
    approved_records = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            invalid_indices.append(index)
            continue

        missing = validate_import_record(record)
        # Room may be UNKNOWN only if a real header default is available.
        if not _usable(record.get("room")) and not _usable(default_room):
            missing.append("room/default_room")

        if missing:
            invalid_indices.append({"index": index, "missing_fields": missing})
            continue

        if index not in approved_indices:
            pending_indices.append(index)
            continue

        approved_record = dict(record)
        approved_record["review_status"] = "verified"
        approved_record["class_name"] = (
            f"Semester {semester} Section {section}"
            if semester is not None and section is not None
            else approved_record.get("class_name")
        )
        approved_records.append(approved_record)

    invalid_approval_indices = sorted(
        index for index in approved_indices
        if not isinstance(index, int) or index < 0 or index >= len(records)
    )

    # The importer replaces a timetable, so only a complete approval set is safe.
    valid_indices = {
        index for index, record in enumerate(records)
        if isinstance(record, dict) and not validate_import_record(record)
        and (_usable(record.get("room")) or _usable(default_room))
    }
    if not approved_records:
        return {
            "status": "nothing_imported",
            "timetable_id": timetable_id,
            "extracted_count": len(records),
            "imported_count": 0,
            "pending_review_indices": pending_indices,
            "invalid_records": invalid_indices,
            "invalid_approval_indices": invalid_approval_indices,
            "import_results": [],
            "message": "No approved valid records. Existing timetable data was left unchanged.",
        }

    if invalid_indices or approved_indices.intersection(valid_indices) != valid_indices:
        return {
            "status": "awaiting_complete_approval",
            "timetable_id": timetable_id,
            "extracted_count": len(records),
            "approved_count": len(approved_records),
            "imported_count": 0,
            "pending_review_indices": pending_indices,
            "invalid_records": invalid_indices,
            "invalid_approval_indices": invalid_approval_indices,
            "import_results": [],
            "message": (
                "The importer replaces the whole timetable. Approve every "
                "valid extracted record and resolve invalid records before import."
            ),
        }

    import_results = import_timetable_records(
        records=approved_records,
        default_room=default_room,
        semester=semester,
        section=section,
        timetable_id=timetable_id,
        source_document=extraction.get("source_document"),
    )

    imported_count = sum(
        1 for result in import_results
        if isinstance(result, dict) and result.get("status") == "imported"
    )
    status = "import_completed" if imported_count == len(approved_records) else "import_failed"

    return {
        "status": status,
        "timetable_id": timetable_id,
        "extracted_count": len(records),
        "approved_count": len(approved_records),
        "imported_count": imported_count,
        "pending_review_indices": pending_indices,
        "invalid_records": invalid_indices,
        "invalid_approval_indices": invalid_approval_indices,
        "import_results": import_results,
        "message": (
            "All approved records were imported."
            if status == "import_completed"
            else "Import failed; check import_results. Do not assume data was imported."
        ),
    }
