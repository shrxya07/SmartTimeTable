
import pytest

from app.database import db
from app.ingestion.importer import import_timetable_records


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_timetable.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize_database()
    return tmp_path / "test_timetable.db"


def valid_record(**overrides):
    record = {
        "teacher_name": "Dr Rao",
        "subject": "DBMS",
        "class_name": "CSE-A",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "Room 301",
        "review_status": "verified",
    }
    record.update(overrides)
    return record


def test_import_requires_timetable_id(test_database):
    with pytest.raises(ValueError, match="timetable_id is required"):
        import_timetable_records(
            [valid_record()],
            default_room="LHC 301",
        )


def test_import_rejects_missing_fields(test_database):
    record = valid_record()
    del record["teacher_name"]

    result = import_timetable_records(
        [record],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="test_sem3_a",
    )

    assert result[0]["status"] == "needs_review"
    assert "teacher_name" in result[0]["reason"]
    assert result[-1]["status"] == "not_imported"
    assert db.get_all_entries() == []


def test_import_and_replace_timetable(test_database):
    first = valid_record(subject="DBMS")

    result = import_timetable_records(
        [first],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="test_sem3_a",
    )

    assert result[0]["status"] == "imported"
    assert result[0]["room"] == "Room 301"

    replacement = valid_record(
        subject="Networks",
        room=None,
        start_time="10:00",
        end_time="11:00",
    )

    result = import_timetable_records(
        [replacement],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="test_sem3_a",
    )

    assert result[0]["status"] == "imported"

    entries = db.get_all_entries()
    matching = [
        entry for entry in entries
        if entry["timetable_id"] == "test_sem3_a"
    ]

    assert len(matching) == 1
    assert matching[0]["subject"] == "Networks"
    assert matching[0]["room"] == "LHC 301"
def test_empty_import_does_not_delete_existing_timetable(test_database):
    import_timetable_records(
        [valid_record()],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="test_sem3_a",
    )

    result = import_timetable_records(
        [],
        timetable_id="test_sem3_a",
    )

    assert result[0]["status"] == "not_imported"

    entries = db.get_all_entries()
    assert len(entries) == 1
    assert entries[0]["subject"] == "DBMS"


def test_real_importer_derives_missing_class_name(test_database):
    record = valid_record()
    del record["class_name"]

    result = import_timetable_records(
        [record],
        semester=5,
        section="A",
        timetable_id="class_name_derived",
    )

    assert result[0]["status"] == "imported"
    imported = db.get_all_entries()[0]
    assert imported["class_name"] == "Semester 5 Section A"


def test_activity_imports_with_null_teacher_and_room(test_database):
    activity = {
        "entry_type": "activity",
        "subject": "Industry talk",
        "teacher_name": "UNKNOWN",
        "room": "NONE",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
    }

    result = import_timetable_records(
        [activity],
        semester=5,
        section="A",
        timetable_id="activity_import",
    )

    assert result[0]["status"] == "imported"
    imported = db.get_all_entries()[0]
    assert imported["teacher_name"] is None
    assert imported["room"] is None
    assert imported["class_name"] == "Semester 5 Section A"


def test_multiple_teachers_import_with_their_entry(test_database):
    record = valid_record(
        teachers=[
            {"teacher_name": "Dr Rao", "initials": "DR"},
            {"teacher_name": "Dr Sen", "initials": "DS"},
        ],
    )

    result = import_timetable_records(
        [record],
        semester=5,
        section="A",
        timetable_id="multiple_teachers",
    )

    assert result[0]["status"] == "imported"
    with db.get_connection() as connection:
        teachers = connection.execute(
            """
            SELECT teacher_name FROM entry_teachers
            WHERE entry_id = ?
            ORDER BY teacher_name
            """,
            (result[0]["entry_id"],),
        ).fetchall()
    assert [teacher["teacher_name"] for teacher in teachers] == [
        "Dr Rao",
        "Dr Sen",
    ]