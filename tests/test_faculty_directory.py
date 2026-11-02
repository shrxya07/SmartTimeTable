from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.database import db
from app.database.retrieval import get_faculty_directory
from app.ingestion.timetable_validator import (
    apply_faculty_directory,
    build_records,
    fill_initials_in_pending_reviews,
)

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture
def department(tmp_path, monkeypatch):
    """Two timetables whose course tables define different faculty."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "department.db")
    db.initialize_database()
    db.register_timetable("t5a", "sem5_a.png", "sem5_a.png")
    db.insert_course_offerings("t5a", [
        {"course_name": "FAFL", "faculty_name": "PALLAVI N", "faculty_initials": "PN"},
        {"course_name": "OOAD", "faculty_name": "DR.SUSHMA B", "faculty_initials": "SB"},
        {"course_name": "BDA", "faculty_name": "UZMA SULTHANA", "faculty_initials": "US"},
    ])
    db.register_timetable("t7c", "sem7_c.png", "sem7_c.png")
    db.insert_course_offerings("t7c", [
        {"course_name": "OOAD", "faculty_name": "Dr Sushma B", "faculty_initials": "SB"},
        {"course_name": "Maths", "faculty_name": "UMA SHANKAR", "faculty_initials": "US"},
    ])


def lab_record(*initials):
    return build_records({
        "institution": None, "semester": "3", "section": "A",
        "default_room": "LHC 301", "academic_year": None, "periods": [],
        "legend": [], "uncertainties": [],
        "slots": [{
            "day": "Monday", "start_time": "09:00", "end_time": "10:50",
            "entry_type": "lab", "cell_text": f"DS LAB ({', '.join(initials)})",
            "course_code": None, "course_abbrev": "DS LAB", "course_name": None,
            "teachers": [{"name": None, "initials": i} for i in initials],
            "room": "CSE LAB 1", "group": None, "notes": None,
        }],
    })["records"][0]


def test_directory_merges_spellings_and_keeps_conflicts(department):
    directory = get_faculty_directory()
    assert directory["PN"] == {"names": ["PALLAVI N"], "sources": ["sem5_a.png"]}
    # One person, two spellings -> one name, both sources.
    assert len(directory["SB"]["names"]) == 1
    assert directory["SB"]["sources"] == ["sem5_a.png", "sem7_c.png"]
    # Two different people share "US".
    assert directory["US"]["names"] == ["UMA SHANKAR", "UZMA SULTHANA"]


def test_unique_initials_are_filled_with_their_source(department):
    record = lab_record("PN")
    assert record["review_tier"] == "check"

    filled = apply_faculty_directory([record], get_faculty_directory())

    assert filled == 1
    assert record["teachers"][0]["teacher_name"] == "PALLAVI N"
    assert record["teacher_name"] == "PALLAVI N"
    assert record["directory_notes"] == ["PN = PALLAVI N (from sem5_a.png)"]
    assert record["review_tier"] == "auto"


def test_ambiguous_and_unknown_initials_stay_flagged(department):
    record = lab_record("US", "RDF")
    apply_faculty_directory([record], get_faculty_directory())

    assert [t["teacher_name"] for t in record["teachers"]] == [None, None]
    reason = record["unresolved_fields"]["teachers"]
    assert "US match several faculty: UMA SHANKAR, UZMA SULTHANA" in reason
    assert "RDF are not defined in any course table" in reason
    assert record["review_tier"] == "check"


def test_a_pages_own_course_table_wins_over_the_directory(department):
    page = {
        "institution": None, "semester": "3", "section": "A",
        "default_room": None, "academic_year": None, "periods": [],
        "uncertainties": [],
        "legend": [{
            "course_code": None, "course_abbrev": "DS", "course_name": "DS",
            "faculty": [{"name": "PRIYA NAIR", "initials": "PN"}],
            "section_assignments": [],
        }],
        "slots": [{
            "day": "Monday", "start_time": "09:00", "end_time": "09:55",
            "entry_type": "lecture", "cell_text": "DS (PN)", "course_code": None,
            "course_abbrev": "DS", "course_name": None,
            "teachers": [{"name": None, "initials": "PN"}],
            "room": "LHC 301", "group": None, "notes": None,
        }],
    }
    record = build_records(page, faculty_directory=get_faculty_directory())["records"][0]
    assert record["teacher_name"] == "PRIYA NAIR"
    assert record["directory_notes"] == []


def test_a_new_course_table_fills_other_pending_reviews(department):
    db.register_timetable("t3a", "sem3_a.png", "sem3_a.png")
    db.save_pending_review("t3a", {"records": [lab_record("RJ", "PN")]})
    db.register_timetable("t3b", "sem3_b.png", "sem3_b.png")
    db.save_pending_review("t3b", {"records": [lab_record("XY")]})

    # A later upload's course table defines RJ.
    db.register_timetable("t7b", "sem7_b.png", "sem7_b.png")
    db.insert_course_offerings("t7b", [
        {"course_name": "SAN", "faculty_name": "RAJESH J", "faculty_initials": "RJ"},
    ])
    changed = fill_initials_in_pending_reviews(
        db.get_pending_reviews(), get_faculty_directory(),
    )

    assert [(timetable_id, filled) for timetable_id, _, filled in changed] == [("t3a", 2)]
    record = changed[0][1]["records"][0]
    assert [t["teacher_name"] for t in record["teachers"]] == ["RAJESH J", "PALLAVI N"]
    assert record["review_tier"] == "auto"


def test_review_page_button_fills_initials_in_a_pending_review(department):
    db.register_timetable("t3a", "sem3_a.png", "sem3_a.png")
    db.save_pending_review("t3a", {
        "records": [lab_record("PN", "RDF")],
        "metadata": {"class_name": "3A"},
    })

    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.sidebar.radio[0].set_value("Review Extracted Records").run()
    button = next(b for b in app.button if b.label.startswith("Fill 1 teacher"))
    button.click().run()
    assert not app.exception, [e.value for e in app.exception]

    saved = db.get_pending_reviews()[0]["payload"]["records"][0]
    assert [t["teacher_name"] for t in saved["teachers"]] == ["PALLAVI N", None]
    assert "Filled 1 teacher initial(s)" in " ".join(s.value for s in app.success)
