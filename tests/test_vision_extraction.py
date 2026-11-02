import json

import pytest

from app.database import db
from app.ingestion import extraction_pipeline
from app.ingestion.extraction_pipeline import run_extraction_pipeline
from app.ingestion.timetable_validator import (
    build_records,
    normalize_clock,
    normalize_day,
    parse_semester,
)
from app.ingestion.vision_extractor import parse_response, render_pages


def teacher(name, initials=None):
    return {"name": name, "initials": initials}


def slot(day, start, end, text, **overrides):
    value = {
        "day": day,
        "start_time": start,
        "end_time": end,
        "entry_type": "lecture",
        "cell_text": text,
        "course_code": None,
        "course_abbrev": text,
        "course_name": None,
        "teachers": [],
        "room": "LHC 301",
        "group": None,
        "notes": None,
    }
    value.update(overrides)
    return value


def page(slots, legend=None, **header):
    value = {
        "institution": "Test Institute",
        "semester": "III",
        "section": "A",
        "default_room": "LHC 301",
        "academic_year": "2026-2027",
        "legend": legend if legend is not None else LEGEND,
        "slots": slots,
        "uncertainties": [],
    }
    value.update(header)
    return value


LEGEND = [
    {
        "course_code": "CS33",
        "course_abbrev": "DS",
        "course_name": "DATA STRUCTURES",
        "faculty": [teacher("AKSHATHA KAMATH", "AKK")],
        "section_assignments": [],
    },
    {
        "course_code": "CS556",
        "course_abbrev": "OOAD",
        "course_name": "OBJECT ORIENTED ANALYSIS AND DESIGN",
        "faculty": [],
        "section_assignments": [
            {"section": "1", "teacher_name": "DR SUSHMA B",
             "initials": "SB", "room": "LHC 206"},
            {"section": "2", "teacher_name": "DR CHANDRIKA P",
             "initials": "CP", "room": "LHC 207"},
        ],
    },
]


# ---------------- Normalization ----------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("09:00", ("09:00", False)),
        ("9.55", ("09:55", False)),
        ("1.45", ("13:45", True)),
        ("2:40 PM", ("14:40", False)),
        ("12:00 pm", ("12:00", False)),
        ("12:30 AM", ("00:30", False)),
        ("25:00", (None, False)),
        ("noon", (None, False)),
    ],
)
def test_normalize_clock(value, expected):
    assert normalize_clock(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("MONDAY", "Monday"), ("Tue", "Tuesday"), ("thurs.", "Thursday"),
     ("Funday", None)],
)
def test_normalize_day(value, expected):
    assert normalize_day(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("VII", 7), ("Semester 5", 5), ("3", 3), ("IX", None), (None, None)],
)
def test_parse_semester(value, expected):
    assert parse_semester(value) == expected


# ---------------- Validation ----------------

def test_fully_supported_slot_is_auto_tier():
    result = build_records(page([
        slot("MONDAY", "9.00", "9.55", "DS",
             teachers=[teacher("AKSHATHA KAMATH", "AKK")]),
    ]), source_document="tt.png")

    record = result["records"][0]
    assert record["review_tier"] == "auto"
    assert record["review_status"] == "needs_review"
    assert record["approved"] is False
    assert (record["day"], record["start_time"], record["end_time"]) == (
        "Monday", "09:00", "09:55",
    )
    assert record["class_name"] == "3A"
    assert record["teacher_name"] == "AKSHATHA KAMATH"
    assert result["header"]["semester"] == 3


def test_initials_resolved_through_legend_but_unknown_initials_flagged():
    result = build_records(page([
        slot("Wednesday", "09:00", "10:50", "DS LAB (AKK, RDF)",
             entry_type="lab", room="CSE LAB 1",
             teachers=[teacher(None, "AKK"), teacher(None, "RDF")]),
    ]))

    record = result["records"][0]
    assert [t["teacher_name"] for t in record["teachers"]] == [
        "AKSHATHA KAMATH", None,
    ]
    assert record["review_tier"] == "check"
    assert "RDF" in record["unresolved_fields"]["teachers"]


def test_teacher_not_in_course_table_is_flagged():
    record = build_records(page([
        slot("Monday", "09:00", "09:55", "DS",
             teachers=[teacher("SOMEONE INVENTED")]),
    ]))["records"][0]

    assert record["review_tier"] == "check"
    assert any("SOMEONE INVENTED" in item for item in record["uncertainties"])


def test_class_without_room_or_teacher_needs_review_but_activity_does_not():
    records = build_records(page([
        slot("Monday", "09:00", "09:55", "DS", room=None),
        slot("Monday", "14:40", "16:30", "INDUSTRY TALK",
             entry_type="activity", course_abbrev=None, room=None),
    ]))["records"]

    lecture, activity = records
    assert {"teacher_name", "room"} <= set(lecture["unresolved_fields"])
    assert activity["entry_type"] == "activity"
    assert activity["review_tier"] == "auto"


