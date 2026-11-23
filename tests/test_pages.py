from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.database import db
from app.database.retrieval import get_timetable_entries

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pages.db")
    db.initialize_database()
    db.register_timetable("t3a", "sem3a.png", "sem3a.png", 3, "A",
                          status="ready", file_sha256="abc123")
    db.save_timetable_layout("t3a", {
        "day_start": "09:00", "day_end": "12:55",
        "breaks": [["10:50", "11:05"]],
        "periods": [["09:00", "09:55"], ["09:55", "10:50"],
                    ["11:05", "12:00"], ["12:00", "12:55"]],
    })
    for day, start, end, subject, teacher in (
        ("Monday", "09:00", "09:55", "DS", "Kamath"),
        ("Monday", "09:00", "09:55", "OOP", "Kamath"),  # deliberate clash
        ("Monday", "12:00", "12:55", "Maths", "Uma"),
        ("Tuesday", "11:05", "12:00", "Maths", "Uma"),
        ("Tuesday", "12:00", "12:55", "OOP", "Kamath"),
    ):
        db.insert_entry({
            "subject": subject, "teacher_name": teacher, "class_name": "3A",
            "semester": 3, "section": "A", "day": day, "start_time": start,
            "end_time": end, "room": "LHC 301", "review_status": "verified",
            "timetable_id": "t3a",
        })
    db.save_pending_review("t3a", {"records": [{"day": "Friday"}]})


def open_page(name):
    app = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    app.sidebar.radio[0].set_value(name).run()
    assert not app.exception, [error.value for error in app.exception]
    return app


def texts(app):
    return " ".join(
        str(element.value)
        for kind in ("success", "error", "warning", "info", "caption", "markdown")
        for element in getattr(app, kind)
    )


def test_manage_page_lists_timetables_and_deletes(seeded):
    app = open_page("Manage Timetables")
    table = app.dataframe[0].value
    assert table.loc[0, "Approved records"] == 5
    assert table.loc[0, "Awaiting review"] == 1

    app.checkbox(key="confirm_delete_t3a").check().run()
    app.button(key="delete_t3a").click().run()
    assert not app.exception
    assert db.get_timetable_overview() == []
    assert get_timetable_entries("t3a") == []


def test_explorer_page_shows_schedules_free_time_and_clashes(seeded):
    app = open_page("Schedules & Conflicts")
    content = texts(app)
    assert "1 clash" in content
    free_table = app.dataframe[1].value
    monday = free_table[free_table["Day"] == "Monday"]["Free"].iloc[0]
    assert monday == "09:55-10:50, 11:05-12:55"  # 10:50 break skipped


def test_duplicate_upload_lookup_and_overview(seeded):
    assert [t["timetable_id"] for t in db.find_timetables_by_hash("abc123")] == ["t3a"]
    assert db.find_timetables_by_hash("other") == []
    db.update_timetable_status("t3a", "failed")
    assert db.find_timetables_by_hash("abc123") == []
