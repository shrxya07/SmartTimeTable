import json

from app.ingestion.timetable_resolver import (
    CourseOffering,
    SlotCell,
    resolve_slots,
    snap_course,
)
from app.ingestion.teacher_registry import TeacherRegistry


def offering(
    course_name,
    abbrev,
    section,
    teacher,
    initials,
    room,
    kind=None,
):
    return CourseOffering(
        course_name=course_name,
        abbrev=abbrev,
        kind=kind,
        section_label=section,
        faculty_name=teacher,
        faculty_initials=[initials] if initials else [],
        room=room,
        source_bbox={"x": 1, "y": section * 10, "width": 20, "height": 8},
    )


def slot(text, *, lines=(), is_break=False):
    return SlotCell(
        day="Monday",
        start_time="11:05",
        end_time="12:00",
        text=text,
        lines=tuple(lines),
        is_break=is_break,
        bbox={"x": 10, "y": 20, "width": 30, "height": 15},
        source_document="timetable.png",
        source_page=1,
    )


def header():
    return {
        "semester": 5,
        "section": "A",
        "default_room": "LHC 301",
    }


def elective_offerings():
    return [
        offering("Object Oriented Analysis and Design", "OOAD", 1,
                 "Dr. Sushma B", "SB", "LHC 206"),
        offering("Object Oriented Analysis and Design", "OOAD", 2,
                 "Dr. Chandrika P", "CP", "LHC 207"),
        offering("Object Oriented Analysis and Design", "OOAD", 3,
                 "Dr. Parkavi A", "APK", "LHC 208"),
        offering("Big Data Analytics", "BDA", 4,
                 "Uzma Sultana", "US", "LHC 301"),
    ]


def test_combined_ooad_bda_expands_to_legend_sections():
    result = resolve_slots(
        [slot("OOAD/BDA")],
        elective_offerings(),
        header(),
    )

    entries = result["entries"]
    assert len(entries) == 4
    assert [entry["subgroup"] for entry in entries] == [1, 2, 3, 4]
    assert [entry["course_abbrev"] for entry in entries] == [
        "OOAD", "OOAD", "OOAD", "BDA",
    ]
    assert len({entry["parallel_group"] for entry in entries}) == 1
    assert {entry["entry_type"] for entry in entries} == {"elective"}
    assert all(entry["review_status"] == "needs_review" for entry in entries)


def test_prompt_engineering_offerings_expand_by_section():
    pe_offerings = [
        offering("Prompt Engineering", "PE", 1, "Brunda G", "BG", "CSE LAB 3"),
        offering("Prompt Engineering", "PE", 2, "Pallavi N", "PN", "CSE LAB 1"),
        offering("Prompt Engineering", "PE", 3, "Pavan Kumar K", "NPK", "CSE LAB 2"),
        offering("Prompt Engineering", "PE", 4, "Akshatha Kamath", "AK", "LHC 301"),
    ]

    result = resolve_slots([slot("AEC PE")], pe_offerings, header())

    assert len(result["entries"]) == 4
    assert [entry["subgroup"] for entry in result["entries"]] == [1, 2, 3, 4]
    assert {entry["course"] for entry in result["entries"]} == {
        "Prompt Engineering",
    }
    assert {entry["entry_type"] for entry in result["entries"]} == {"class"}
    assert {entry["kind"] for entry in result["entries"]} == {"AEC"}


def test_aec_prefix_and_name_alias_resolve_section_four_offering():
    offerings = [
        offering(
            "AEC- NoSQL (1:0:0)",
            None,
            4,
            "Akshatha Kamath",
            None,
            "LHC 301",
        ),
        offering(
            "AEC- Prompt Engineering (1:0:0) (PE)",
            "PE",
            1,
            "Brunda G",
            "BG",
            "CSE LAB 3",
        ),
    ]
    evidence = "AEC\nNOSQL/PE"
    result = resolve_slots(
        [slot(evidence, lines=evidence.splitlines())],
        offerings,
        header(),
    )

    assert [
        (entry["subgroup"], entry["course_abbrev"], entry["teacher_name"])
        for entry in result["entries"]
    ] == [
        (4, None, "Akshatha Kamath"),
        (1, "PE", "Brunda G"),
    ]


def test_ocr_confused_course_abbreviation_matches_conservatively():
    courses = [
        offering(
            "Object Oriented Analysis and Design (O0AD)",
            "O0AD",
            1,
            "Dr Sushma B",
            "SB",
            "LHC 206",
        ),
    ]

    match = snap_course("OOAD", courses)

    assert match["course_name"] == (
        "Object Oriented Analysis and Design (O0AD)"
    )
    assert match["tier"] == "fuzzy"