def test_invalid_times_are_reported_not_guessed():
    record = build_records(page([
        slot("Monday", "10:00", "09:00", "DS",
             teachers=[teacher("AKSHATHA KAMATH")]),
    ]))["records"][0]

    assert "end_time" in record["unresolved_fields"]
    assert record["review_tier"] == "check"


def test_parallel_sections_are_grouped_and_not_overlaps():
    records = build_records(page([
        slot("Monday", "11:05", "12:00", "OOAD/BDA", course_abbrev="OOAD",
             group="Section 1", room="LHC 206",
             teachers=[teacher("DR SUSHMA B", "SB")]),
        slot("Monday", "11:05", "12:00", "OOAD/BDA", course_abbrev="OOAD",
             group="Section 2", room="LHC 207",
             teachers=[teacher("DR CHANDRIKA P", "CP")]),
    ]))["records"]

    assert {r["parallel_group"] for r in records} == {"Monday 11:05-12:00"}
    assert all(r["review_tier"] == "auto" for r in records)
    assert [r["subgroup"] for r in records] == ["Section 1", "Section 2"]


def test_overlapping_slots_for_same_class_are_flagged():
    records = build_records(page([
        slot("Monday", "09:00", "10:50", "DS",
             teachers=[teacher("AKSHATHA KAMATH")]),
        slot("Monday", "09:55", "10:50", "DS",
             teachers=[teacher("AKSHATHA KAMATH")]),
    ]))["records"]

    assert all(r["review_tier"] == "check" for r in records)
    assert all(any("Overlaps" in u for u in r["uncertainties"]) for r in records)


def test_cell_text_missing_from_ocr_is_flagged():
    supported, invented = build_records(
        page([
            slot("Monday", "09:00", "09:55", "DS",
                 teachers=[teacher("AKSHATHA KAMATH")]),
            slot("Tuesday", "09:00", "09:55", "QUANTUM BASKETRY",
                 teachers=[teacher("AKSHATHA KAMATH")]),
        ]),
        ocr_text="MONDAY DS LHC 301 TUESDAY",
    )["records"]

    assert supported["review_tier"] == "auto"
    assert "Cell text was not confirmed by OCR of the page" in invented["uncertainties"]


def test_user_metadata_overrides_detected_header():
    result = build_records(
        page([slot("Monday", "09:00", "09:55", "DS")]),
        metadata={"semester": 5, "section": "B", "class_name": "5B"},
    )
    assert result["records"][0]["class_name"] == "5B"
    assert result["header"]["semester"] == 5


def test_class_name_without_semester_uses_group():
    result = build_records(page(
        [slot("Mon", "8:30 AM", "9:30 AM", "Physics")],
        semester=None, section="Group B",
    ))
    assert result["header"]["class_name"] == "B"
    assert result["records"][0]["start_time"] == "08:30"


def test_day_layout_comes_from_period_header():
    header = build_records(page(
        [slot("Monday", "09:00", "09:55", "DS")],
        periods=[
            {"start_time": "9.00", "end_time": "9.55", "is_break": False},
            {"start_time": "10.50", "end_time": "11.05", "is_break": True},
            {"start_time": "12.55", "end_time": "1.45", "is_break": True},
            {"start_time": "3.35", "end_time": "4.30", "is_break": False},
        ],
    ))["header"]
    assert (header["day_start"], header["day_end"]) == ("09:00", "16:30")
    assert header["breaks"] == [["10:50", "11:05"], ["12:55", "13:45"]]


def test_day_layout_without_header_uses_slot_span_and_no_breaks():
    header = build_records(page([
        slot("Monday", "09:00", "09:55", "DS"),
        slot("Monday", "13:45", "14:40", "DS"),
    ]))["header"]
    assert (header["day_start"], header["day_end"], header["breaks"]) == (
        "09:00", "14:40", [],
    )


def test_course_offerings_flatten_section_assignments():
    offerings = build_records(page([]))["course_offerings"]
    assert {"faculty_name": "DR CHANDRIKA P", "section_label": "2",
            "room": "LHC 207"}.items() <= next(
        o for o in offerings if o["faculty_name"] == "DR CHANDRIKA P"
    ).items()


# ---------------- Response parsing and rendering ----------------

def test_parse_response_accepts_fenced_json_and_rejects_garbage():
    body = json.dumps(page([slot("Monday", "09:00", "09:55", "DS")]))
    assert parse_response(f"```json\n{body}\n```")["slots"][0]["day"] == "Monday"
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_response("not json")
    with pytest.raises(ValueError):
        parse_response({"slots": "wrong"})


