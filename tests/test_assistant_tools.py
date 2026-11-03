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
    assert assistant.resolve_teacher_name(query)["teacher_name"] == "Dr Rao"


def test_initials_and_co_teachers_resolve():
    assert assistant.resolve_teacher_name("MRC")["teacher_name"] == "MANJULA CHOUGALA"
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
    assert assistant.resolve_room("lhc301")["room"] == "LHC 301"
    assert [e["subject"] for e in assistant.get_room_schedule(
        "LHC-301", "Monday", "9:30")] == ["Data Structures"]
    assert assistant.get_room_schedule("LHC 301", "Monday", "11:30") == []


@pytest.mark.parametrize("name", ["3A", "CSE 3A", "3 a", "Semester 3 Section A"])
def test_class_names_resolve(name):
    assert assistant.resolve_class(name) == {"class_name": "3A"}


def test_class_timetable_and_free_slots():
    monday = assistant.get_class_timetable(class_name="CSE 3A", day="Monday")
    assert [entry["subject"] for entry in monday] == ["Data Structures", "DS Lab"]

    free = assistant.get_class_free_slots("Monday", semester=3, section="a")["free_periods"]
    assert {"start_time": "09:55", "end_time": "14:40"}.items() <= free[0].items()

    assert "error" in assistant.get_class_timetable(class_name="9Z", day="Monday")


def test_free_time_skips_inferred_breaks_and_uses_real_day_hours():
    # Busy everywhere except a 15-minute gap no class ever uses (a break).
    for start, end in (("09:55", "10:50"), ("11:05", "12:00")):
        db.insert_entry({
            "subject": "Maths", "teacher_name": "Other Teacher",
            "class_name": "5B", "day": "Tuesday", "start_time": start,
            "end_time": end, "room": "LHC 206", "review_status": "verified",
            "timetable_id": "t5b",
        })

    free = assistant.get_teacher_free_slots("rao", "Tuesday")["free_periods"]
    spans = [(slot["start_time"], slot["end_time"]) for slot in free]
    assert ("10:50", "11:05") not in spans
    assert spans[-1][1] == "16:30"  # Tuesday's last class, not 17:00


def test_free_time_from_stored_header_breaks(monkeypatch):
    db.register_timetable("t3a", "3a.png", "3a.png")
    db.save_timetable_layout("t3a", {
        "day_start": "09:00", "day_end": "16:30",
        "breaks": [["10:50", "11:05"], ["12:55", "13:45"]],
    })
    free = assistant.get_class_free_slots("Monday", class_name="3A")["free_periods"]
    spans = [(slot["start_time"], slot["end_time"]) for slot in free]
    assert spans == [("09:55", "10:50"), ("11:05", "12:55"), ("13:45", "14:40")]


def test_breaks_are_inferred_from_all_classes_for_a_sparse_class():
    # Another class shows lunch is 12:55-13:45; 3A alone has no such gap.
    for start, end in (("12:00", "12:55"), ("13:45", "14:40")):
        db.insert_entry({
            "subject": "Maths", "teacher_name": "Other", "class_name": "5B",
            "day": "Wednesday", "start_time": start, "end_time": end,
            "room": "LHC 206", "review_status": "verified", "timetable_id": "t5b",
        })
    free = assistant.get_class_free_slots("Monday", class_name="3A")["free_periods"]
    spans = [(slot["start_time"], slot["end_time"]) for slot in free]
    assert all(not (s < "13:00" < e) for s, e in spans), spans


