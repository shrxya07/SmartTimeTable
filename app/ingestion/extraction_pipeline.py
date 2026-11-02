"""Timetable extraction: vision model first, deterministic checks second.

Flow per page: render (image or PDF page) -> vision model -> optional OCR
cross-check -> ``timetable_validator.build_records``. No record is approved
here; everything is returned with ``review_status = needs_review``.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile

from app.ingestion.timetable_validator import build_records
from app.ingestion.vision_extractor import extract_page, render_pages


MAX_PARALLEL_PAGES = 3


def _ocr_text(image_bytes, mime_type, tesseract_cmd):
    """OCR text of a page for cross-checking, or None if OCR is unavailable."""
    try:
        from app.ingestion.document_processor import process_timetable_image
    except ImportError:
        return None

    suffix = ".png" if mime_type == "image/png" else ".jpg"
    if mime_type == "image/webp":
        suffix = ".webp"
    with tempfile.TemporaryDirectory(prefix="smart-timetable-ocr-") as directory:
        page_path = Path(directory) / f"page{suffix}"
        page_path.write_bytes(image_bytes)
        try:
            evidence = process_timetable_image(
                page_path,
                tesseract_cmd=tesseract_cmd,
            )
        except Exception:
            # Tesseract missing or unreadable page: skip the cross-check.
            return None
    return evidence.get("text") or None


def _extract_one(page, vision_client, metadata, source_document,
                 tesseract_cmd, ocr_check):
    page_number, image_bytes, mime_type = page
    extracted = extract_page(image_bytes, mime_type, vision_client)
    ocr_text = (
        _ocr_text(image_bytes, mime_type, tesseract_cmd) if ocr_check else None
    )
    result = build_records(
        extracted,
        metadata=metadata,
        ocr_text=ocr_text,
        source_document=source_document,
        source_page=page_number,
    )
    result["source_page"] = page_number
    return result


def run_extraction_pipeline(
    image_path,
    vision_client,
    metadata=None,
    tesseract_cmd=None,
    ocr_check=True,
):
    """Extract an image or PDF timetable without approving any records.

    ``vision_client(image_bytes, mime_type, prompt, response_schema)`` is the
    injected vision model. When it is None (no API key), nothing is extracted
    and the result says why.
    """
    metadata = dict(metadata or {})
    source_path = Path(image_path)
    source_document = metadata.get("source_document") or source_path.name
    pages = render_pages(source_path)

    base = {
        "source_document": source_document,
        "page_count": len(pages),
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
    }
    if not callable(vision_client):
        return {
            **base,
            "status": "ai_unavailable",
            "records": [],
            "pages": [],
            "header": {},
            "legend": [],
            "course_offerings": [],
            "unresolved_items": [],
            "document_uncertainties": [],
            "message": (
                "Timetable extraction needs Gemini. Configure GEMINI_API_KEY "
                "and restart the application."
            ),
        }

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_PAGES) as pool:
        page_results = list(pool.map(
            lambda page: _extract_one(
                page, vision_client, metadata, source_document,
                tesseract_cmd, ocr_check,
            ),
            pages,
        ))

    records = [r for page in page_results for r in page["records"]]
    header = next(
        (page["header"] for page in page_results if page["header"].get("class_name")),
        page_results[0]["header"] if page_results else {},
    )
    result = {
        **base,
        "status": "extraction_completed" if records else "needs_review",
        "records": records,
        "pages": page_results,
        "header": header,
        "legend": [row for page in page_results for row in page["legend"]],
        "course_offerings": [
            row for page in page_results for row in page["course_offerings"]
        ],
        "unresolved_items": [
            item for page in page_results for item in page["unresolved_items"]
        ],
        "document_uncertainties": [
            item for page in page_results for item in page["document_uncertainties"]
        ],
        "ocr_checked": all(page["ocr_checked"] for page in page_results),
        "auto_count": sum(r["review_tier"] == "auto" for r in records),
    }
    if not records:
        result["message"] = "No timetable entries were found in the document."
    return result