def test_render_pages_rejects_unsupported_and_corrupt_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_pages(tmp_path / "missing.png")

    text_file = tmp_path / "timetable.txt"
    text_file.write_text("hello")
    with pytest.raises(ValueError, match="Unsupported"):
        render_pages(text_file)

    corrupt = tmp_path / "broken.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="Could not read image"):
        render_pages(corrupt)


def make_pdf(path, page_count):
    import pymupdf

    document = pymupdf.open()
    for index in range(page_count):
        document.new_page().insert_text((72, 72), f"Timetable page {index + 1}")
    document.save(path)
    document.close()


def test_render_pages_renders_every_pdf_page(tmp_path):
    pdf = tmp_path / "timetable.pdf"
    make_pdf(pdf, 2)

    pages = render_pages(pdf)
    assert [(number, mime) for number, _, mime in pages] == [
        (1, "image/png"), (2, "image/png"),
    ]
    assert all(data.startswith(b"\x89PNG") for _, data, _ in pages)


# ---------------- Pipeline ----------------

def fake_vision_client(pages_by_call):
    calls = []

    def client(image_bytes, mime_type, prompt, response_schema):
        calls.append(mime_type)
        return json.dumps(pages_by_call[len(calls) - 1])

    client.calls = calls
    return client


def test_pipeline_extracts_pdf_pages_with_one_based_provenance(tmp_path):
    pdf = tmp_path / "two_pages.pdf"
    make_pdf(pdf, 2)
    client = fake_vision_client([
        page([slot("Monday", "09:00", "09:55", "DS",
                   teachers=[teacher("AKSHATHA KAMATH")])]),
        page([slot("Tuesday", "09:00", "09:55", "DS",
                   teachers=[teacher("AKSHATHA KAMATH")])],
             section="B"),
    ])

    result = run_extraction_pipeline(pdf, client, ocr_check=False)

    assert len(client.calls) == 2
    assert result["status"] == "extraction_completed"
    assert [r["source_page"] for r in result["records"]] == [1, 2]
    assert {r["source_document"] for r in result["records"]} == {"two_pages.pdf"}
    assert all(r["review_status"] == "needs_review" for r in result["records"])


def test_pipeline_without_vision_client_extracts_nothing(tmp_path):
    pdf = tmp_path / "tt.pdf"
    make_pdf(pdf, 1)
    result = run_extraction_pipeline(pdf, None)
    assert result["status"] == "ai_unavailable"
    assert result["records"] == []
    assert "GEMINI_API_KEY" in result["message"]


def test_pipeline_reports_empty_timetable(tmp_path):
    pdf = tmp_path / "blank.pdf"
    make_pdf(pdf, 1)
    result = run_extraction_pipeline(
        pdf, fake_vision_client([page([])]), ocr_check=False,
    )
    assert result["records"] == []
    assert result["status"] == "needs_review"
    assert "No timetable entries" in result["message"]


def test_pipeline_ocr_unavailable_skips_cross_check(tmp_path, monkeypatch):
    pdf = tmp_path / "tt.pdf"
    make_pdf(pdf, 1)
    monkeypatch.setattr(extraction_pipeline, "_ocr_text", lambda *a: None)
    result = run_extraction_pipeline(pdf, fake_vision_client([
        page([slot("Monday", "09:00", "09:55", "DS",
                   teachers=[teacher("AKSHATHA KAMATH")])]),
    ]))
    assert result["ocr_checked"] is False
    assert result["records"][0]["review_tier"] == "auto"


def test_extracted_records_can_be_saved(tmp_path, monkeypatch):
    """Records from the validator satisfy the database's insert rules."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.initialize_database()
    records = build_records(page([
        slot("Monday", "9.00", "9.55", "DS",
             teachers=[teacher("AKSHATHA KAMATH", "AKK")]),
        slot("Monday", "1.45", "2.40", "INDUSTRY TALK",
             entry_type="activity", course_abbrev=None, room=None),
    ]))["records"]

    for record in records:
        db.insert_entry({**record, "review_status": "verified",
                         "timetable_id": "t1"})

    saved = db.get_all_entries()
    assert [(r["start_time"], r["entry_type"]) for r in saved] == [
        ("09:00", "class"), ("13:45", "activity"),
    ]


# ---------------- Pending review persistence ----------------

def test_pending_review_survives_reload_and_clears_when_done(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.initialize_database()
    db.register_timetable("t1", "tt.png", tmp_path / "tt.png")

    db.save_pending_review("t1", {"records": [{"day": "Monday"}]})
    db.save_pending_review("t1", {"records": [{"day": "Tuesday"}]})
    pending = db.get_pending_reviews()
    assert len(pending) == 1
    assert pending[0]["original_filename"] == "tt.png"
    assert pending[0]["payload"]["records"] == [{"day": "Tuesday"}]

    db.save_pending_review("t1", {"records": [], "unresolved_items": []})
    assert db.get_pending_reviews() == []
