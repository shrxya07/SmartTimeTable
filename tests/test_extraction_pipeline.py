
import os
import json
from pathlib import Path
import shutil

import pytest

from app.ingestion.extraction_pipeline import run_extraction_pipeline


def test_pipeline_connects_ocr_and_genai(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    fake_ocr = {
        "text": "Monday 09:00 Data Structures",
        "ocr_confidence": 0.91,
        "words": [],
        "source_document": "sample.png",
    }

    def fake_process(**kwargs):
        return fake_ocr

    def fake_interpret(ocr_evidence, llm_client, metadata):
        assert ocr_evidence["text"] == fake_ocr["text"]
        assert metadata["source_document"] == "sample.png"
        return {
            "records": [
                {
                    "day": "Monday",
                    "start_time": "09:00",
                    "end_time": "09:55",
                    "subject": "Data Structures",
                    "teacher_name": "Unknown",
                    "room": "LHC 301",
                    "original_text": "Monday 09:00 Data Structures",
                    "confidence": 0.95,
                }
            ]
        }

    monkeypatch.setattr(pipeline, "process_timetable_image", fake_process)
    monkeypatch.setattr(
        pipeline, "interpret_timetable_document", fake_interpret
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "sample.png",
        llm_client=lambda system, prompt: "{}",
    )

    assert result["status"] == "extraction_completed"
    assert len(result["records"]) == 1

    record = result["records"][0]
    assert record["source_document"] == "sample.png"
    assert record["ocr_confidence"] == 0.91
    assert record["confidence"] == 0.95
    assert record["review_status"] == "needs_review"


def test_pipeline_handles_empty_ocr(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    def fake_process(**kwargs):
        return {
            "text": "",
            "ocr_confidence": 0.0,
            "words": [],
        }

    def unexpected_interpret(*args, **kwargs):
        raise AssertionError("LLM should not be called for empty OCR")

    monkeypatch.setattr(pipeline, "process_timetable_image", fake_process)
    monkeypatch.setattr(
        pipeline, "interpret_timetable_document", unexpected_interpret
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "blank.png",
        llm_client=lambda system, prompt: "{}",
    )

    assert result["status"] == "needs_review"
    assert result["records"] == []


def test_pipeline_preserves_page_provenance(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    monkeypatch.setattr(
        pipeline,
        "process_timetable_image",
        lambda **kwargs: {
            "text": "Tuesday 10:00 Mathematics",
            "ocr_confidence": 0.88,
            "words": [],
        },
    )

    monkeypatch.setattr(
        pipeline,
        "interpret_timetable_document",
        lambda **kwargs: {
            "records": [
                {
                    "day": "Tuesday",
                    "start_time": "10:00",
                    "end_time": "10:55",
                    "subject": "Mathematics",
                    "teacher_name": "Unknown",
                    "room": "LHC 301",
                    "original_text": "Tuesday 10:00 Mathematics",
                    "confidence": 0.9,
                }
            ]
        },
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "sample.png",
        llm_client=lambda system, prompt: "{}",
        metadata={
            "source_document": "semester4.pdf",
            "source_page": 2,
        },
    )

    record = result["records"][0]
    assert record["source_document"] == "semester4.pdf"
    assert record["source_page"] == 1


def _configure_synthetic_pipeline(monkeypatch, text, offerings):
    import app.ingestion.extraction_pipeline as pipeline

    bbox = {"x": 1, "y": 2, "width": 30, "height": 20}
    cell = {
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "09:55",
        "text": text,
        "bbox": bbox,
        "source_bbox": bbox,
        "words": [{"text": text, "confidence": 0.93}],
        "lines": [{"text": text, "bbox": bbox}],
    }
    geometry = {
        "width": 100,
        "height": 100,
        "x_lines": [0, 100],
        "y_lines": [0, 100],
        "schedule_top": 10,
        "schedule_bottom": 90,
    }
    regions = {
        "header": {
            "metadata": {"semester": 5, "section": "A"},
            "status": "located",
        },
        "grid": {"bbox": bbox, "status": "located"},
        "legend": {"bbox": bbox, "status": "located"},
    }
    monkeypatch.setattr(
        pipeline,
        "process_timetable_image",
        lambda **kwargs: {
            "text": f"Monday 09:00 {text}",
            "source_document": "sample.png",
            "ocr_confidence": 0.93,
            "words": [{
                "text": text,
                "confidence": 0.93,
                "original_bbox": bbox,
            }],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "detect_grid_geometry",
        lambda image_path: geometry,
    )
    monkeypatch.setattr(
        pipeline,
        "locate_regions",
        lambda evidence, detected_geometry: regions,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_cells_from_evidence",
        lambda **kwargs: {"cells": [dict(cell)], "grid_geometry": geometry},
    )
    monkeypatch.setattr(
        pipeline,
        "extract_legend",
        lambda *args, **kwargs: {
            "status": "resolved",
            "offerings": offerings,
            "unresolved": [],
        },
    )
    return cell, bbox


def test_deterministic_resolution_does_not_invoke_gemini(monkeypatch, tmp_path):
    from app.ingestion.timetable_resolver import CourseOffering

    offering = CourseOffering(
        course_name="Data Structures",
        abbrev="DS",
        faculty_name="Alice Smith",
        faculty_initials=("AS",),
        room="LHC 206",
        source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
    )
    _configure_synthetic_pipeline(monkeypatch, "DS", [offering])

    def unexpected_gemini(*args, **kwargs):
        raise AssertionError("Gemini must not receive resolved timetable lines")

    result = run_extraction_pipeline(
        tmp_path / "synthetic.png",
        unexpected_gemini,
    )

    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["review_tier"] == "auto"
    assert record["resolution_method"] in {"exact", "normalized"}
    assert record["source_page"] == 1
    assert record["source_bbox"] is not None
    assert record["legend_source_bbox"] == offering.source_bbox
    assert record["evidence"] == "DS"
    assert record["ocr_confidence"] == 0.93
    assert record["approved"] is False
    assert result["gemini_fallback_count"] == 0


def test_only_unresolved_lines_reach_gemini_and_stay_for_review(
    monkeypatch,
    tmp_path,
):
    from app.ingestion.timetable_resolver import CourseOffering

    offering = CourseOffering(
        course_name="Database Systems",
        abbrev="DBMS",
        faculty_name="Alice Smith",
        faculty_initials=("AS",),
        room="LHC 206",
        source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
    )
    _configure_synthetic_pipeline(
        monkeypatch,
        "UNMATCHED Alice Smith AS LHC 206",
        [offering],
    )
    calls = []

    def llm_client(system_prompt, user_prompt):
        calls.append(user_prompt)
        assert "UNMATCHED" in user_prompt
        assert "unresolved timetable lines" in system_prompt
        return {
            "interpretations": [{
                "line_index": 0,
                "subject": "UNMATCHED",
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "teacher_name": "Alice Smith",
                "teacher_initials": ["AS"],
                "room": "LHC 206",
                "confidence": 1.0,
            }]
        }

    result = run_extraction_pipeline(
        tmp_path / "unresolved.png",
        llm_client,
    )

    assert len(calls) == 1
    assert result["gemini_fallback_count"] == 1
    assert result["gemini_fallback_candidate_count"] == 1
    assert result["gemini_fallback_input_count"] == 1
    fallback = result["records"][0]
    assert fallback["resolution_method"] == "gemini_fallback"
    assert fallback["review_tier"] == "check"
    assert fallback["source_bbox"] is not None
    assert fallback["source_page"] == 1
    assert fallback["approved"] is False
    assert fallback["review_status"] == "needs_review"
    assert result["unresolved_items"]


def test_unresolved_line_remains_reviewable_without_gemini(
    monkeypatch,
    tmp_path,
):
    from app.ingestion.timetable_resolver import CourseOffering

    offering = CourseOffering(
        course_name="Database Systems",
        abbrev="DBMS",
        faculty_name="Alice Smith",
        faculty_initials=("AS",),
        room="LHC 206",
        source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
    )
    _configure_synthetic_pipeline(monkeypatch, "UNMATCHED", [offering])

    result = run_extraction_pipeline(
        tmp_path / "no-gemini.png",
        None,
    )

    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["resolution_method"] == "unresolved_deterministic"
    assert record["review_tier"] == "check"
    assert record["original_text"] == "UNMATCHED"
    assert record["source_bbox"] is not None
    assert record["unresolved_fields"]["course"]["status"] == "unresolved"
    assert result["gemini_fallback_count"] == 0
    assert result["gemini_fallback_candidate_count"] == 0
    assert result["gemini_fallback_input_count"] == 0


@pytest.mark.parametrize(
    ("text", "entry_type"),
    [("INDUSTRY TALK", "activity"), ("BREAK", "break")],
)
def test_activity_and_break_entries_keep_optional_teacher_and_room(
    monkeypatch,
    tmp_path,
    text,
    entry_type,
):
    _configure_synthetic_pipeline(monkeypatch, text, [])

    def unexpected_gemini(*args, **kwargs):
        raise AssertionError("Activity and break entries are deterministic")

    result = run_extraction_pipeline(
        tmp_path / "non-course.png",
        unexpected_gemini,
    )

    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["entry_type"] == entry_type
    assert record["teacher_name"] is None
    assert record["room"] is None
    assert record["review_tier"] == "auto"
    assert record["approved"] is False


def test_pipeline_resolver_preserves_parallel_lab_activity_and_break_entries(
    monkeypatch,
    tmp_path,
):
    import app.ingestion.extraction_pipeline as pipeline
    from app.ingestion.timetable_resolver import CourseOffering

    offerings = [
        CourseOffering(
            course_name="Alpha",
            abbrev="AA",
            section_label=1,
            faculty_name="Teacher One",
            faculty_initials=("T1",),
            room="LHC 206",
            source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
        ),
        CourseOffering(
            course_name="Beta",
            abbrev="BB",
            section_label=2,
            faculty_name="Teacher Two",
            faculty_initials=("T2",),
            room="LHC 207",
            source_bbox={"x": 40, "y": 80, "width": 20, "height": 10},
        ),
        CourseOffering(
            course_name="Course Lab",
            abbrev="CC",
            kind="LAB",
            section_label="S1",
            faculty_name="Lab Faculty",
            faculty_initials=("AS", "BJ"),
            room="CSE LAB 1",
            source_bbox={"x": 40, "y": 100, "width": 20, "height": 10},
        ),
    ]
    _configure_synthetic_pipeline(monkeypatch, "AA/BB", offerings)
    texts = [
        "AA/BB",
        "CSE LAB 1 (AS, BJ) LAB S1 CC",
        "INDUSTRY TALK",
        "BREAK",
    ]
    cells = []
    for index, text in enumerate(texts):
        bbox = {
            "x": index * 40,
            "y": 2,
            "width": 30,
            "height": 20,
        }
        cells.append({
            "day": "Monday",
            "start_time": "09:00",
            "end_time": "09:55",
            "text": text,
            "bbox": bbox,
            "source_bbox": bbox,
            "words": [{"text": text, "confidence": 0.9}],
            "lines": [{"text": text, "bbox": bbox}],
        })
    monkeypatch.setattr(
        pipeline,
        "extract_cells_from_evidence",
        lambda **kwargs: {"cells": cells},
    )

    result = run_extraction_pipeline(
        tmp_path / "mixed-entries.png",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("All supported slot lines should resolve")
        ),
    )

    parallel = [
        record for record in result["records"]
        if record["course_abbrev"] in {"AA", "BB"}
    ]
    lab = next(
        record for record in result["records"]
        if record["course_abbrev"] == "CC"
    )
    activity = next(
        record for record in result["records"]
        if record["entry_type"] == "activity"
    )
    break_entry = next(
        record for record in result["records"]
        if record["entry_type"] == "break"
    )
    assert len(parallel) == 2
    assert len({record["parallel_group"] for record in parallel}) == 1
    assert {record["subgroup"] for record in parallel} == {1, 2}
    assert lab["subgroup"] == "S1"
    assert len(lab["teachers"]) == 2
    assert activity["teacher_name"] is None and activity["room"] is None
    assert break_entry["teacher_name"] is None and break_entry["room"] is None
    assert result["gemini_fallback_count"] == 0
    assert result["gemini_fallback_candidate_count"] == 0


def test_duplicate_events_in_separate_documents_keep_separate_provenance(
    monkeypatch,
    tmp_path,
):
    from app.ingestion.timetable_resolver import CourseOffering

    offering = CourseOffering(
        course_name="Data Structures",
        abbrev="DS",
        faculty_name="Alice Smith",
        faculty_initials=("AS",),
        room="LHC 206",
        source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
    )
    _configure_synthetic_pipeline(monkeypatch, "DS", [offering])
    first = run_extraction_pipeline(tmp_path / "first.png", lambda *_: {})
    second = run_extraction_pipeline(tmp_path / "second.png", lambda *_: {})

    assert first["records"][0]["source_document"] == "first.png"
    assert second["records"][0]["source_document"] == "second.png"
    assert first["records"][0]["source_bbox"] == second["records"][0]["source_bbox"]


def test_missing_legend_is_reported_without_discarding_unresolved_lines(
    monkeypatch,
    tmp_path,
):
    import app.ingestion.extraction_pipeline as pipeline

    _configure_synthetic_pipeline(monkeypatch, "UNMATCHED", [])
    monkeypatch.setattr(
        pipeline,
        "extract_legend",
        lambda *args, **kwargs: {
            "status": "unresolved",
            "offerings": [],
            "unresolved": [{"reason": "legend headings not readable"}],
        },
    )
    result = run_extraction_pipeline(
        tmp_path / "unreadable-legend.png",
        lambda *_: {"interpretations": []},
    )

    assert result["legend_error"] == "legend headings not readable"
    assert any(
        item.get("reason") == "legend_extraction_failed"
        for item in result["unresolved_items"]
    )
    assert any(
        item.get("reason") == "No unambiguous course match in legend."
        for item in result["unresolved_items"]
    )


def test_grid_failure_uses_legacy_fallback_without_fabricating_provenance(
    monkeypatch,
    tmp_path,
):
    import app.ingestion.extraction_pipeline as pipeline

    monkeypatch.setattr(
        pipeline,
        "process_timetable_image",
        lambda **kwargs: {
            "text": "Monday 09:00 Data Structures",
            "ocr_confidence": 0.9,
            "words": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "detect_grid_geometry",
        lambda image_path: (_ for _ in ()).throw(
            ValueError("grid not detected")
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "interpret_timetable_document",
        lambda **kwargs: {
            "records": [{
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "subject": "Data Structures",
                "teacher_name": "UNKNOWN",
                "room": "UNKNOWN",
                "original_text": "Monday 09:00 Data Structures",
            }],
        },
    )

    result = run_extraction_pipeline(
        tmp_path / "grid-failure.png",
        lambda *_: {},
    )

    record = result["records"][0]
    assert result["extraction_error"] == (
        "grid geometry detection failed: grid not detected"
    )
    assert record["resolution_method"] == "gemini_full_document_fallback"
    assert record["review_tier"] == "check"
    assert record["source_bbox"] is None
    assert "source_bbox" in record["unresolved_fields"]
    assert record["approved"] is False


def test_pdf_pages_keep_one_based_provenance_and_duplicate_events(
    monkeypatch,
    tmp_path,
):
    import pymupdf
    from app.ingestion.timetable_resolver import CourseOffering
    import app.ingestion.extraction_pipeline as pipeline

    pdf_path = tmp_path / "two-pages.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "First page")
    document.new_page().insert_text((72, 72), "Second page")
    document.save(pdf_path)
    document.close()

    bbox = {"x": 1, "y": 2, "width": 30, "height": 20}
    geometry = {
        "width": 100,
        "height": 100,
        "x_lines": [0, 100],
        "y_lines": [0, 100],
        "schedule_top": 10,
        "schedule_bottom": 90,
    }
    monkeypatch.setattr(
        pipeline,
        "process_timetable_image",
        lambda **kwargs: {
            "text": "Monday 09:00 DS",
            "ocr_confidence": 0.9,
            "words": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "detect_grid_geometry",
        lambda image_path: geometry,
    )
    monkeypatch.setattr(
        pipeline,
        "locate_regions",
        lambda *args: {
            "header": {
                "metadata": {"semester": 5, "section": "A"},
                "status": "located",
            },
            "grid": {"bbox": bbox, "status": "located"},
            "legend": {"bbox": bbox, "status": "located"},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "extract_cells_from_evidence",
        lambda **kwargs: {
            "cells": [{
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "text": "DS",
                "bbox": bbox,
                "source_bbox": bbox,
                "words": [{"text": "DS", "confidence": 0.9}],
                "lines": [],
                "source_page": kwargs["ocr_evidence"]["source_page"],
            }],
        },
    )
    offering = CourseOffering(
        course_name="Data Structures",
        abbrev="DS",
        faculty_name="Alice Smith",
        faculty_initials=("AS",),
        room="LHC 206",
        source_bbox={"x": 40, "y": 60, "width": 20, "height": 10},
    )
    monkeypatch.setattr(
        pipeline,
        "extract_legend",
        lambda *args, **kwargs: {
            "status": "resolved",
            "offerings": [offering],
            "unresolved": [],
        },
    )

    result = run_extraction_pipeline(
        pdf_path,
        lambda *_: (_ for _ in ()).throw(
            AssertionError("Resolved PDF entries must not reach Gemini")
        ),
        metadata={"semester": 5, "section": "A"},
    )

    assert result["extraction_metadata"]["page_numbering"] == "one-based"
    assert [record["source_page"] for record in result["records"]] == [1, 2]
    assert {record["source_document"] for record in result["records"]} == {
        "two-pages.pdf",
    }
    assert len(result["records"]) == 2


@pytest.mark.parametrize(
    "filename",
    ["cse_sem3_a.png", "cse_sem5_a.png", "cse_sem7_c.jpeg"],
)
def test_real_timetable_images_execute_end_to_end(filename):
    root = Path(__file__).resolve().parents[1] / "data" / "uploads"
    image_path = root / filename
    if not image_path.is_file():
        pytest.skip(f"Timetable regression image is unavailable: {filename}")

    tesseract = os.getenv("TESSERACT_CMD")
    default_tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not tesseract and default_tesseract.is_file():
        tesseract = str(default_tesseract)
    if not tesseract and not shutil.which("tesseract"):
        pytest.skip("Tesseract is required for timetable image regression.")

    def local_llm(system_prompt, user_prompt):
        if "unresolved timetable lines" in system_prompt:
            return {"interpretations": []}
        return {"records": [], "document_uncertainties": []}

    result = run_extraction_pipeline(
        image_path,
        local_llm,
        tesseract_cmd=tesseract,
    )

    assert result["status"] in {"extraction_completed", "needs_review"}
    assert result["source_page"] == 1
    assert result["ocr_evidence"]["text"]
    assert result["source_document"] == filename
    for record in result["records"]:
        assert record["source_document"] == filename
        assert record["source_page"] == 1
        assert record["review_tier"] in {"auto", "check"}
        assert record["review_status"] == "needs_review"
        assert record["approved"] is False


def test_semester5_image_preserves_legend_assignments_and_cell_structure():
    root = Path(__file__).resolve().parents[1] / "data" / "uploads"
    image_path = root / "cse_sem5_a.png"
    if not image_path.is_file():
        pytest.skip("Semester 5 A regression image is unavailable.")

    tesseract = os.getenv("TESSERACT_CMD")
    default_tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not tesseract and default_tesseract.is_file():
        tesseract = str(default_tesseract)
    if not tesseract and not shutil.which("tesseract"):
        pytest.skip("Tesseract is required for timetable image regression.")

    fallback_lines = []

    def local_llm(system_prompt, user_prompt):
        assert "unresolved timetable lines" in system_prompt
        payload = json.loads(user_prompt)
        fallback_lines.extend(payload["unresolved_lines"])
        return {"interpretations": []}

    result = run_extraction_pipeline(
        image_path,
        local_llm,
        tesseract_cmd=tesseract,
    )
    records = result["records"]

    parallel = [
        record for record in records
        if record.get("evidence") == "OOAD/BDA"
    ]
    assert len(parallel) == 8
    for day in ("Monday", "Friday"):
        day_entries = [record for record in parallel if record["day"] == day]
        assert len(day_entries) == 4
        assert {record["subgroup"] for record in day_entries} == {
            "1", "2", "3", "4",
        }
        assert {record["course_abbrev"] for record in day_entries} == {
            "O0AD", "BDA",
        }
        assert len({record["parallel_group"] for record in day_entries}) == 1

    aec = [
        record for record in records
        if record.get("evidence") == "AEC\nNOSQL/PE"
    ]
    assert {record["subgroup"] for record in aec} == {"1", "2", "3", "4"}
    assert any(
        record["subgroup"] == "4"
        and record["teacher_name"] == "AKSHATHA KAMATH"
        for record in aec
    )

    lab_records = [
        record for record in records
        if record.get("entry_type") == "lab"
    ]
    dcn_lab = next(
        record for record in lab_records
        if "DCN LAB" in record.get("evidence", "")
    )
    assert dcn_lab["room"] == "CSE LAB 2"
    assert [
        (teacher["teacher_name"], teacher["initials"])
        for teacher in dcn_lab["teachers"]
    ] == [
        ("NANDINI SB", "NSB"),
        ("PALLAVI N", "PN"),
        ("PK", "PK"),
    ]

    subgroup_labs = [
        record for record in lab_records
        if record.get("day") == "Thursday"
    ]
    assert {record["subgroup"] for record in subgroup_labs} == {
        "S1", "S2", "S3", "S4",
    }
    assert all(record["room"] for record in subgroup_labs)
    assert any(
        record.get("course_abbrev") == "FSD LAB"
        and record.get("room") == "CSE LAB 3"
        for record in lab_records
    )

    monday_activity = next(
        record for record in records
        if record.get("day") == "Monday"
        and record.get("entry_type") == "activity"
    )
    assert (monday_activity["start_time"], monday_activity["end_time"]) == (
        "15:35", "16:30",
    )
    assert all(
        record.get("start_time") and record.get("end_time")
        for record in records
        if record.get("entry_type") in {"activity", "break"}
    )
    assert all(record["approved"] is False for record in records)

    assert result["deterministic_count"] == 39
    assert result["gemini_fallback_input_count"] == len(fallback_lines) == 1
    assert [
        line["evidence"]["text"] for line in fallback_lines
    ] == ["EVS"]
