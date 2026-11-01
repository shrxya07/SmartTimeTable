
"""Safe handoff from GenAI timetable extraction to the database importer."""

from app.ingestion.extraction_pipeline import run_extraction_pipeline
from app.ingestion.importer import import_timetable_records


REQUIRED_FIELDS = (
    "day",
    "start_time",
    "end_time",
    "subject",
    "teacher_name",
    "room",
)


def validate_import_record(record):
    """Return missing required fields for a proposed database record."""
    return [
        field
        for field in REQUIRED_FIELDS
        if not str(record.get(field, "")).strip()
    ]


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
    Extract records and import only explicitly approved records.

    approved_record_indices refers to zero-based positions in the extracted
    records list. No records are approved by default.
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
        if missing:
            invalid_indices.append({
                "index": index,
                "missing_fields": missing,
            })
            continue

        if index not in approved_indices:
            pending_indices.append(index)
            continue

        # Approval is an explicit caller action, not an LLM confidence result.
        approved_record = dict(record)
        approved_record["review_status"] = "verified"
        approved_records.append(approved_record)

    # Reject approval indices that do not identify an extracted record.
    invalid_approval_indices = sorted(
        index for index in approved_indices
        if not isinstance(index, int) or index < 0 or index >= len(records)
    )

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
            "message": (
                "No approved valid records were supplied. "
                "Existing timetable data was left unchanged."
            ),
        }

    source_document = extraction.get("source_document")

    import_results = import_timetable_records(
        records=approved_records,
        default_room=default_room,
        semester=semester,
        section=section,
        timetable_id=timetable_id,
        source_document=source_document,
    )

    imported_count = sum(
        1 for result in import_results
        if isinstance(result, dict)
        and result.get("status") in ("imported", "success")
    )

    return {
        "status": "import_completed",
        "timetable_id": timetable_id,
        "extracted_count": len(records),
        "approved_count": len(approved_records),
        "imported_count": imported_count,
        "pending_review_indices": pending_indices,
        "invalid_records": invalid_indices,
        "invalid_approval_indices": invalid_approval_indices,
        "import_results": import_results,
    }
