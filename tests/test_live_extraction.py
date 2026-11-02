"""Live accuracy check against a hand-verified timetable.

Calls the real Gemini API, so it only runs when asked:

    $env:SMARTTIMETABLE_LIVE_TESTS = "1"; python -m pytest tests/test_live_extraction.py -v
"""

import os
import re
from pathlib import Path

import pytest

from app.genai_assistant import gemini_vision_client, is_ai_available
from app.ingestion.extraction_pipeline import run_extraction_pipeline


IMAGE = Path(__file__).resolve().parents[1] / "data" / "uploads" / "cse_sem3_a.png"

pytestmark = pytest.mark.skipif(
    os.getenv("SMARTTIMETABLE_LIVE_TESTS") != "1"
    or not is_ai_available()
    or not IMAGE.is_file(),
    reason="Set SMARTTIMETABLE_LIVE_TESTS=1 with GEMINI_API_KEY and the sample image.",
)

# Read by hand from cse_sem3_a.png: day, start, end, cell keyword,
# teacher surname fragments, room (None for activities).
SEM3_A = [
    ("Monday", "09:00", "09:55", "DD&CO", ["CHOUGALA"], "LHC 301"),
    ("Monday", "09:55", "10:50", "DS", ["KAMATH"], "LHC 301"),
    ("Monday", "11:05", "12:55", "DD&CO LAB", ["CHOUGALA"], "CSE LAB1"),
    ("Monday", "13:45", "14:40", "MATHS", ["UMA"], "LHC 301"),
    ("Monday", "14:40", "16:30", "INDUSTRY", [], None),
    ("Tuesday", "09:00", "09:55", "OOP", ["PRADEEP"], "LHC 301"),
    ("Tuesday", "09:55", "10:50", "DS", ["KAMATH"], "LHC 301"),
    ("Tuesday", "11:05", "12:55", "MATHS TUT", [], "LHC 301"),
    ("Tuesday", "13:45", "14:40", "DMS", ["MAMATHA"], "LHC 301"),
    ("Tuesday", "14:40", "15:35", "DD&CO", ["CHOUGALA"], "LHC 301"),
    ("Tuesday", "15:35", "16:30", "UHV", ["JAMWAL"], "LHC 301"),
    ("Wednesday", "09:00", "10:50", "DS LAB", ["KAMATH", "CHOUGALA"], "CSE LAB 1"),
    ("Wednesday", "11:05", "12:00", "DD&CO", ["CHOUGALA"], "LHC 301"),
    ("Wednesday", "12:00", "12:55", "DS", ["KAMATH"], "LHC 301"),
    ("Wednesday", "13:45", "14:40", "MATHS", ["UMA"], "LHC 301"),
    ("Wednesday", "14:40", "15:35", "OOP", ["PRADEEP"], "LHC 301"),
    ("Wednesday", "15:35", "16:30", "UNIX", ["SULTHANA"], "CSE LAB1"),
    ("Thursday", "09:00", "09:55", "UHV", ["JAMWAL"], "LHC 301"),
    ("Thursday", "09:55", "10:50", "DMS", ["MAMATHA"], "LHC 301"),
    ("Thursday", "11:05", "12:00", "OOP", ["PRADEEP"], "LHC 301"),
    ("Thursday", "12:00", "12:55", "MATHS", ["UMA"], "LHC 301"),
    ("Thursday", "13:45", "16:30", "INDUSTRY", [], None),
    ("Friday", "09:00", "10:50", "DMS TUT", ["MAMATHA"], "LHC 305"),
    ("Friday", "11:05", "12:55", "OOP LAB", ["PRADEEP", "SULTHANA"], "CSE LAB2"),
    ("Friday", "13:45", "16:30", "AM31", [], "AB 701"),
    ("Saturday", "09:00", "10:50", "SKILL", [], None),
    ("Saturday", "11:05", "12:55", "PARTIAL", [], None),
]


def compact(value):
    return re.sub(r"[^A-Z0-9&]", "", str(value or "").upper())


def test_semester_3a_matches_hand_checked_timetable():
    result = run_extraction_pipeline(IMAGE, gemini_vision_client)
    records = result["records"]
    problems = []

    for day, start, end, keyword, teachers, room in SEM3_A:
        matches = [
            record for record in records
            if (record["day"], record["start_time"], record["end_time"])
            == (day, start, end)
            and compact(keyword) in compact(
                f"{record['original_text']} {record['course_abbrev']}"
            )
        ]
        if not matches:
            problems.append(f"missing {day} {start}-{end} {keyword}")
            continue
        record = matches[0]
        names = " ".join(
            teacher["teacher_name"] or "" for teacher in record["teachers"]
        ).upper()
        if not all(fragment in names for fragment in teachers):
            problems.append(f"teachers {day} {start} {keyword}: {names}")
        if room and compact(room) not in compact(record["room"]):
            problems.append(f"room {day} {start} {keyword}: {record['room']}")

    assert not problems, problems
    assert len(records) == len(SEM3_A)
    assert result["header"]["class_name"] == "3A"