def test_lab_batches_match_offering_sections_and_initials():
    lab_offerings = [
        offering("Object Oriented Programming", "OOP", number,
                 f"Teacher {initials}", initials, f"CSE LAB {number}", "LAB")
        for number, initials in enumerate(("SB", "CP", "APK", "US"), start=1)
    ]
    lines = [
        f"CSE LAB {number} ({initials}) LAB S{number} OOP"
        for number, initials in enumerate(("SB", "CP", "APK", "US"), start=1)
    ]

    result = resolve_slots(
        [slot("\n".join(lines), lines=lines)],
        lab_offerings,
        header(),
    )

    assert len(result["entries"]) == 4
    assert [entry["subgroup"] for entry in result["entries"]] == [
        "S1", "S2", "S3", "S4",
    ]
    assert {entry["entry_type"] for entry in result["entries"]} == {"lab"}
    assert all(entry["review_tier"] == "auto" for entry in result["entries"])
    assert result["unresolved_items"] == []


def test_room_continuation_preserves_lab_subgroup_and_all_teachers():
    offerings = [
        CourseOffering(
            course_name="Data Communication Networking Laboratory",
            abbrev="DCN",
            kind="laboratory",
            section_label=1,
            faculty_name="Nandini S B",
            faculty_initials=("NSB",),
            room="LHC 206",
            source_bbox={"x": 1, "y": 2, "width": 20, "height": 8},
        ),
        CourseOffering(
            course_name="Formal Languages and Automata Theory",
            abbrev="FAFL",
            faculty_name="Pallavi N",
            faculty_initials=("PN",),
            room="LHC 205",
            source_bbox={"x": 1, "y": 12, "width": 20, "height": 8},
        ),
    ]
    evidence = "S1 DCN LAB(NSB,PN,PK)\n(CSE LAB 2)"
    result = resolve_slots(
        [slot(evidence, lines=evidence.splitlines())],
        offerings,
        header(),
    )

    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["subgroup"] == "S1"
    assert entry["room"] == "CSE LAB 2"
    assert [
        (teacher["teacher_name"], teacher["initials"])
        for teacher in entry["teachers"]
    ] == [
        ("Nandini S B", "NSB"),
        ("Pallavi N", "PN"),
        ("PK", "PK"),
    ]


def test_lab_abbreviation_suffix_matches_course_token_and_room_continuation():
    evidence = "FSD LAB (JGR,BG,CP)\n(CSE LAB 3)"
    result = resolve_slots(
        [slot(evidence, lines=evidence.splitlines())],
        [
            CourseOffering(
                course_name="Full Stack Development Laboratory",
                abbrev="FSD LAB",
                kind="laboratory",
                faculty_initials=("JGR", "BG", "CP"),
                room="LHC 206",
                source_bbox={"x": 1, "y": 2, "width": 20, "height": 8},
            ),
        ],
        header(),
    )

    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["course_abbrev"] == "FSD LAB"
    assert entry["room"] == "CSE LAB 3"
    assert [teacher["initials"] for teacher in entry["teachers"]] == [
        "JGR", "BG", "CP",
    ]


def test_unverified_legend_initials_do_not_block_cell_teacher_resolution():
    room = {"x": 1, "y": 2, "width": 20, "height": 8}
    offerings = [
        CourseOffering(
            course_name="Data Communication Networking Laboratory",
            abbrev="DCN LAB",
            kind="laboratory",
            faculty_name="Nandini SB",
            faculty_initials=("BY",),
            room="LHC 206",
            source_bbox=room,
            review_required=True,
            faculty_initials_unverified=True,
        ),
        CourseOffering(
            course_name="Data Communication Networking",
            abbrev="DCN",
            faculty_name="Nandini SB",
            faculty_initials=("NSB",),
            room="LHC 206",
            source_bbox=room,
        ),
        CourseOffering(
            course_name="Formal Languages",
            abbrev="FAFL",
            faculty_name="Pallavi N",
            faculty_initials=("PN",),
            room="LHC 205",
            source_bbox=room,
        ),
    ]
    evidence = "S1 DCN LAB(NSB,PN,PK)\n(CSE LAB 2)"
    result = resolve_slots(
        [slot(evidence, lines=evidence.splitlines())],
        offerings,
        header(),
    )

    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["subgroup"] == "S1"
    assert entry["room"] == "CSE LAB 2"
    assert [teacher["teacher_name"] for teacher in entry["teachers"]] == [
        "Nandini SB", "Pallavi N", "PK",
    ]
    assert entry["review_tier"] == "check"
    assert result["unresolved_items"] == []


def test_lab_initial_mismatch_is_flagged_for_review():
    course = [
        offering("Object Oriented Programming", "OOP", 1,
                 "Dr Sushma", "SB", "CSE LAB 1", "LAB"),
    ]
    result = resolve_slots(
        [slot("S1 OOP LAB (CP) CSE LAB 1")],
        course,
        header(),
    )

    assert result["entries"][0]["review_tier"] == "check"
    assert result["unresolved_items"]


