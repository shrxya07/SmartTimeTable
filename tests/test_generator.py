import pytest

from app.database import db
from app.scheduling.generator import Requirement, check_timetable, generate_timetable
from app.scheduling.generator_data import (
    existing_commitments,
    requirements_from_class,
    teaching_days,
    teaching_periods,
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PERIODS = [
    ("09:00", "09:55"), ("09:55", "10:50"),          # break 10:50-11:05
    ("11:05", "12:00"), ("12:00", "12:55"),          # lunch 12:55-13:45
    ("13:45", "14:40"), ("14:40", "15:35"), ("15:35", "16:30"),
]


def minutes(clock):
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def by_course(result, course):
    return [r for r in result["records"] if r["course"] == course]


def test_every_session_is_placed_without_clashes():
    requirements = [
        Requirement("Data Structures", 4, teachers=["Kamath"], rooms=["LHC 301"]),
        Requirement("Maths", 3, teachers=["Uma"], rooms=["LHC 301"]),
        Requirement("OOP", 3, teachers=["Pradeep"], rooms=["LHC 301"]),
        Requirement("DS Lab", 2, periods_per_session=2, kind="lab",
                    teachers=["Kamath", "Chougala"], rooms=["CSE LAB 1"]),
    ]
    result = generate_timetable("3A", DAYS, PERIODS, requirements)

    assert result["complete"]
    assert len(result["records"]) == 4 + 3 + 3 + 2
    assert check_timetable(result["records"]) == []


def test_labs_use_consecutive_periods_and_never_cross_a_break():
    result = generate_timetable("3A", DAYS, PERIODS, [
        Requirement("Lab", 5, periods_per_session=2, kind="lab", teachers=["T"]),
    ])
    for lab in result["records"]:
        span = minutes(lab["end_time"]) - minutes(lab["start_time"])
        assert span == 110, lab  # two adjacent 55-minute periods, no break


def test_one_session_of_a_course_per_day_when_possible():
    result = generate_timetable("3A", DAYS, PERIODS, [
        Requirement("DS", 5, teachers=["Kamath"]),
        Requirement("Maths", 4, teachers=["Uma"]),
    ])
    for course in ("DS", "Maths"):
        days = [r["day"] for r in by_course(result, course)]
        assert len(days) == len(set(days))
    assert not result["relaxed_one_per_day"]


def test_other_classes_bookings_are_respected():
    # Uma teaches another class all of Monday and Tuesday morning.
    busy = {
        "teacher": {"Uma": [("Monday", (540, 990)), ("Tuesday", (540, 775))]},
        "room": {"LHC 301": [("Wednesday", (540, 650))]},
    }
    result = generate_timetable("3A", DAYS, PERIODS, [
        Requirement("Maths", 4, teachers=["Uma"], rooms=["LHC 205"]),
        Requirement("DS", 4, teachers=["Kamath"], rooms=["LHC 301"]),
    ], busy=busy)

    assert result["complete"]
    assert check_timetable(result["records"], busy) == []
    assert all(r["day"] != "Monday" for r in by_course(result, "Maths"))


def test_parallel_sections_need_all_teachers_free():
    busy = {"teacher": {"Section 2 Teacher": [(day, (540, 775)) for day in DAYS]}}
    result = generate_timetable("5A", DAYS, PERIODS, [
        Requirement("OOAD/BDA", 2, kind="elective",
                    teachers=["Section 1 Teacher", "Section 2 Teacher"],
                    rooms=["LHC 206", "LHC 207"]),
    ], busy=busy)

    assert result["complete"]
    assert all(minutes(r["start_time"]) >= 13 * 60 for r in result["records"])
    assert result["records"][0]["room"] == "LHC 206 & LHC 207"


def test_fixed_activities_and_short_days_are_kept_free():
    result = generate_timetable(
        "3A", DAYS + ["Saturday"], PERIODS,
        [Requirement("DS", 6, teachers=["Kamath"])],
        fixed=[("Thursday", "13:45", "16:30")],
        day_bounds={"Saturday": ("09:00", "12:55")},
    )
    for record in result["records"]:
        if record["day"] == "Thursday":
            assert minutes(record["end_time"]) <= minutes("13:45")
        if record["day"] == "Saturday":
            assert minutes(record["end_time"]) <= minutes("12:55")


def test_impossible_requests_are_reported_with_a_reason():
    busy = {"teacher": {"Uma": [(day, (0, 24 * 60)) for day in DAYS]}}
    result = generate_timetable("3A", DAYS, PERIODS, [
        Requirement("Maths", 2, teachers=["Uma"]),
        Requirement("Long lab", 1, periods_per_session=4, kind="lab"),
    ], busy=busy)

    assert not result["complete"]
    reasons = {item["course"]: item["reason"] for item in result["unplaced"]}
    assert "Uma" in reasons["Maths"]
    assert "consecutive" in reasons["Long lab"]


def test_over_full_week_falls_back_to_two_per_day():
    result = generate_timetable("3A", DAYS[:2], PERIODS[:4], [
        Requirement("DS", 4, teachers=["Kamath"]),
    ])
    assert result["complete"]
    assert result["relaxed_one_per_day"]


# ---------------- Inputs read from approved data ----------------

@pytest.fixture
def approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "generator.db")
    db.initialize_database()

    def add(**fields):
        entry = {
            "subject": "DS", "teacher_name": "Kamath", "class_name": "3A",
            "semester": 3, "section": "A", "room": "LHC 301",
            "review_status": "verified", "timetable_id": "t3a",
        }
        entry.update(fields)
        db.insert_entry(entry)

    for day, start, end in (("Monday", "09:00", "09:55"), ("Tuesday", "09:55", "10:50")):
        add(day=day, start_time=start, end_time=end)
    add(subject="DS Lab", entry_type="lab", room="CSE LAB 1", day="Wednesday",
        start_time="11:05", end_time="12:55",
        teachers=[{"teacher_name": "Kamath"}, {"teacher_name": "Chougala"}])
    for group, teacher, room in (("Section 1", "Sushma", "LHC 206"),
                                 ("Section 2", "Chandrika", "LHC 207")):
        add(subject="OOAD", entry_type="elective", teacher_name=teacher, room=room,
            subgroup=group, parallel_group="Thursday 13:45-14:40",
            day="Thursday", start_time="13:45", end_time="14:40")
    add(subject="Industry Talk", entry_type="activity", teacher_name=None,
        room=None, day="Friday", start_time="14:40", end_time="16:30")
    add(subject="Maths", teacher_name="Uma", class_name="5A", semester=5,
        day="Monday", start_time="09:55", end_time="10:50", timetable_id="t5a")
    add(subject="Maths", teacher_name="Uma", class_name="5A", semester=5,
        day="Tuesday", start_time="12:00", end_time="12:55", timetable_id="t5a")


