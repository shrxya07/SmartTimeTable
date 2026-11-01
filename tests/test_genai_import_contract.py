import pytest

from app.database import db
from app.ingestion.importer import import_timetable_records


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_timetable.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize_database()


def make_record(**overrides):
    record = {
        "teacher_name": "Dr Rao",
        "subject": "DBMS",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "10:00",
        "room": "UNKNOWN",
        "review_status": "verified",
    }
    record.update(overrides)
    return record


def test_real_importer_derives_class_name_and_uses_default_room(isolated_database):
    result = import_timetable_records(
        [make_record()],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="contract_test",
    )

    assert result[0]["status"] == "imported"
    entries = [
        row for row in db.get_all_entries()
        if row["timetable_id"] == "contract_test"
    ]
    assert len(entries) == 1
    assert entries[0]["class_name"] == "Semester 3 Section A"
    assert entries[0]["room"] == "LHC 301"


def test_unknown_teacher_is_rejected_without_replacing_existing_data(
    isolated_database,
):
    good = make_record(room="Room 301")
    first = import_timetable_records(
        [good],
        semester=3,
        section="A",
        timetable_id="contract_test",
    )
    assert first[0]["status"] == "imported"

    result = import_timetable_records(
        [make_record(teacher_name="UNKNOWN")],
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id="contract_test",
    )

    assert result[-1]["status"] == "not_imported"
    entries = [
        row for row in db.get_all_entries()
        if row["timetable_id"] == "contract_test"
    ]
    assert len(entries) == 1
    assert entries[0]["teacher_name"] == "Dr Rao"
