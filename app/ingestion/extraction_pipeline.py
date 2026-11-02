"""Timetable extraction orchestration with deterministic resolution first."""

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import tempfile

from app.ingestion.document_processor import process_timetable_image
from app.ingestion.document_intelligence import (
    interpret_timetable_document,
    interpret_unresolved_lines,
    validate_against_evidence,
)
from app.ingestion.cell_extractor import extract_cells_from_evidence
from app.ingestion.dynamic_grid import detect_grid_geometry
from app.ingestion.layout_regions import locate_regions
from app.ingestion.legend_extractor import extract_legend
from app.ingestion.reference_resolver import (
    extract_reference_table,
    resolve_records_with_reference,
)
from app.ingestion.timetable_resolver import resolve_slots


def _plain_offering(offering):
    if is_dataclass(offering):
        return asdict(offering)
    if isinstance(offering, dict):
        return dict(offering)
    return offering


def _record_confidence(cell, page_confidence):
    values = []
    for word in cell.get("words", []) or []:
        try:
            confidence = float(word.get("confidence"))
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 <= confidence <= 1:
            values.append(confidence)
    if values:
        return round(sum(values) / len(values), 4)
    return page_confidence


def _enrich_provenance(record, source_document, source_page, ocr_confidence):
    enriched = dict(record)
    enriched["source_document"] = source_document
    enriched["source_page"] = source_page
    enriched.setdefault("source_bbox", None)
    enriched.setdefault(
        "original_text",
        enriched.get("evidence", ""),
    )
    enriched.setdefault("evidence", enriched.get("original_text", ""))
    enriched["ocr_confidence"] = ocr_confidence
    enriched["review_status"] = "needs_review"
    enriched["verified"] = False
    enriched["approved"] = False

    if enriched.get("source_bbox") is None:
        enriched["review_tier"] = "check"
        unresolved_fields = dict(enriched.get("unresolved_fields") or {})
        unresolved_fields.setdefault(
            "source_bbox",
            {
                "status": "unresolved",
                "reason": "Original timetable cell bounds unavailable",
            },
        )
        enriched["unresolved_fields"] = unresolved_fields
    return enriched