def test_requirements_are_read_from_an_approved_class(approved):
    periods = teaching_periods("3A")
    assert ("09:00", "09:55") in periods and ("10:50", "11:05") not in periods

    requirements, fixed = requirements_from_class("3A", periods)
    by_name = {r.course: r for r in requirements}
    assert by_name["DS"].sessions_per_week == 2
    assert (by_name["DS Lab"].periods_per_session, by_name["DS Lab"].teachers) == (
        2, ["Kamath", "Chougala"],
    )
    parallel = by_name["OOAD"]
    assert parallel.sessions_per_week == 1
    assert parallel.teachers == ["Sushma", "Chandrika"]
    assert parallel.rooms == ["LHC 206", "LHC 207"]
    assert fixed == [("Friday", "14:40", "16:30")]


def test_regenerating_a_class_ignores_its_own_bookings(approved):
    busy = existing_commitments(exclude_class="3A")
    assert set(busy["teacher"]) == {"Uma"}

    days, bounds = teaching_days()
    periods = teaching_periods("3A")
    requirements, fixed = requirements_from_class("3A", periods)
    result = generate_timetable("3A", days, periods, requirements,
                                busy=busy, fixed=fixed, day_bounds=bounds)
    assert result["complete"]
    assert check_timetable(result["records"], busy) == []
