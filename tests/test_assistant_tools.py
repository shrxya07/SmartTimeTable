import pytest

from app import genai_assistant as assistant
from app.database import db


@pytest.fixture(autouse=True)
def timetable(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "assistant.db")
    db.initialize_database()

    def add(**fields):
        entry = {
            "subject": "Data Structures",
            "course_abbrev": "DS",
            "teacher_name": "Dr Rao",
            "class_name": "3A",
            "semester": 3,
            "section": "A",
            "day": "Monday",
            "start_time": "09:00",
            "end_time": "09:55",
            "room": "LHC 301",
            "review_status": "verified",
            "timetable_id": "t3a",
        }
        entry.update(fields)
        return db.insert_entry(entry)

    add()
    add(subject="DS Lab", teacher_name="AKSHATHA KAMATH",
        teachers=[{"teacher_name": "AKSHATHA KAMATH", "initials": "AKK"},
                  {"teacher_name": "MANJULA CHOUGALA", "initials": "MRC"}],
        start_time="14:40", end_time="16:30", room="CSE LAB 1",
        entry_type="lab")
    add(subject="Industry Talk", teacher_name=None, room=None,
        entry_type="activity", day="Tuesday",
        start_time="13:45", end_time="16:30")
    add(subject="Unapproved", teacher_name="Dr Rao", day="Monday",
        start_time="11:05", end_time="12:00", review_status="needs_review")


@pytest.mark.parametrize("query", ["Dr. Rao", "rao", "DR RAO", "Prof Rao"])
def test_teacher_names_match_despite_titles_and_punctuation(query):
    assert assistant.resolve_teacher_name(query) == {"teacher_name": "Dr Rao"}


def test_initials_and_co_teachers_resolve():
    assert assistant.resolve_teacher_name("MRC") == {
        "teacher_name": "MANJULA CHOUGALA",
    }
    schedule = assistant.get_teacher_schedule("Manjula", "monday")
    assert [entry["subject"] for entry in schedule] == ["DS Lab"]


def test_unknown_teacher_is_an_error_not_an_empty_schedule():
    assert "error" in assistant.get_teacher_schedule("Nobody", "Monday")


def test_teacher_location_accepts_12_hour_time_and_short_day():
    found = assistant.get_teacher_location("akshatha", "Mon", "3:00 PM")
    assert [entry["subject"] for entry in found] == ["DS Lab"]
    assert "error" in assistant.get_teacher_location("rao", "Funday", "9:00")
    assert "error" in assistant.get_teacher_location("rao", "Monday", "soon")


def test_room_lookup_is_fuzzy_and_ignores_unapproved_records():
    assert assistant.resolve_room("lhc301") == {"room": "LHC 301"}
    assert [e["subject"] for e in assistant.get_room_schedule(
        "LHC-301", "Monday", "9:30")] == ["Data Structures"]
    assert assistant.get_room_schedule("LHC 301", "Monday", "11:30") == []


@pytest.mark.parametrize("name", ["3A", "CSE 3A", "3 a", "Semester 3 Section A"])
def test_class_names_resolve(name):
    assert assistant.resolve_class(name) == {"class_name": "3A"}


def test_class_timetable_and_free_slots():
    monday = assistant.get_class_timetable(class_name="CSE 3A", day="Monday")
    assert [entry["subject"] for entry in monday] == ["Data Structures", "DS Lab"]

    free = assistant.get_class_free_slots("Monday", semester=3, section="a")
    assert {"start_time": "09:55", "end_time": "14:40"}.items() <= free[0].items()

    assert "error" in assistant.get_class_timetable(class_name="9Z", day="Monday")


def test_known_data_lists_only_approved_values():
    known = assistant.list_known_timetable_data()
    assert known["teachers"] == ["AKSHATHA KAMATH", "Dr Rao", "MANJULA CHOUGALA"]
    assert known["rooms"] == ["CSE LAB 1", "LHC 301"]
    assert known["classes"] == ["3A"]