def _legacy_fallback(
    ocr_evidence,
    llm_client,
    metadata,
    source_document,
    source_page,
    failure,
    reference_table=None,
):
    if not callable(llm_client):
        return {
            "status": "needs_review",
            "source_document": source_document,
            "source_page": source_page,
            "ocr_confidence": ocr_evidence.get("ocr_confidence", 0.0),
            "ocr_evidence": ocr_evidence,
            "records": [],
            "deterministic_count": 0,
            "deterministic_auto_count": 0,
            "gemini_fallback_count": 0,
            "gemini_fallback_candidate_count": 0,
            "gemini_fallback_input_count": 0,
            "gemini_full_document_fallback_count": 0,
            "reference_table": reference_table or {"found": False, "courses": []},
            "reference_table_found": bool(
                (reference_table or {}).get("found", False)
            ),
            "message": (
                f"Deterministic extraction failed ({failure}); "
                "no LLM client was supplied, so no records were generated."
            ),
            "fallback_method": None,
            "extraction_error": failure,
        }

    extraction_metadata = {
        **metadata,
        "source_document": source_document,
        "source_page": source_page,
        "deterministic_extraction_error": failure,
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

    if reference_table is None:
        reference_table = extract_reference_table(
            ocr_evidence.get("words", [])
        )
    records = resolve_records_with_reference(records, reference_table)
    checked = validate_against_evidence(
        {"records": records},
        ocr_evidence.get("text", ""),
        reference_table,
    )
    enriched_records = []
    for record in checked["records"]:
        item = _enrich_provenance(
            record,
            source_document,
            source_page,
            ocr_evidence.get("ocr_confidence"),
        )
        item["resolution_method"] = "gemini_full_document_fallback"
        item["review_tier"] = "check"
        item["unresolved_fields"] = dict(item.get("unresolved_fields") or {})
        item["unresolved_fields"].setdefault(
            "source_bbox",
            {
                "status": "unresolved",
                "reason": "Full-document fallback has no cell geometry",
            },
        )
        enriched_records.append(item)

    return {
        "status": "extraction_completed" if enriched_records else "needs_review",
        "source_document": source_document,
        "source_page": source_page,
        "ocr_confidence": ocr_evidence.get("ocr_confidence", 0.0),
        "ocr_evidence": ocr_evidence,
        "records": enriched_records,
        "deterministic_count": 0,
        "deterministic_auto_count": 0,
        "gemini_fallback_count": 0,
        "gemini_fallback_candidate_count": 0,
        "gemini_fallback_input_count": 0,
        "gemini_full_document_fallback_count": len(enriched_records),
        "reference_table": reference_table,
        "reference_table_found": reference_table.get("found", False),
        "extraction_metadata": extraction.get("metadata", {}),
        "extraction_error": failure,
        "fallback_method": "gemini_full_document",
        "unresolved_items": [{
            "reason": "deterministic_extraction_failed",
            "detail": failure,
        }],
        **(
            {"message": "The full-document fallback returned no records."}
            if not enriched_records
            else {}
        ),
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
    }


def _extract_image_page(
    image_path,
    llm_client,
    metadata,
    source_document,
    source_page,
    tesseract_cmd,
):
    ocr_evidence = process_timetable_image(
        image_path=image_path,
        tesseract_cmd=tesseract_cmd,
    )
    ocr_evidence["source_document"] = source_document
    ocr_evidence["source_page"] = source_page
    for word in ocr_evidence.get("words", []):
        if isinstance(word, dict):
            word["source_page"] = source_page

    reference_table = extract_reference_table(ocr_evidence.get("words", []))
    ocr_evidence["reference_table"] = reference_table
    page_confidence = ocr_evidence.get("ocr_confidence", 0.0)

    if not ocr_evidence.get("text", "").strip():
        return {
            "status": "needs_review",
            "source_document": source_document,
            "source_page": source_page,
            "ocr_confidence": page_confidence,
            "ocr_evidence": ocr_evidence,
            "records": [],
            "deterministic_count": 0,
            "deterministic_auto_count": 0,
            "gemini_fallback_count": 0,
            "gemini_fallback_candidate_count": 0,
            "gemini_fallback_input_count": 0,
            "gemini_full_document_fallback_count": 0,
            "reference_table": reference_table,
            "reference_table_found": reference_table.get("found", False),
            "message": "No readable OCR text was detected.",
            "unresolved_items": [],
        }

    try:
        geometry = detect_grid_geometry(image_path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _legacy_fallback(
            ocr_evidence,
            llm_client,
            metadata,
            source_document,
            source_page,
            f"grid geometry detection failed: {exc}",
            reference_table,
        )

    try:
        regions = locate_regions(ocr_evidence, geometry)
    except (OSError, TypeError, ValueError) as exc:
        return _legacy_fallback(
            ocr_evidence,
            llm_client,
            metadata,
            source_document,
            source_page,
            f"region detection failed: {exc}",
            reference_table,
        )
    ocr_evidence["regions"] = regions
    ocr_evidence["grid_geometry"] = geometry

    if (
        regions.get("grid", {}).get("status") != "located"
        or not regions.get("grid", {}).get("bbox")
    ):
        failure = regions.get("grid", {}).get("reason") or (
            "grid region was not located"
        )
        return _legacy_fallback(
            ocr_evidence,
            llm_client,
            metadata,
            source_document,
            source_page,
            f"region detection failed: {failure}",
            reference_table,
        )

    try:
        cell_evidence = extract_cells_from_evidence(
            image_path=image_path,
            ocr_evidence=ocr_evidence,
            grid_geometry=geometry,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        cell_evidence = {
            "cells": [],
            "merged_candidates": [],
            "error": str(exc),
            "review_status": "needs_review",
        }
    ocr_evidence["cell_evidence"] = cell_evidence

    if cell_evidence.get("error"):
        return _legacy_fallback(
            ocr_evidence,
            llm_client,
            metadata,
            source_document,
            source_page,
            f"slot extraction failed: {cell_evidence['error']}",
            reference_table,
        )

    cells = cell_evidence.get("cells")
    if not isinstance(cells, list) or not cells:
        return _legacy_fallback(
            ocr_evidence,
            llm_client,
            metadata,
            source_document,
            source_page,
            "slot extraction returned no timetable cells",
            reference_table,
        )

    header = {
        **regions.get("header", {}).get("metadata", {}),
        **metadata,
        "source_document": source_document,
        "source_page": source_page,
    }
    for cell in cells:
        cell["source_document"] = source_document
        cell["source_page"] = source_page

    legend_region = regions.get("legend", {})
    legend_failure = None
    if legend_region.get("status") != "located" or not legend_region.get("bbox"):
        legend = {
            "status": "unresolved",
            "review_status": "needs_review",
            "offerings": [],
            "unresolved": [{
                "reason": legend_region.get("reason")
                or "legend region was not located",
                "source_bbox": legend_region.get("bbox"),
            }],
        }
        legend_failure = (
            legend_region.get("reason") or "legend region was not located"
        )
    else:
        try:
            legend = extract_legend(
                image_path,
                ocr_evidence,
                legend_region["bbox"],
                llm_client=None,
            )
            if legend.get("status") == "unresolved":
                legend_failure = "; ".join(
                    item.get("reason", "legend unresolved")
                    for item in legend.get("unresolved", [])
                    if isinstance(item, dict)
                ) or "legend extraction returned unresolved"
        except (OSError, TypeError, ValueError) as exc:
            legend_failure = str(exc)
            legend = {
                "status": "unresolved",
                "review_status": "needs_review",
                "offerings": [],
                "unresolved": [{
                    "reason": "legend_extraction_failed",
                    "detail": legend_failure,
                    "source_bbox": legend_region.get("bbox"),
                }],
            }

    ocr_evidence["legend"] = {
        "status": legend.get("status"),
        "unresolved": legend.get("unresolved", []),
    }
    resolution = resolve_slots(
        cells,
        legend.get("offerings", []),
        header,
    )
    records = []
    cell_by_bbox = {
        json.dumps(cell.get("source_bbox"), sort_keys=True): cell
        for cell in cells
        if cell.get("source_bbox") is not None
    }
    for record in resolution.get("entries", []):
        bbox_key = json.dumps(record.get("source_bbox"), sort_keys=True)
        cell = cell_by_bbox.get(bbox_key, {})
        item = _enrich_provenance(
            record,
            source_document,
            source_page,
            _record_confidence(cell, page_confidence),
        )
        item["original_text"] = item.get("evidence", "")
        if item.get("legend_source_bbox") is None and item.get("entry_type") not in {
            "activity",
            "break",
        }:
            item["review_tier"] = "check"
            unresolved_fields = dict(item.get("unresolved_fields") or {})
            unresolved_fields.setdefault(
                "legend_source_bbox",
                {
                    "status": "unresolved",
                    "reason": "Matched legend row bounds unavailable",
                },
            )
            item["unresolved_fields"] = unresolved_fields
        records.append(item)

    unresolved_items = list(resolution.get("unresolved_items", []))
    unresolved_items.extend(legend.get("unresolved", []))
    if legend_failure:
        unresolved_items.append({
            "reason": "legend_extraction_failed",
            "detail": legend_failure,
            "source_bbox": legend_region.get("bbox"),
        })

    gemini_interpretations = []
    unresolved_lines = resolution.get("unresolved_items", [])
    if unresolved_lines and callable(llm_client):
        fallback_lines = []
        for item in unresolved_lines:
            bbox_key = json.dumps(item.get("source_bbox"), sort_keys=True)
            cell = cell_by_bbox.get(bbox_key, {})
            line_text = item.get("line") or item.get("evidence") or ""
            fallback_lines.append({
                "text": line_text,
                "original_text": line_text,
                "line": line_text,
                "source_bbox": item.get("source_bbox"),
                "day": cell.get("day"),
                "start_time": cell.get("start_time"),
                "end_time": cell.get("end_time"),
                "resolution_status": "unresolved",
            })

        legend_vocab = [
            _plain_offering(item)
            for item in legend.get("offerings", [])
        ]
        fallback = interpret_unresolved_lines(
            fallback_lines,
            legend_vocab,
            llm_client,
        )
        fallback_evidence = " ".join(
            [
                ocr_evidence.get("text", ""),
                *(
                    f"{line.get('day', '')} {line.get('start_time', '')} "
                    f"{line.get('end_time', '')}"
                    for line in fallback_lines
                ),
            ]
        )
        checked_fallback = validate_against_evidence(
            fallback,
            fallback_evidence,
            legend_vocab,
        )
        for interpretation in checked_fallback["interpretations"]:
            line_index = interpretation.get("line_index")
            source = (
                fallback_lines[line_index]
                if isinstance(line_index, int)
                and 0 <= line_index < len(fallback_lines)
                else {}
            )
            source_cell = cell_by_bbox.get(
                json.dumps(source.get("source_bbox"), sort_keys=True),
                {},
            )
            interpretation["source_bbox"] = source.get("source_bbox")
            enriched = _enrich_provenance(
                interpretation,
                source_document,
                source_page,
                _record_confidence(source_cell, page_confidence),
            )
            for field in ("day", "start_time", "end_time"):
                if not enriched.get(field) and source.get(field):
                    enriched[field] = source[field]
            enriched["source_bbox"] = source.get("source_bbox")
            enriched["legend_source_bbox"] = None
            enriched["resolution_method"] = "gemini_fallback"
            enriched["review_tier"] = "check"
            enriched["original_text"] = source.get("original_text", "")
            enriched["evidence"] = source.get("original_text", "")
            enriched["unresolved_fields"] = dict(
                enriched.get("unresolved_fields") or {}
            )
            if source_cell.get("source_bbox") is None:
                enriched["unresolved_fields"].setdefault(
                    "source_bbox",
                    {
                        "status": "unresolved",
                        "reason": "Original timetable cell bounds unavailable",
                    },
                )
            records.append(enriched)
            gemini_interpretations.append(enriched)
        unresolved_items.extend(fallback.get("unresolved_lines", []))
    elif unresolved_lines:
        for unresolved in unresolved_lines:
            bbox_key = json.dumps(
                unresolved.get("source_bbox"),
                sort_keys=True,
            )
            cell = cell_by_bbox.get(bbox_key, {})
            text = unresolved.get("line") or unresolved.get("evidence") or ""
            unresolved_record = _enrich_provenance(
                {
                    "day": cell.get("day"),
                    "start_time": cell.get("start_time"),
                    "end_time": cell.get("end_time"),
                    "subject": None,
                    "course": None,
                    "teacher_name": None,
                    "teachers": [],
                    "teacher_initials": None,
                    "room": None,
                    "entry_type": "class",
                    "source_bbox": unresolved.get("source_bbox"),
                    "original_text": text,
                    "evidence": text,
                    "unresolved_fields": {
                        "course": {
                            "status": "unresolved",
                            "reason": unresolved.get("reason"),
                            "proposed_value": text,
                        },
                        "teacher_name": {
                            "status": "unresolved",
                            "reason": "No verified course offering matched",
                        },
                        "room": {
                            "status": "unresolved",
                            "reason": "No verified course offering matched",
                        },
                    },
                },
                source_document,
                source_page,
                _record_confidence(cell, page_confidence),
            )
            unresolved_record["resolution_method"] = "unresolved_deterministic"
            unresolved_record["review_tier"] = "check"
            records.append(unresolved_record)

    course_offerings = [
        _plain_offering(item) for item in legend.get("offerings", [])
    ]
    return {
        "status": "extraction_completed",
        "source_document": source_document,
        "source_page": source_page,
        "ocr_confidence": page_confidence,
        "ocr_evidence": ocr_evidence,
        "records": records,
        "reference_table": reference_table,
        "reference_table_found": reference_table.get("found", False),
        "extraction_metadata": {},
        "regions": regions,
        "slot_evidence": cell_evidence,
        "legend": {
            **{
                key: value
                for key, value in legend.items()
                if key != "offerings"
            },
            "offerings": course_offerings,
        },
        "course_offerings": course_offerings,
        "unresolved_items": unresolved_items,
        "resolution_completeness": resolution.get("completeness", {}),
        "gemini_fallback_count": sum(
            not item.get("unresolved_fields")
            and not item.get("uncertainties")
            for item in gemini_interpretations
        ),
        "gemini_fallback_candidate_count": len(gemini_interpretations),
        "gemini_fallback_input_count": (
            len(unresolved_lines) if callable(llm_client) else 0
        ),
        "gemini_full_document_fallback_count": 0,
        "deterministic_count": len(
            resolution.get("entries", [])
        ),
        "deterministic_auto_count": sum(
            entry.get("review_tier") == "auto"
            for entry in resolution.get("entries", [])
        ),
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
        **(
            {"legend_error": legend_failure}
            if legend_failure
            else {}
        ),
    }


def _run_pdf_pipeline(
    pdf_path,
    llm_client,
    metadata,
    tesseract_cmd,
):
    import pymupdf

    source_document = metadata.get("source_document", pdf_path.name)
    pages = []
    with pymupdf.open(pdf_path) as document:
        if document.page_count == 0:
            return {
                "status": "needs_review",
                "source_document": source_document,
                "source_page": None,
                "ocr_confidence": 0.0,
                "ocr_evidence": {
                    "document_type": "pdf",
                    "pages": [],
                    "text": "",
                    "words": [],
                },
                "records": [],
                "reference_table": {"found": False, "courses": []},
                "reference_table_found": False,
                "pages": [],
                "message": "The PDF contains no pages.",
            }

        with tempfile.TemporaryDirectory(
            prefix="smart-timetable-pdf-"
        ) as directory:
            for page_index, page in enumerate(document, start=1):
                page_path = Path(directory) / f"page-{page_index}.png"
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                pixmap.save(str(page_path))
                page_metadata = {
                    **metadata,
                    "source_document": source_document,
                    "source_page": page_index,
                }
                pages.append(_extract_image_page(
                    page_path,
                    llm_client,
                    page_metadata,
                    source_document,
                    page_index,
                    tesseract_cmd,
                ))

    words = []
    texts = []
    for result in pages:
        evidence = result.get("ocr_evidence", {})
        texts.append(evidence.get("text", ""))
        words.extend(evidence.get("words", []))
    combined_evidence = {
        "document_type": "pdf",
        "source_document": source_document,
        "source_path": str(pdf_path),
        "page_numbering": "one-based",
        "pages": [
            result.get("ocr_evidence", {}) for result in pages
        ],
        "text": "\n".join(text for text in texts if text),
        "words": words,
        "word_count": len(words),
        "ocr_confidence": (
            round(
                sum(
                    result.get("ocr_confidence", 0.0)
                    for result in pages
                ) / len(pages),
                4,
            )
            if pages
            else 0.0
        ),
    }
    all_records = [
        record
        for result in pages
        for record in result.get("records", [])
    ]
    first_reference = next(
        (
            result.get("reference_table")
            for result in pages
            if result.get("reference_table") is not None
        ),
        {"found": False, "courses": []},
    )
    errors = [
        result.get("message") or result.get("legend_error")
        for result in pages
        if result.get("message") or result.get("legend_error")
    ]
    return {
        "status": (
            "extraction_completed"
            if any(
                result.get("status") == "extraction_completed"
                for result in pages
            )
            else "needs_review"
        ),
        "source_document": source_document,
        "source_page": None,
        "ocr_confidence": combined_evidence["ocr_confidence"],
        "ocr_evidence": combined_evidence,
        "records": all_records,
        "reference_table": first_reference,
        "reference_table_found": any(
            result.get("reference_table_found", False)
            for result in pages
        ),
        "pages": pages,
        "extraction_metadata": {
            "page_count": len(pages),
            "page_numbering": "one-based",
        },
        "unresolved_items": [
            item
            for result in pages
            for item in result.get("unresolved_items", [])
        ],
        "deterministic_count": sum(
            result.get("deterministic_count", 0) for result in pages
        ),
        "deterministic_auto_count": sum(
            result.get("deterministic_auto_count", 0) for result in pages
        ),
        "gemini_fallback_count": sum(
            result.get("gemini_fallback_count", 0) for result in pages
        ),
        "gemini_fallback_candidate_count": sum(
            result.get("gemini_fallback_candidate_count", 0)
            for result in pages
        ),
        "gemini_fallback_input_count": sum(
            result.get("gemini_fallback_input_count", 0)
            for result in pages
        ),
        "gemini_full_document_fallback_count": sum(
            result.get("gemini_full_document_fallback_count", 0)
            for result in pages
        ),
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
        **({"message": "; ".join(errors)} if errors else {}),
    }


def run_extraction_pipeline(
    image_path,
    llm_client,
    metadata=None,
    tesseract_cmd=None,
):
    """
    Extract an image or PDF timetable without approving any records.

    PDF page numbers in results are one-based. Images are page 1.
    """
    metadata = dict(metadata or {})
    source_path = Path(image_path)
    if source_path.suffix.casefold() == ".pdf":
        return _run_pdf_pipeline(
            source_path,
            llm_client,
            metadata,
            tesseract_cmd,
        )

    source_document = metadata.get("source_document", source_path.name)
    return _extract_image_page(
        source_path,
        llm_client,
        metadata,
        source_document,
        1,
        tesseract_cmd,
    )
