import cv2
import numpy as np

from app.ingestion.legend_extractor import (
    LegendCourseOffering,
    extract_legend,
)
from app.ingestion.teacher_registry import TeacherRegistry


def word(text, x, y, width=None, height=12, confidence=0.95):
    return {
        "text": text,
        "confidence": confidence,
        "original_bbox": {
            "x": x,
            "y": y,
            "width": width or max(8, len(text) * 7),
            "height": height,
        },
    }


def draw_table(path, x_lines, y_lines):
    image = np.full((260, 820, 3), 255, dtype=np.uint8)
    for y in y_lines:
        cv2.line(image, (x_lines[0], y), (x_lines[-1], y), (0, 0, 0), 2)
    for x in x_lines:
        cv2.line(image, (x, y_lines[0]), (x, y_lines[-1]), (0, 0, 0), 2)
    assert cv2.imwrite(str(path), image)


def test_extracts_wrapped_course_names_and_section_faculty_assignments(
    tmp_path,
):
    image_path = tmp_path / "legend.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 70, 130, 190])
    words = [
        word("SEMESTER:", 60, 5),
        word("V", 140, 5),
        word("SECTION:", 200, 5),
        word("A", 275, 5),
        word("ROOM", 350, 5),
        word("LHC", 410, 5),
        word("206", 450, 5),
        word("COURSE CODE", 75, 38, width=125),
        word("COURSE NAME", 295, 38, width=145),
        word("FACULTY", 570, 38, width=95),
        word("CS101", 95, 78),
        word("ADVANCED", 260, 78),
        word("SYSTEMS", 335, 78),
        word("DESIGN", 260, 100),
        word("(ASD)", 320, 100),
        word("DR.", 565, 78),
        word("ALICE", 595, 78),
        word("SMITH", 640, 90),
        word("(AS)", 690, 90),
        word("SECTION", 565, 100),
        word("1:", 625, 100),
        word("BOB", 650, 100),
        word("JONES", 680, 100),
        word("(BJ)", 725, 100),
        word("LHC", 565, 115),
        word("206", 598, 115),
        word("CS102", 95, 140),
        word("DATA", 260, 140),
        word("SCIENCE", 300, 140),
        word("(DS)", 365, 140),
        word("NS,", 565, 140),
        word("BK", 595, 140),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 160},
    )

    assert result["status"] == "partial"
    assert result["review_status"] == "needs_review"
    assert len(result["offerings"]) == 3
    assert all(
        isinstance(offering, LegendCourseOffering)
        for offering in result["offerings"]
    )
    first, second, third = result["offerings"]
    assert first.course_code == "CS101"
    assert first.course_name == "ADVANCED SYSTEMS DESIGN (ASD)"
    assert first.abbrev == "ASD"
    assert first.section_label == "A"
    assert first.faculty_name == "DR. ALICE SMITH"
    assert first.faculty_initials == ("AS",)
    assert first.room == "LHC 206"
    assert second.section_label == "1"
    assert second.faculty_name == "BOB JONES"
    assert second.faculty_initials == ("BJ",)
    assert second.room == "LHC 206"
    assert third.course_code == "CS102"
    assert third.course_name == "DATA SCIENCE (DS)"
    assert third.faculty_name is None
    assert third.faculty_initials == ("NS", "BK")
    assert any(
        "initials_without_faculty_name" in item["reason"]
        for item in result["unresolved"]
    )
    assert first.source_bbox["x"] <= 95
    assert first.source_bbox["y"] <= 78


def test_extracts_reordered_columns_using_header_positions(tmp_path):
    image_path = tmp_path / "reordered_legend.png"
    draw_table(image_path, [50, 260, 560, 750], [30, 80, 140])
    words = [
        word("FACULTY", 70, 42, width=80),
        word("COURSE NAME", 295, 42, width=145),
        word("COURSE CODE", 600, 42, width=130),
        word("PARKER", 70, 95),
        word("(PK)", 125, 95),
        word("NETWORK", 285, 95),
        word("SECURITY", 365, 95),
        word("LAB", 445, 95),
        word("(NSL)", 480, 95),
        word("IT204", 630, 95),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
    )

    assert result["status"] == "resolved"
    assert len(result["offerings"]) == 1
    offering = result["offerings"][0]
    assert offering.course_code == "IT204"
    assert offering.course_name == "NETWORK SECURITY LAB (NSL)"
    assert offering.kind == "laboratory"
    assert offering.faculty_name == "PARKER"
    assert offering.faculty_initials == ("PK",)


def test_noisy_codes_remain_reviewable_without_dropping_course_data(tmp_path):
    image_path = tmp_path / "noisy_legend.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("S51", 95, 95, confidence=0.35),
        word("SOFTWARE", 260, 95),
        word("ENGINEERING", 335, 95),
        word("SMITH", 565, 95),
        word("(SS)", 620, 95, confidence=0.4),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
    )

    assert result["status"] == "partial"
    assert len(result["offerings"]) == 1
    assert result["offerings"][0].course_code == "S51"
    assert result["offerings"][0].faculty_initials == ("SS",)
    assert result["offerings"][0].review_required is True
    assert any(
        item["reason"] == "low_confidence_course_code"
        for item in result["unresolved"]
    )


