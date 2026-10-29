
from app.ingestion.record_parser import parse_timetable_cell


def make_cell(text):
    return {
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "09:55",
        "text": text,
        "review_status": "needs_review",
    }


def test_oop_is_normalized():
    record = parse_timetable_cell(make_cell("oop"))
    assert record["subject"] == "Object Oriented Programming"
    assert record["teacher_name"] == "Pradeep Kumar D"


def test_ds_lab_is_recognized_as_lab():
    record = parse_timetable_cell(
        make_cell("DS LAB (AKK,MRC,PN) CSE LAB 1")
    )
    assert record["subject"] == "Data Structures Laboratory"
    assert record["room"] == "CSE LAB 1"
    assert record["teacher_name"] == "UNKNOWN"
    assert record["review_status"] == "needs_review"


def test_explicit_tutorial_room_is_extracted():
    record = parse_timetable_cell(
        make_cell("DMS TUT (MA,NPK) LHC 305")
    )
    assert record["subject"] == "Discrete Mathematical Structures"
    assert record["room"] == "LHC 305"


def test_default_room_is_used():
    record = parse_timetable_cell(make_cell("OOP"))
    assert record["room"] == "LHC 301"


def test_unknown_text_is_flagged():
    record = parse_timetable_cell(make_cell("UNREADABLE TEXT"))
    assert record["teacher_name"] == "UNKNOWN"
    assert record["review_status"] == "needs_review"
