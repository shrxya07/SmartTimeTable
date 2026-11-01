
import pytest

from app.database import db


@pytest.fixture
def registry_database(tmp_path, monkeypatch):
    """Use an isolated database for registry tests."""
    test_db_path = tmp_path / "registry_test.db"

    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)

    db.initialize_database()
    return test_db_path


def test_register_and_retrieve_timetable(registry_database):
    timetable_id = "registry_test"

    db.register_timetable(
        timetable_id=timetable_id,
        original_filename="sample.png",
        stored_path="sample.png",
        semester=5,
        section="E",
        academic_year="2026",
        term="Odd",
    )

    timetable = db.get_timetable(timetable_id)

    assert timetable is not None
    assert timetable["section"] == "E"
    assert timetable["semester"] == 5


def test_update_timetable_status(registry_database):
    timetable_id = "registry_status_test"

    db.register_timetable(
        timetable_id=timetable_id,
        original_filename="sample.png",
        stored_path="sample.png",
        semester=5,
        section="E",
        academic_year="2026",
        term="Odd",
    )

    db.update_timetable_status(timetable_id, "processing")

    timetable = db.get_timetable(timetable_id)
    assert timetable is not None
    assert timetable["status"] == "processing"


def test_list_timetables(registry_database):
    timetable_id = "registry_list_test"

    db.register_timetable(
        timetable_id=timetable_id,
        original_filename="sample.png",
        stored_path="sample.png",
        semester=5,
        section="E",
        academic_year="2026",
        term="Odd",
    )

    timetables = db.get_all_timetables()

    assert any(
        item["timetable_id"] == timetable_id
        for item in timetables
    )


def test_delete_timetable(registry_database):
    timetable_id = "registry_delete_test"

    db.register_timetable(
        timetable_id=timetable_id,
        original_filename="sample.png",
        stored_path="sample.png",
        semester=5,
        section="E",
        academic_year="2026",
        term="Odd",
    )

    db.delete_timetable(timetable_id)

    assert db.get_timetable(timetable_id) is None