def test_teacher_free_time_checks_every_class_however_the_name_is_written():
    # One teacher, written three ways in three timetables.
    for class_name, start, end, teacher, teachers in (
        ("5A", "09:00", "09:55", "DR.SUSHMA B", None),
        ("3B", "11:05", "12:00", "Dr Sushma B", None),
        ("7C", "13:45", "14:40", "SB", None),
        ("7C", "14:40", "15:35", "Other Teacher",
         [{"teacher_name": "Other Teacher"}, {"teacher_name": "DR SUSHMA B", "initials": "SB"}]),
    ):
        db.insert_entry({
            "subject": "OOAD", "teacher_name": teacher, "teachers": teachers,
            "class_name": class_name, "day": "Wednesday", "start_time": start,
            "end_time": end, "room": "LHC 206", "review_status": "verified",
            "timetable_id": f"t{class_name}",
        })

    assert assistant.resolve_teacher_name("Sushma")["teacher_name"] in {
        "DR.SUSHMA B", "Dr Sushma B", "DR SUSHMA B",
    }
    result = assistant.get_teacher_free_slots("dr. sushma", "Wednesday")

    busy = [(p["start_time"], p["class_name"]) for p in result["busy_periods"]]
    assert busy == [("09:00", "5A"), ("11:05", "3B"), ("13:45", "7C"), ("14:40", "7C")]
    assert result["classes_checked"] == ["3B", "5A", "7C"]
    for slot in result["free_periods"]:
        for start, _ in busy:
            assert not (slot["start_time"] <= start < slot["end_time"]), slot
    assert assistant.get_teacher_location("SB", "Wednesday", "11:30")[0]["class_name"] == "3B"
    assert sum(
        "sushma" in name.lower()
        for name in assistant.list_known_timetable_data()["teachers"]
    ) == 1


def test_gaps_between_header_periods_count_as_breaks():
    db.register_timetable("t3a", "3a.png", "3a.png")
    db.save_timetable_layout("t3a", {
        "breaks": [["12:55", "13:45"]],
        "periods": [["09:00", "09:55"], ["09:55", "10:50"], ["11:05", "12:00"],
                    ["12:00", "12:55"], ["13:45", "14:40"]],
    })
    free = assistant.get_class_free_slots("Monday", class_name="3A")["free_periods"]
    spans = [(slot["start_time"], slot["end_time"]) for slot in free]
    assert ("09:55", "10:50") in spans and ("11:05", "12:55") in spans
    assert all(not (s < "11:00" < e) for s, e in spans), spans


def test_lab_free_time_checks_every_class_however_the_lab_is_written():
    # One lab, written differently by three classes, once in a shared booking.
    for class_name, start, end, room in (
        ("3A", "09:00", "10:50", "CSE LAB1"),
        ("5D", "11:05", "12:55", "CSE LAB 1"),
        ("7C", "13:45", "14:40", "CSE LAB 3 & CSE-Lab 1"),
        ("7C", "14:40", "15:35", "CSE LAB 2"),
    ):
        db.insert_entry({
            "subject": "Lab", "teacher_name": "Someone", "class_name": class_name,
            "day": "Thursday", "start_time": start, "end_time": end, "room": room,
            "entry_type": "lab", "review_status": "verified",
            "timetable_id": f"t{class_name}",
        })

    resolved = assistant.resolve_room("cse lab 1")
    assert "error" not in resolved
    result = assistant.get_room_free_slots("Lab 1", "Thursday")

    busy = [(p["start_time"], p["class_name"]) for p in result["busy_periods"]]
    assert busy == [("09:00", "3A"), ("11:05", "5D"), ("13:45", "7C")]
    assert result["classes_checked"] == ["3A", "5D", "7C"]
    spans = [(s["start_time"], s["end_time"]) for s in result["free_periods"]]
    # Thursday's last class in this data ends at 15:35 (CSE LAB 2, not Lab 1).
    assert spans == [("14:40", "15:35")]
    assert assistant.get_room_schedule("cselab1", "Thursday", "14:00")[0]["class_name"] == "7C"
    assert sum(
        name.replace(" ", "").replace("-", "").upper() == "CSELAB1"
        for name in assistant.list_known_timetable_data()["rooms"]
    ) == 1


