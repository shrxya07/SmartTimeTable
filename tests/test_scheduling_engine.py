
import pytest

from app.database import db
from app.scheduling.engine import (
    find_teacher_schedule,
    find_teacher_at_time,
    find_class_schedule,
    find_room_at_time,
    detect_conflicts,
    find_free_slots,
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
):
    return db.insert_entry({
        "teacher_name": teacher,
        "subject": subject,
        "class_name": class_name,
        "day": day,
        "start_time": start,
        "end_time": end,
        "room": room,
        "review_status": "verified",
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


def test_free_slots_respect_configured_breaks():
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