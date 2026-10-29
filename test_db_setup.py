
import sqlite3

import pytest

from app.database import db


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    """Create an isolated temporary database for each test."""
    test_db_path = tmp_path / "test_timetable.db"

    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)

    db.initialize_database()
    return test_db_path


def test_database_initializes(test_database):
    """The timetable table should be created."""
    with db.get_connection() as connection:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

    assert any(row["name"] == "timetable_entries" for row in tables)


def test_insert_and_retrieve_entry(test_database):
    """A valid entry should be saved and retrievable."""
    sample_entry = {
        "teacher_name": "Dr Rao",
        "subject": "DBMS",
        "class_name": "CSE-A",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "Room 301",
        "source_document": "sample_timetable.pdf",
        "source_page": 1,
        "review_status": "verified",
    }

    entry_id = db.insert_entry(sample_entry)

    assert entry_id is not None

    entries = db.get_all_entries()

    assert len(entries) == 1
    assert entries[0]["id"] == entry_id
    assert entries[0]["teacher_name"] == "Dr Rao"
    assert entries[0]["subject"] == "DBMS"
    assert entries[0]["room"] == "Room 301"


def test_invalid_day_is_rejected(test_database):
    """Invalid day names should not be inserted."""
    sample_entry = {
        "teacher_name": "Dr Rao",
        "subject": "DBMS",
        "class_name": "CSE-A",
        "day": "Funday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "Room 301",
    }

    with pytest.raises(ValueError, match="Invalid day name"):
        db.insert_entry(sample_entry)

    assert db.get_all_entries() == []
