import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import genai_assistant
from app.database import db


APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "streamlit_app.py"
)


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_timetable.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize_database()


def class_entry(**overrides):
    entry = {
        "teacher_name": "Dr Rao",
        "subject": "DBMS",
        "class_name": "3A",
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "09:55",
        "room": "LHC 301",
        "review_status": "verified",
        "timetable_id": "phase1",
    }
    entry.update(overrides)
    return entry


# ---------------- Time normalization ----------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [("9:00", "09:00"), ("09:05", "09:05"), (" 13:45 ", "13:45")],
)
def test_normalize_time_pads_hours(value, expected):
    assert db.normalize_time(value) == expected


@pytest.mark.parametrize("value", ["9", "9.00", "24:00", "10:60", "nine"])
def test_normalize_time_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="Invalid time"):
        db.normalize_time(value)


def test_unpadded_times_are_stored_zero_padded(test_database):
    entry_id = db.insert_entry(
        class_entry(start_time="9:00", end_time="10:00")
    )

    stored = next(
        entry for entry in db.get_all_entries() if entry["id"] == entry_id
    )
    assert stored["start_time"] == "09:00"
    assert stored["end_time"] == "10:00"


# ---------------- Teacher resolution ----------------

def test_resolve_teacher_ignores_null_teachers_and_uses_associations(
    test_database,
):
    db.insert_entry({
        "subject": "Industry Talk",
        "class_name": "3A",
        "day": "Monday",
        "start_time": "14:40",
        "end_time": "16:30",
        "entry_type": "activity",
        "review_status": "verified",
        "timetable_id": "phase1",
    })
    db.insert_entry(class_entry(
        teachers=[
            {"teacher_name": "Dr Rao"},
            {"teacher_name": "Asha Bhat", "initials": "ASB"},
        ],
    ))
    db.insert_entry(class_entry(
        teacher_name="Pending Teacher",
        review_status="needs_review",
        start_time="10:00",
        end_time="10:55",
    ))

    assert genai_assistant.resolve_teacher_name("rao") == {
        "teacher_name": "Dr Rao",
    }
    assert genai_assistant.resolve_teacher_name("Asha") == {
        "teacher_name": "Asha Bhat",
    }
    assert genai_assistant.resolve_teacher_name("asb") == {
        "teacher_name": "Asha Bhat",
    }
    assert "error" in genai_assistant.resolve_teacher_name("Pending")

    schedule = genai_assistant.get_teacher_schedule("Asha", "Monday")
    assert [entry["subject"] for entry in schedule] == ["DBMS"]


# ---------------- Lazy Gemini initialization ----------------

def test_ai_features_are_disabled_without_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(genai_assistant, "_client", None)

    assert genai_assistant.is_ai_available() is False
    assert genai_assistant.create_chat_session() is None
    assert (
        genai_assistant.ask_timetable_assistant("Who teaches DBMS?", None)
        == genai_assistant.AI_UNAVAILABLE_MESSAGE
    )
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        genai_assistant.gemini_vision_client(b"", "image/png", "prompt", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        genai_assistant.get_client()


@pytest.mark.parametrize(
    ("api_key", "expect_warning"),
    [("", True), ("placeholder-key-for-startup-test", False)],
)
def test_app_starts_outside_project_root_with_and_without_key(
    tmp_path, api_key, expect_warning,
):
    """Mirror `streamlit run`: the project root is not on sys.path."""
    script = f"""
from streamlit.testing.v1 import AppTest
app = AppTest.from_file({str(APP_PATH)!r}, default_timeout=60).run()
assert not app.exception, [error.value for error in app.exception]
warned = any("GEMINI_API_KEY" in item.value for item in app.warning)
assert warned is {expect_warning!r}, [item.value for item in app.warning]
print("STARTED")
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH"
    }
    env["GEMINI_API_KEY"] = api_key
    env["SMARTTIMETABLE_DB_PATH"] = str(tmp_path / "startup.db")
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "STARTED" in result.stdout
    assert (tmp_path / "startup.db").is_file()
