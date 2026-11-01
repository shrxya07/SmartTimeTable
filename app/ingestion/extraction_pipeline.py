
"""End-to-end timetable image extraction: OCR -> GenAI -> provenance."""

from pathlib import Path

from app.ingestion.document_processor import process_timetable_image
from app.ingestion.document_intelligence import interpret_timetable_document


def run_extraction_pipeline(
    image_path,
    llm_client,
    metadata=None,
    tesseract_cmd=None,
):
    """
    Extract timetable records from an image.

    llm_client must have the provider-independent interface:
        llm_client(system_prompt, user_prompt) -> JSON string or dict

    Extracted records remain needs_review until independently checked.
    """
    metadata = dict(metadata or {})
    image_path = Path(image_path)

    # Step 1: OCR and visual evidence
    ocr_evidence = process_timetable_image(
        image_path=image_path,
        tesseract_cmd=tesseract_cmd,
    )

    source_document = metadata.get(
        "source_document", image_path.name
    )
    source_page = metadata.get("source_page")

    # Do not ask the LLM to interpret an image with no OCR evidence.
    if not ocr_evidence.get("text", "").strip():
        return {
            "status": "needs_review",
            "source_document": source_document,
            "source_page": source_page,
            "ocr_confidence": ocr_evidence.get("ocr_confidence", 0.0),
            "ocr_evidence": ocr_evidence,
            "records": [],
            "message": "No readable OCR text was detected.",
        }

    # Step 2: GenAI semantic interpretation
    extraction_metadata = {
        **metadata,
        "source_document": source_document,
        "source_page": source_page,
    }

    extraction = interpret_timetable_document(
        ocr_evidence=ocr_evidence,
        llm_client=llm_client,
        metadata=extraction_metadata,
    )

    if not isinstance(extraction, dict):
        raise ValueError("GenAI extraction must return a dictionary.")

    records = extraction.get("records")
    if not isinstance(records, list):
        raise ValueError("GenAI extraction did not return a records list.")

    # Step 3: Attach provenance and keep confidence sources separate.
    ocr_confidence = ocr_evidence.get("ocr_confidence", 0.0)
    enriched_records = []

    for record in records:
        if not isinstance(record, dict):
            continue

        enriched = dict(record)
        enriched["source_document"] = source_document

        if source_page is not None:
            enriched["source_page"] = source_page

        # These are separate measurements; do not combine them.
        enriched["ocr_confidence"] = ocr_confidence
        enriched["extraction_method"] = "ocr_plus_genai"

        # Model confidence alone is not sufficient to verify a timetable.
        enriched["review_status"] = "needs_review"

        enriched_records.append(enriched)

    return {
        "status": "extraction_completed",
        "source_document": source_document,
        "source_page": source_page,
        "ocr_confidence": ocr_confidence,
        "ocr_evidence": ocr_evidence,
        "records": enriched_records,
        "extraction_metadata": extraction.get("metadata", {}),
    }
