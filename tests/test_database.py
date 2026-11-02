
import sqlite3

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
    """The timetable and new association tables should be created."""
    with db.get_connection() as connection:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

    assert any(row["name"] == "timetable_entries" for row in tables)
    assert any(row["name"] == "timetable_courses" for row in tables)
    assert any(row["name"] == "entry_teachers" for row in tables)


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


def test_activity_entry_accepts_null_teacher_and_room(test_database):
    entry_id = db.insert_entry({
        "teacher_name": "UNKNOWN",
        "subject": "Industry talk",
        "class_name": "Semester 5 Section A",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "NONE",
        "entry_type": "activity",
    })

    entry = next(
        entry for entry in db.get_all_entries()
        if entry["id"] == entry_id
    )
    assert entry["teacher_name"] is None
    assert entry["room"] is None


def test_insert_helpers_persist_offerings_and_deduplicate_teachers(
    test_database,
):
    entry_id = db.insert_entry({
        "teacher_name": "Dr Rao",
        "teachers": [
            {"teacher_name": "Dr Rao", "initials": "DR"},
            {"teacher_name": "Dr Sen", "initials": "DS"},
            {"teacher_name": "dr sen", "initials": "ds"},
        ],
        "subject": "DBMS",
        "class_name": "CSE-A",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "Room 301",
    })
    associations = db.insert_entry_teachers(
        entry_id,
        [{"teacher_name": "DR SEN", "initials": "DS"}],
    )
    offering_ids = db.insert_course_offerings(
        "test_timetable",
        [{
            "course_name": "Database Systems",
            "abbrev": "DBMS",
            "kind": "lecture",
            "section_label": "A",
            "faculty_name": "Dr Rao",
            "faculty_initials": "DR",
            "room": "Room 301",
            "source_bbox": [1, 2, 3, 4],
        }],
    )

    with db.get_connection() as connection:
        teacher_rows = connection.execute(
            "SELECT teacher_name, initials FROM entry_teachers "
            "WHERE entry_id = ? ORDER BY teacher_name",
            (entry_id,),
        ).fetchall()
        course = connection.execute(
            "SELECT * FROM timetable_courses WHERE id = ?",
            (offering_ids[0],),
        ).fetchone()

    assert associations == 0
    assert len(teacher_rows) == 2
    assert course["abbrev"] == "DBMS"
    assert course["source_bbox"] == "[1, 2, 3, 4]"


def test_old_schema_migration_preserves_rows_constraints_and_objects(
    tmp_path, monkeypatch,
):
    database_path = tmp_path / "legacy_timetable.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)

    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            CREATE TABLE timetable_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                semester INTEGER,
                section TEXT,
                timetable_id TEXT,
                teacher_name TEXT NOT NULL,
                subject TEXT NOT NULL,
                class_name TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                room TEXT NOT NULL,
                original_room_text TEXT,
                source_document TEXT,
                source_page INTEGER,
                review_status TEXT NOT NULL DEFAULT 'verified',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (semester IN (1, 2, 3, 4, 5, 6, 7, 8)),
                CHECK (day IN (
                    'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                    'Friday', 'Saturday', 'Sunday'
                )),
                CHECK (start_time < end_time),
                CHECK (review_status IN ('verified', 'needs_review'))
            );
            CREATE INDEX idx_teacher_day
            ON timetable_entries (teacher_name, day);
            CREATE INDEX idx_class_day
            ON timetable_entries (semester, section, day);
            CREATE INDEX idx_room_day
            ON timetable_entries (room, day);
            CREATE INDEX idx_timetable_id
            ON timetable_entries (timetable_id);
            CREATE TABLE timetables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timetable_id TEXT NOT NULL UNIQUE,
                original_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                semester INTEGER,
                section TEXT,
                academic_year TEXT,
                term TEXT,
                status TEXT NOT NULL DEFAULT 'uploaded',
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (semester IS NULL OR semester BETWEEN 1 AND 8),
                CHECK (status IN (
                    'uploaded', 'processing', 'needs_review', 'ready', 'failed'
                ))
            );
            CREATE INDEX idx_timetables_semester_section
            ON timetables (semester, section);
            CREATE INDEX idx_timetables_status
            ON timetables (status);
            CREATE INDEX legacy_subject_index
            ON timetable_entries (subject);
            CREATE TABLE legacy_entry_child (
                entry_id INTEGER REFERENCES timetable_entries(id)
            );
            CREATE TABLE migration_audit (entry_id INTEGER);
            CREATE TRIGGER legacy_entry_update
            AFTER UPDATE OF review_status ON timetable_entries
            BEGIN
                INSERT INTO migration_audit VALUES (NEW.id);
            END;
            INSERT INTO timetable_entries (
                id, semester, section, timetable_id, teacher_name, subject,
                class_name, day, start_time, end_time, room, review_status
            ) VALUES (
                41, 5, 'A', 'legacy', 'Dr Rao', 'DBMS', 'CSE-A',
                'Monday', '09:00', '10:00', 'Room 301', 'verified'
            );
            INSERT INTO legacy_entry_child VALUES (41);
        """)

    db.initialize_database()
    db.initialize_database()

    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM timetable_entries WHERE id = 41"
        ).fetchone()
        columns = {
            column["name"]: column
            for column in connection.execute(
                "PRAGMA table_info(timetable_entries)"
            )
        }
        index = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'index' AND name = 'legacy_subject_index'
            """
        ).fetchone()
        trigger = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'trigger' AND name = 'legacy_entry_update'
            """
        ).fetchone()
        foreign_key = connection.execute(
            "PRAGMA foreign_key_list(legacy_entry_child)"
        ).fetchone()

        assert row["teacher_name"] == "Dr Rao"
        assert row["room"] == "Room 301"
        assert columns["teacher_name"]["notnull"] == 0
        assert columns["room"]["notnull"] == 0
        assert index is not None
        assert trigger is not None
        assert foreign_key["table"] == "timetable_entries"
        assert "entry_type" in columns
        assert "review_tier" in columns
        migrated_indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(timetable_entries)"
            )
        }
        assert "idx_timetable_entries_parallel_group" in migrated_indexes

        connection.execute(
            "UPDATE timetable_entries SET review_status = 'needs_review' "
            "WHERE id = 41"
        )
        audit = connection.execute(
            "SELECT entry_id FROM migration_audit"
        ).fetchone()

    assert audit["entry_id"] == 41

    next_entry_id = db.insert_entry({
        "teacher_name": "Dr Sen",
        "subject": "Networks",
        "class_name": "CSE-A",
        "day": "Tuesday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "Room 302",
    })
    assert next_entry_id > 41

    with pytest.raises(sqlite3.IntegrityError):
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE timetable_entries SET semester = 9 WHERE id = 41"
            )

    assert db.get_all_entries()[0]["id"] == 41
