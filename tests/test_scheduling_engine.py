
import pytest
from app.database import db
from app.scheduling.engine import (
    find_teacher_schedule,
    find_teacher_at_time,
    find_class_schedule,
    find_room_at_time,
    detect_conflicts,
    find_free_slots,
    find_room_free_slots,
    find_section_free_slots,
    find_common_free_slots,
)


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    """Use a fresh temporary database for every test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_timetable.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize_database()
    return tmp_path / "test_timetable.db"


def add_entry(
    teacher="Dr Rao",
    subject="DBMS",
    class_name="CSE-A",
    day="Monday",
    start="09:00",
    end="10:00",
    room="Room 301",
    semester=None,
    section=None,
    review_status="verified",
):
    return db.insert_entry({
        "teacher_name": teacher,
        "subject": subject,
        "class_name": class_name,
        "day": day,
        "start_time": start,
        "end_time": end,
        "room": room,
        "semester": semester,
        "section": section,
        "review_status": review_status,
    })


def test_teacher_schedule_is_ordered_and_case_insensitive(test_database):
    add_entry(start="11:00", end="12:00", subject="Networks")
    add_entry(start="09:00", end="10:00", subject="DBMS")
    add_entry(day="Tuesday", start="13:00", end="14:00")

    result = find_teacher_schedule("  dr rao ", "monday")

    assert [entry["subject"] for entry in result] == ["DBMS", "Networks"]


def test_teacher_at_time_uses_start_inclusive_end_exclusive(test_database):
    add_entry(start="09:00", end="10:00")

    assert len(find_teacher_at_time("Dr Rao", "Monday", "09:00")) == 1
    assert len(find_teacher_at_time("Dr Rao", "Monday", "09:59")) == 1
    assert find_teacher_at_time("Dr Rao", "Monday", "10:00") == []


def test_class_and_room_lookup(test_database):
    add_entry(teacher="Dr Rao", class_name="CSE-A", room="Room 301")
    add_entry(
        teacher="Dr Sen",
        subject="Networks",
        class_name="CSE-B",
        start="10:00",
        end="11:00",
        room="Room 302",
    )

    assert len(find_class_schedule("cse-a", "monday")) == 1
    assert len(find_room_at_time("room 301", "Monday", "09:30")) == 1
    assert find_room_at_time("Room 301", "Monday", "10:00") == []


def test_detects_teacher_conflict(test_database):
    add_entry(
        teacher="Dr Rao",
        class_name="CSE-A",
        room="Room 301",
        start="09:00",
        end="10:00",
    )
    add_entry(
        teacher="Dr Rao",
        subject="Networks",
        class_name="CSE-B",
        room="Room 302",
        start="09:30",
        end="10:30",
    )

    conflicts = detect_conflicts("Monday")

    assert len(conflicts) == 1
    assert "teacher" in conflicts[0]["conflict_types"]


def test_back_to_back_classes_are_not_a_conflict(test_database):
    add_entry(start="09:00", end="10:00", room="Room 301")
    add_entry(
        teacher="Dr Sen",
        class_name="CSE-B",
        room="Room 302",
        start="10:00",
        end="11:00",
    )

    assert detect_conflicts("Monday") == []


def test_free_slots_respect_working_hours(test_database):
    add_entry(start="10:00", end="11:00")
    add_entry(start="13:00", end="14:00")

    slots = find_free_slots(
        teacher_name="Dr Rao",
        day="Monday",
        min_duration_minutes=60,
        working_start="09:00",
        working_end="15:00",
    )

    assert slots == [
        {
            "start_time": "09:00",
            "end_time": "10:00",
            "duration_minutes": 60,
        },
        {
            "start_time": "11:00",
            "end_time": "13:00",
            "duration_minutes": 120,
        },
        {
            "start_time": "14:00",
            "end_time": "15:00",
            "duration_minutes": 60,
        },
    ]


def test_free_slots_reject_invalid_duration(test_database):
    with pytest.raises(ValueError, match="Minimum duration must be positive"):
        find_free_slots("Dr Rao", "Monday", min_duration_minutes=0)


def test_free_slots_respect_configured_breaks(test_database):
    add_entry(start="09:00", end="09:55")

    slots = find_free_slots(
        teacher_name="Dr Rao",
        day="Monday",
        min_duration_minutes=60,
        working_start="09:00",
        working_end="13:00",
        breaks=[
            ("10:50", "11:05"),
            ("12:55", "13:45"),
        ],
    )

    assert slots == [
        {
            "start_time": "11:05",
            "end_time": "12:55",
            "duration_minutes": 110,
        }
    ]

def test_room_free_slots(test_database):
    add_entry(start="10:00", end="11:00", room="Room 301")
    add_entry(start="13:00", end="14:00", room="Room 301")

    slots = find_room_free_slots(
        "Room 301",
        "Monday",
        working_start="09:00",
        working_end="15:00",
    )

    assert slots == [
        {"start_time": "09:00", "end_time": "10:00", "duration_minutes": 60},
        {"start_time": "11:00", "end_time": "13:00", "duration_minutes": 120},
        {"start_time": "14:00", "end_time": "15:00", "duration_minutes": 60},
    ]


def test_section_free_slots(test_database):
    add_entry(class_name="CSE-A", start="10:00", end="11:00")

    slots = find_section_free_slots(
        "CSE-A",
        "Monday",
        working_start="09:00",
        working_end="12:00",
    )

    assert slots == [
        {"start_time": "09:00", "end_time": "10:00", "duration_minutes": 60},
        {"start_time": "11:00", "end_time": "12:00", "duration_minutes": 60},
    ]


def test_common_free_slots_for_teachers(test_database):
    add_entry(teacher="Dr Rao", start="09:00", end="10:00")
    add_entry(teacher="Dr Sen", start="10:00", end="11:00")

    slots = find_common_free_slots(
        ["Dr Rao", "Dr Sen"],
        "teacher",
        "Monday",
        working_start="09:00",
        working_end="13:00",
    )

    assert slots == [
        {"start_time": "11:00", "end_time": "13:00", "duration_minutes": 120}
    ]


def test_common_free_slots_for_sections(test_database):
    add_entry(class_name="CSE-A", start="09:00", end="10:00")
    add_entry(class_name="CSE-B", start="10:00", end="11:00")

    slots = find_common_free_slots(
        ["CSE-A", "CSE-B"],
        "section",
        "Monday",
        working_start="09:00",
        working_end="13:00",
    )

    assert slots == [
        {"start_time": "11:00", "end_time": "13:00", "duration_minutes": 120}
    ]


def test_common_free_slots_respect_semester(test_database):
    add_entry(
        teacher="Dr Rao",
        start="09:00",
        end="10:00",
        semester=3,
    )
    add_entry(
        teacher="Dr Sen",
        start="10:00",
        end="11:00",
        semester=3,
    )

    # These Semester 5 entries must not affect the result.
    add_entry(
        teacher="Dr Rao",
        start="11:00",
        end="12:00",
        semester=5,
    )
    add_entry(
        teacher="Dr Sen",
        start="11:00",
        end="12:00",
        semester=5,
    )

    slots = find_common_free_slots(
        ["Dr Rao", "Dr Sen"],
        "teacher",
        "Monday",
        semester=3,
        working_start="09:00",
        working_end="13:00",
    )

    assert slots == [
        {"start_time": "11:00", "end_time": "13:00", "duration_minutes": 120}
    ]


def test_unverified_records_excluded_by_default(test_database):
    add_entry(
        teacher="Dr Rao",
        start="09:00",
        end="10:00",
        review_status="needs_review",
    )

    slots = find_free_slots(
        "Dr Rao",
        "Monday",
        working_start="09:00",
        working_end="11:00",
    )

    assert slots == [
        {"start_time": "09:00", "end_time": "11:00", "duration_minutes": 120}
    ]
