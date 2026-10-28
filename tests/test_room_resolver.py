
from app.ingestion.room_resolver import resolve_room


def test_default_room_is_used():
    assert resolve_room(
        timetable_default_room="LHC 514"
    ) == "LHC 514"


def test_explicit_room_overrides_default():
    assert resolve_room(
        cell_room="CSE Lab 3",
        timetable_default_room="LHC 514",
    ) == "CSE Lab 3"


def test_no_room_returns_none():
    assert resolve_room() is None


def test_default_is_specific_to_each_timetable():
    assert resolve_room(
        timetable_default_room="LHC 514"
    ) == "LHC 514"

    assert resolve_room(
        timetable_default_room="LHC 203"
    ) == "LHC 203"