def test_activity_is_one_record_with_null_teacher_room_and_full_evidence():
    evidence = "INDUSTRY TALK\nGuest speaker session"
    result = resolve_slots(
        [slot(evidence, lines=evidence.splitlines())],
        [],
        header(),
    )

    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["entry_type"] == "activity"
    assert entry["teacher_name"] is None
    assert entry["teachers"] == []
    assert entry["room"] is None
    assert entry["evidence"] == evidence


def test_break_slot_is_not_resolved_as_a_course():
    result = resolve_slots(
        [slot("", is_break=True)],
        elective_offerings(),
        header(),
    )

    assert result["entries"][0]["entry_type"] == "break"
    assert result["entries"][0]["course"] == "Break"


def test_se_m_and_ocr_variant_snap_to_legend_vocabulary():
    courses = [
        offering("Software Engineering and Modelling", "SERM", 1,
                 "Dr Rao", "DR", "LHC 204"),
        offering("Formal Languages and Automata Theory", "FAFL", 1,
                 "Dr Sen", "DS", "LHC 205"),
    ]

    assert snap_course("SE&M", courses)["abbrev"] == "SERM"
    assert snap_course("FAEL", courses)["abbrev"] == "FAFL"
    assert snap_course("QOAD", elective_offerings())["abbrev"] == "OOAD"


def test_course_name_acronym_is_used_as_last_match_tier():
    courses = [
        offering(
            "Formal Languages and Automata Theory",
            None,
            1,
            "Dr Sen",
            "DS",
            "LHC 205",
        ),
    ]

    match = snap_course("FLAT", courses)

    assert match["course_name"] == "Formal Languages and Automata Theory"
    assert match["tier"] == "acronym"


def test_unresolved_initials_remain_initials_and_need_check():
    course = [
        offering("Data Structures", "DS", 1, None, None, "LHC 210", "LAB"),
    ]
    result = resolve_slots(
        [slot("S1 DS LAB (VGS, RJ, DPK) LHC 210")],
        course,
        header(),
    )

    assert result["entries"][0]["teacher_name"] == "VGS"
    assert [teacher["teacher_name"] for teacher in
            result["entries"][0]["teachers"]] == ["VGS", "RJ", "DPK"]
    assert result["entries"][0]["review_tier"] == "check"


def test_registry_persists_mappings_and_preserves_ambiguous_aliases(tmp_path):
    first_timetable = TeacherRegistry(offerings=[
        offering("Database Systems", "DBMS", 1, "Dr Rao", "DR", "LHC 1"),
    ])
    path = tmp_path / "teacher_registry.json"
    first_timetable.save(path)

    second_timetable = TeacherRegistry.load(path, offerings=[
        offering("Networks", "NET", 1, "Dr Sen", "DS", "LHC 2"),
    ])
    second_timetable.add_offerings([
        offering("AI", "AI", 1, "Another Teacher", "DR", "LHC 3"),
    ])

    assert second_timetable.resolve("DS") == "Dr Sen"
    assert second_timetable.resolve("DR") == "DR"
    assert not second_timetable.is_resolved("DR")
    assert json.loads(path.read_text(encoding="utf-8"))["DR"] == ["Dr Rao"]


def test_registry_collapses_spacing_only_name_variants():
    registry = TeacherRegistry(offerings=[
        offering("Networks", "NET", 1, "PALLAVIN", "PN", "LHC 1"),
        offering("Data Systems", "DS", 1, "PALLAVI N", "PN", "LHC 2"),
    ])

    assert registry.resolve("PN") == "PALLAVI N"
    assert registry.to_dict()["PN"] == ["PALLAVI N"]


def test_completeness_reports_unmatched_legend_courses():
    offerings = [
        offering("Database Systems", "DBMS", 1, "Dr Rao", "DR", "LHC 1"),
        offering("Networks", "NET", 1, "Dr Sen", "DS", "LHC 2"),
    ]
    result = resolve_slots([slot("DBMS")], offerings, header())

    assert result["completeness"]["unmatched_legend_courses"] == [
        {
            "course_name": "Networks",
            "abbrev": "NET",
            "reason": "Legend course was not matched to a non-empty grid cell.",
        }
    ]


def test_unresolved_nonempty_slot_is_reported_explicitly():
    result = resolve_slots(
        [slot("UNREADABLE TEXT")],
        elective_offerings(),
        header(),
    )

    assert result["entries"] == []
    assert len(result["unresolved_items"]) == 1
    assert result["unresolved_items"][0]["evidence"] == "UNREADABLE TEXT"
