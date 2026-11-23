import pytest

from app.database import db
from app.database.retrieval import find_similar_timetables


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_timetable.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize_database()
    return tmp_path


WEEK = [
    ("Monday", "09:00", "10:00", "DBMS"),
    ("Monday", "10:00", "11:00", "OS"),
    ("Tuesday", "09:00", "10:00", "CN"),
    ("Tuesday", "11:00", "13:00", "OS LAB"),
    ("Wednesday", "09:00", "10:00", "DBMS"),
    ("Wednesday", "10:00", "11:00", "AI"),
    ("Thursday", "09:00", "10:00", "OS"),
    ("Friday", "14:00", "16:00", "AI LAB"),
]


def records(rows, class_name="5A"):
    return [
        {
            "day": day, "start_time": start, "end_time": end,
            "course_abbrev": course, "subject": course,
            "class_name": class_name,
        }
        for day, start, end, course in rows
    ]


def approved_timetable(timetable_id, filename, rows, class_name="5A"):
    db.register_timetable(timetable_id, filename, f"{filename}", status="ready")
    for record in records(rows, class_name):
        db.insert_entry({
            **record, "teacher_name": "Dr Rao", "room": "LHC 301",
            "review_status": "verified", "timetable_id": timetable_id,
        })


def test_cropped_copy_of_an_approved_timetable_is_found(test_database):
    approved_timetable("tt_full", "sem5_a.png", WEEK)

    cropped = records(WEEK[:5])  # bottom of the image cut off
    matches = find_similar_timetables(cropped, exclude_timetable_id="tt_new")

    assert [m["original_filename"] for m in matches] == ["sem5_a.png"]
    assert matches[0]["shared_sessions"] == 5
    assert matches[0]["score"] == 1.0


def test_pending_timetable_is_compared_too(test_database):
    db.register_timetable("tt_pending", "sem5_a.pdf", "sem5_a.pdf")
    db.save_pending_review("tt_pending", {
        "records": records(WEEK), "metadata": {"class_name": "5A"},
    })

    matches = find_similar_timetables(records(WEEK[2:]), class_name="5A")

    assert [m["timetable_id"] for m in matches] == ["tt_pending"]


def test_other_section_with_same_slots_is_not_a_match(test_database):
    approved_timetable("tt_5b", "sem5_b.png", WEEK, class_name="5B")

    assert find_similar_timetables(records(WEEK, "5A")) == []


def test_different_timetable_is_not_a_match(test_database):
    approved_timetable("tt_full", "sem5_a.png", WEEK)
    other = [
        ("Monday", "09:00", "10:00", "DBMS"),
        ("Monday", "11:00", "12:00", "ML"),
        ("Tuesday", "14:00", "15:00", "SE"),
        ("Thursday", "11:00", "12:00", "CC"),
        ("Friday", "09:00", "10:00", "IOT"),
    ]

    assert find_similar_timetables(records(other)) == []


def test_upload_itself_is_excluded(test_database):
    approved_timetable("tt_full", "sem5_a.png", WEEK)

    assert find_similar_timetables(records(WEEK), exclude_timetable_id="tt_full") == []


def test_course_and_class_written_differently_still_match(test_database):
    approved_timetable("tt_full", "sem3_a.png", [
        ("Monday", "09:00", "10:00", "Discrete Mathematical Structures"),
        ("Monday", "11:05", "12:55", "Object Oriented Programming Laboratory"),
        ("Tuesday", "09:00", "10:00", "Digital Design and Computer Organization"),
        ("Friday", "09:00", "10:50", "DMS"),
    ], class_name="Semester 3 Section A")
    copy = records([
        ("Monday", "09:00", "10:00", "DMS"),
        ("Monday", "11:05", "12:55", "OOPS LAB"),
        ("Tuesday", "09:00", "10:00", "DDCO"),
        ("Friday", "09:00", "10:50", "DMS TUT"),
    ], class_name="3A")

    matches = find_similar_timetables(copy)

    assert [m["timetable_id"] for m in matches] == ["tt_full"]
    assert matches[0]["shared_sessions"] == 4


def test_same_slots_with_different_courses_do_not_match(test_database):
    approved_timetable("tt_full", "sem5_a.png", WEEK)
    swapped = [(day, start, end, "ML") for day, start, end, _ in WEEK]

    assert find_similar_timetables(records(swapped)) == []