def test_low_confidence_faculty_initials_are_preserved_for_review(tmp_path):
    image_path = tmp_path / "noisy_initials.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("CS201", 95, 95),
        word("DATA", 260, 95),
        word("SCIENCE", 320, 95),
        word("ALICE", 565, 95),
        word("(AL)", 620, 95, confidence=0.35),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
    )

    assert result["status"] == "partial"
    assert len(result["offerings"]) == 1
    assert result["offerings"][0].faculty_name == "ALICE"
    assert result["offerings"][0].faculty_initials == ("AL",)
    assert result["offerings"][0].review_required is True
    assert result["offerings"][0].faculty_initials_unverified is True
    assert TeacherRegistry(result["offerings"]).resolve("AL") == "AL"
    assert any(
        "low_confidence_faculty_initials" in item["reason"]
        for item in result["unresolved"]
    )


def test_low_confidence_faculty_name_does_not_discard_clear_initials(
    tmp_path,
):
    image_path = tmp_path / "name_confidence.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("CS201", 95, 95),
        word("DATA", 260, 95),
        word("SCIENCE", 320, 95),
        word("ALICE", 565, 95, confidence=0.35),
        word("(AL)", 620, 95, confidence=0.95),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
    )

    offering = result["offerings"][0]
    assert offering.faculty_name == "ALICE"
    assert offering.faculty_initials == ("AL",)
    assert offering.review_required is True
    assert any(
        "low_confidence_faculty_name" in item["reason"]
        for item in result["unresolved"]
    )


def test_noisy_course_name_is_preserved_but_flagged_for_review(tmp_path):
    image_path = tmp_path / "noisy_name.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("CS301", 95, 95),
        word("ADVANCED", 260, 95, confidence=0.4),
        word("MATERIALS", 335, 95),
        word("ALICE", 565, 95),
        word("(AL)", 620, 95),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
    )

    assert result["status"] == "partial"
    assert result["offerings"][0].course_name == "ADVANCED MATERIALS"
    assert any(
        item["reason"] == "low_confidence_course_name"
        for item in result["unresolved"]
    )


def test_ambiguous_code_cell_does_not_merge_neighboring_course_rows(tmp_path):
    image_path = tmp_path / "ambiguous_code_row.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 110, 140, 170])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("CS101", 95, 88),
        word("DATA", 260, 88),
        word("STRUCTURES", 320, 88),
        word("ALICE", 565, 88),
        word("(AL)", 620, 88),
        word("ALSS", 95, 118),
        word("NETWORK", 260, 118),
        word("SECURITY", 335, 118),
        word("BOB", 565, 118),
        word("(BJ)", 605, 118),
        word("CS103", 95, 148),
        word("OPERATING", 260, 148),
        word("SYSTEMS", 350, 148),
        word("CAROL", 565, 148),
        word("(CA)", 625, 148),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 140},
    )

    assert [item.course_code for item in result["offerings"]] == [
        "CS101", None, "CS103",
    ]
    assert [item.course_name for item in result["offerings"]] == [
        "DATA STRUCTURES", "NETWORK SECURITY", "OPERATING SYSTEMS",
    ]
    assert result["offerings"][1].review_required is True
    assert any(
        item["reason"] == "ambiguous_course_code"
        and item["raw_text"] == "ALSS"
        for item in result["unresolved"]
    )


def test_llm_fallback_rejects_values_not_grounded_in_legend_ocr(tmp_path):
    image_path = tmp_path / "llm_legend.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("CS101", 95, 95, confidence=0.3),
        word("DATA", 260, 95),
        word("STRUCTURES", 320, 95),
        word("ALICE", 565, 95),
        word("(AL)", 620, 95),
    ]
    calls = []

    def llm_client(system_prompt, user_prompt):
        calls.append((system_prompt, user_prompt))
        return {
            "offerings": [{
                "course_code": "CS101",
                "course_name": "DATA STRUCTURES",
                "abbrev": None,
                "kind": None,
                "section_label": None,
                "faculty_name": "INVENTED PROFESSOR",
                "faculty_initials": ["AL"],
                "room": "LHC 999",
            }],
        }

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
        llm_client=llm_client,
    )

    assert len(calls) == 1
    assert len(result["offerings"]) == 1
    assert result["offerings"][0].faculty_name == "ALICE"
    assert result["offerings"][0].faculty_initials == ("AL",)
    assert any(
        item["reason"] == "llm_values_not_grounded_in_legend_ocr"
        for item in result["unresolved"]
    )


def test_llm_fallback_accepts_only_grounded_values(tmp_path):
    image_path = tmp_path / "grounded_llm_legend.png"
    draw_table(image_path, [50, 230, 550, 750], [30, 80, 140])
    words = [
        word("COURSE CODE", 75, 42, width=125),
        word("COURSE NAME", 295, 42, width=145),
        word("FACULTY", 570, 42, width=95),
        word("DATA", 260, 95),
        word("STRUCTURES", 320, 95),
        word("(DS)", 410, 95),
        word("ALICE", 565, 95),
        word("(AL)", 620, 95),
    ]

    result = extract_legend(
        image_path,
        {"words": words},
        {"x": 50, "y": 30, "width": 700, "height": 110},
        llm_client=lambda system, user: {
            "offerings": [{
                "course_name": "DATA STRUCTURES",
                "abbrev": "DS",
                "kind": None,
                "section_label": None,
                "faculty_name": "ALICE",
                "faculty_initials": ["AL"],
                "room": None,
            }],
        },
    )

    assert result["status"] == "partial"
    assert len(result["offerings"]) == 1
    offering = result["offerings"][0]
    assert offering.course_name == "DATA STRUCTURES"
    assert offering.abbrev == "DS"
    assert offering.faculty_name == "ALICE"
    assert offering.faculty_initials == ("AL",)
    assert offering.source_bbox is not None