def test_one_lab_written_two_ways_is_a_room_clash():
    from app.scheduling.engine import detect_conflicts

    for class_name, room in (("3A", "CSE LAB1"), ("5D", "CSE LAB 1 & CSE LAB 2")):
        db.insert_entry({
            "subject": f"Lab {class_name}", "teacher_name": f"T{class_name}",
            "class_name": class_name, "day": "Friday", "start_time": "09:00",
            "end_time": "10:50", "room": room, "entry_type": "lab",
            "review_status": "verified", "timetable_id": f"t{class_name}",
        })
    clashes = [c for c in detect_conflicts(day="Friday") if "room" in c["conflict_types"]]
    assert len(clashes) == 1


def test_free_time_on_a_day_without_data_is_unknown_not_free():
    result = assistant.get_teacher_free_slots("rao", "Sunday")
    assert "note" in result and "cannot be determined" in result["note"]


def test_known_data_lists_only_approved_values():
    known = assistant.list_known_timetable_data()
    assert known["teachers"] == ["AKSHATHA KAMATH", "Dr Rao", "MANJULA CHOUGALA"]
    assert known["rooms"] == ["CSE LAB 1", "LHC 301"]
    assert known["classes"] == ["3A"]


def test_lateral_entry_sessions_do_not_make_the_class_busy():
    db.insert_entry({
        "subject": "AM31 (Lateral Entry)", "teacher_name": None,
        "class_name": "3A", "semester": 3, "section": "A", "day": "Friday",
        "start_time": "13:45", "end_time": "16:30", "room": "AB 701 & AB702",
        "entry_type": "lateral_entry", "review_status": "verified",
        "timetable_id": "t3a",
    })
    db.insert_entry({
        "subject": "DS", "teacher_name": "Dr Rao", "class_name": "3A",
        "day": "Friday", "start_time": "09:00", "end_time": "09:55",
        "room": "LHC 301", "review_status": "verified", "timetable_id": "t3a",
    })

    result = assistant.get_class_free_slots("Friday", class_name="3A")
    spans = [(slot["start_time"], slot["end_time"]) for slot in result["free_periods"]]
    assert ("09:55", "16:30") in spans
    assert result["lateral_entry_sessions"][0]["start_time"] == "13:45"
    assert "lateral" in result["note"].lower()


def test_confirmed_same_teacher_is_one_person():
    db.register_timetable("t7c", "7c.png", "7c.png")
    db.insert_course_offerings("t7c", [
        {"course_name": "CD", "faculty_name": "DR CHANDRIKA P", "faculty_initials": "CP"},
    ])
    db.register_timetable("t5a", "5a.png", "5a.png")
    db.insert_course_offerings("t5a", [
        {"course_name": "OOAD", "faculty_name": "DR. CHANDRIKA PRASAD", "faculty_initials": "CP"},
    ])
    from app.database.retrieval import get_faculty_directory
    # Before anyone confirms it, CP points at two different-looking names.
    assert len(get_faculty_directory()["CP"]["names"]) == 2

    for name, day in (("DR CHANDRIKA P", "Monday"), ("DR. CHANDRIKA PRASAD", "Tuesday")):
        db.insert_entry({
            "subject": "CD", "teacher_name": name, "class_name": "7C",
            "day": day, "start_time": "11:05", "end_time": "12:00",
            "room": "LHC 514", "review_status": "verified", "timetable_id": "t7c",
        })
    db.save_teacher_alias("DR CHANDRIKA P", "DR. CHANDRIKA PRASAD")

    assert get_faculty_directory()["CP"]["names"] == ["DR. CHANDRIKA PRASAD"]
    resolved = assistant.resolve_teacher_name("Chandrika")
    assert resolved["teacher_name"] == "DR. CHANDRIKA PRASAD"
    assert assistant.get_teacher_location("cp", "Monday", "11:30")
    assert assistant.get_teacher_location("Chandrika Prasad", "Tuesday", "11:30")
    assert sum(
        "chandrika" in name.lower()
        for name in assistant.list_known_timetable_data()["teachers"]
    ) == 1
