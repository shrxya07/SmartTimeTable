from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import app.database.db as database


APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "streamlit_app.py"
)


def review_record(
    *,
    tier="check",
    entry_type="class",
    review_status="needs_review",
):
    return {
        "day": "Monday",
        "start_time": "09:00",
        "end_time": "09:55",
        "subject": "Data Structures",
        "course": "Data Structures",
        "teacher_name": "Alice Smith",
        "teachers": [{"teacher_name": "Alice Smith", "initials": "AS"}],
        "room": "LHC 206",
        "class_name": "5A",
        "entry_type": entry_type,
        "subgroup": "S1",
        "review_tier": tier,
        "resolution_method": "exact",
        "source_document": "fixture.png",
        "source_page": 1,
        "source_bbox": {"x": 1, "y": 2, "width": 30, "height": 20},
        "legend_source_bbox": {
            "x": 40,
            "y": 50,
            "width": 30,
            "height": 10,
        },
        "original_text": "DS (AS) LHC 206",
        "evidence": "DS (AS) LHC 206",
        "unresolved_fields": {},
        "review_status": review_status,
    }


def make_review_app(
    monkeypatch,
    records,
    *,
    unresolved_items=None,
    existing_entries=None,
    editor_changes=None,
):
    database_rows = list(existing_entries or [])
    inserted = []
    status_updates = []
    monkeypatch.setattr(database, "initialize_database", lambda: None)
    monkeypatch.setattr(
        database,
        "get_all_entries",
        lambda: list(database_rows),
    )
    monkeypatch.setattr(
        database,
        "insert_entry",
        lambda entry: (
            inserted.append(dict(entry)),
            database_rows.append(dict(entry)),
        ),
    )
    monkeypatch.setattr(
        database,
        "update_timetable_status",
        lambda *args: status_updates.append(args),
    )

    original_editor = st.data_editor

    def data_editor_with_changes(data, **kwargs):
        edited = original_editor(data, **kwargs)
        if editor_changes and kwargs.get("key", "").startswith(
            "review_editor_"
        ):
            for index, changes in editor_changes.items():
                for column, value in changes.items():
                    edited.loc[index, column] = value
        return edited

    monkeypatch.setattr(st, "data_editor", data_editor_with_changes)

    app = AppTest.from_file(str(APP_PATH)).run()
    app.session_state["current_timetable_id"] = "review-test"
    app.session_state["current_timetable_metadata"] = {
        "class_name": "5A",
        "source_document": "fixture.png",
        "semester": 5,
        "section": "A",
    }
    app.session_state["review_records"] = list(records)
    app.session_state["review_unresolved_items"] = list(
        unresolved_items or []
    )
    app.session_state["review_extraction_result"] = {}
    app.sidebar.radio[0].set_value("Review Extracted Records").run()
    assert not app.exception
    return app, inserted, database_rows, status_updates


def click_button(app, label_prefix):
    button = next(
        item for item in app.button
        if item.label.startswith(label_prefix)
    )
    button.click().run()
    assert not app.exception


def test_auto_records_require_bulk_selection_then_explicit_confirmation(
    monkeypatch,
):
    app, inserted, _, _ = make_review_app(
        monkeypatch,
        [
            review_record(tier="auto"),
            review_record(tier="check"),
        ],
    )

    editor = app.dataframe[0].value
    assert not bool(editor.loc[0, "Approve"])
    assert not bool(editor.loc[1, "Approve"])
    click_button(app, "Select all auto-tier records")
    assert inserted == []
    editor = app.dataframe[0].value
    assert bool(editor.loc[0, "Approve"])
    assert not bool(editor.loc[1, "Approve"])

    click_button(app, "Confirm approval and save selected records")

    assert len(inserted) == 1
    assert inserted[0]["review_status"] == "verified"
    assert app.session_state["review_records"][0]["review_tier"] == "check"


def test_check_record_can_be_individually_edited_and_approved(monkeypatch):
    changes = {
        0: {
            "Approve": True,
            "course": "Edited Course",
            "teacher_name": "Edited Teacher",
            "teachers": "Edited Teacher, Second Teacher",
            "room": "LHC 999",
            "subgroup": "S2",
            "start_time": "10:00",
            "end_time": "10:55",
        },
    }
    unresolved = review_record(tier="check")
    unresolved.update({
        "course": None,
        "subject": None,
        "teacher_name": None,
        "teachers": [],
        "room": None,
        "unresolved_fields": {
            "course": {"status": "unresolved"},
            "teacher_name": {"status": "unresolved"},
            "room": {"status": "unresolved"},
        },
    })
    app, inserted, _, _ = make_review_app(
        monkeypatch,
        [unresolved],
        unresolved_items=[{
            "line": unresolved["original_text"],
            "source_bbox": unresolved["source_bbox"],
            "reason": "No unambiguous course match in legend.",
        }],
        editor_changes=changes,
    )

    assert app.dataframe[0].value.loc[0, "review_tier"] == "check"
    assert {
        "source_bbox",
        "legend_source_bbox",
        "original_text",
        "resolution_method",
    } <= set(app.dataframe[0].value.columns)
    app.run()
    confirm_button = next(
        button for button in app.button
        if button.label.startswith("Confirm approval")
    )
    assert not confirm_button.proto.disabled
    click_button(app, "Confirm approval and save selected records")

    assert len(inserted) == 1
    entry = inserted[0]
    assert entry["course"] == entry["subject"] == "Edited Course"
    assert entry["teacher_name"] == "Edited Teacher"
    assert [item["teacher_name"] for item in entry["teachers"]] == [
        "Edited Teacher",
        "Second Teacher",
    ]
    assert entry["room"] == "LHC 999"
    assert entry["subgroup"] == "S2"
    assert entry["start_time"] == "10:00"
    assert entry["end_time"] == "10:55"
    assert entry["review_status"] == "verified"
    assert app.session_state["review_unresolved_items"] == []


@pytest.mark.parametrize("entry_type", ["activity", "break"])
def test_activity_and_break_can_be_saved_without_teacher_or_room(
    monkeypatch,
    entry_type,
):
    record = review_record(tier="check", entry_type=entry_type)
    record.update({
        "subject": entry_type.title(),
        "course": entry_type.title(),
        "teacher_name": None,
        "teachers": [],
        "room": None,
    })
    app, inserted, _, _ = make_review_app(
        monkeypatch,
        [record],
        editor_changes={0: {"Approve": True}},
    )
    app.run()
    click_button(app, "Confirm approval and save selected records")

    assert len(inserted) == 1
    assert inserted[0]["entry_type"] == entry_type
    assert inserted[0]["teacher_name"] is None
    assert inserted[0]["teachers"] == []
    assert inserted[0]["room"] is None
    assert inserted[0]["review_status"] == "verified"


def test_unresolved_items_are_visible_without_review_records(monkeypatch):
    unresolved = [{
        "reason": "No unambiguous course match in legend.",
        "line": "unreadable code",
        "source_bbox": {"x": 1, "y": 2, "width": 30, "height": 20},
    }]
    app, _, _, _ = make_review_app(
        monkeypatch,
        [],
        unresolved_items=unresolved,
    )

    assert app.json
    assert "unreadable code" in str(app.json[0].value)
    assert not app.dataframe


def test_duplicate_approval_attempt_does_not_insert_twice(monkeypatch):
    record = review_record(tier="auto")
    app, inserted, database_rows, _ = make_review_app(
        monkeypatch,
        [record],
    )
    click_button(app, "Select all auto-tier records")
    click_button(app, "Confirm approval and save selected records")
    assert len(inserted) == 1

    app.session_state["review_records"] = [record]
    app.session_state["review_editor_revision"] += 1
    app.run()
    click_button(app, "Select all auto-tier records")
    click_button(app, "Confirm approval and save selected records")

    assert len(database_rows) == 1
    assert len(inserted) == 1
    assert app.session_state["review_records"] == []


def test_unverified_duplicate_keeps_its_existing_approval_status(monkeypatch):
    record = review_record(tier="auto")
    existing = {
        **record,
        "timetable_id": "review-test",
        "review_status": "needs_review",
    }
    app, inserted, database_rows, _ = make_review_app(
        monkeypatch,
        [record],
        existing_entries=[existing],
    )
    click_button(app, "Select all auto-tier records")
    click_button(app, "Confirm approval and save selected records")

    assert inserted == []
    assert len(database_rows) == 1
    assert database_rows[0]["review_status"] == "needs_review"
    assert len(app.session_state["review_records"]) == 1
    assert app.session_state["review_records"][0]["review_status"] == (
        "needs_review"
    )


def test_legacy_record_without_tier_uses_individual_review_workflow(
    monkeypatch,
):
    legacy = review_record()
    legacy.pop("review_tier")
    legacy.pop("resolution_method")
    legacy.pop("source_bbox")
    legacy.pop("legend_source_bbox")
    legacy.pop("unresolved_fields")
    app, inserted, _, _ = make_review_app(
        monkeypatch,
        [legacy],
        editor_changes={0: {"Approve": True}},
    )
    assert app.dataframe[0].value.loc[0, "review_tier"] == "check"
    app.run()
    click_button(app, "Confirm approval and save selected records")

    assert len(inserted) == 1
    assert inserted[0]["review_status"] == "verified"
    assert inserted[0]["source_bbox"] is None
