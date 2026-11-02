import os
from pathlib import Path
import shutil

import pytest

from app.ingestion.document_processor import process_timetable_image
from app.ingestion.grid_extractor import detect_grid_coordinates
from app.ingestion.legend_extractor import extract_legend
from app.ingestion.layout_regions import locate_regions


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


def geometry():
    return {
        "width": 1000,
        "height": 800,
        "x_lines": [50, 950],
        "y_lines": [200, 250, 300],
        "schedule_top": 200,
        "schedule_bottom": 300,
    }


def test_locates_header_grid_and_legend_from_positioned_anchors():
    words = [
        word("SEMESTER:", 50, 40),
        word("V", 130, 40),
        word("SECTION:", 240, 40),
        word("B", 315, 40),
        word("ROOM", 420, 40),
        word("NO:", 470, 40),
        word("LHC", 510, 40),
        word("206", 545, 40),
        word("TERM:", 650, 40),
        word("2026-27", 710, 40),
        word("COURSE CODE", 80, 320, width=120),
        word("COURSE NAME", 350, 320, width=130),
        word("FACULTY", 750, 320, width=90),
        word("CS101", 100, 350),
        word("ADVANCED", 300, 350),
        word("SYSTEMS", 375, 350),
        word("CS102", 100, 380),
        word("NETWORKS", 300, 380),
        word("SECTION", 750, 380),
        word("1:", 810, 380),
    ]

    result = locate_regions(
        {"words": words},
        geometry(),
    )

    assert result["header"]["bbox"] == {
        "x": 0, "y": 0, "width": 1000, "height": 200,
    }
    assert result["header"]["metadata"]["semester"] == "V"
    assert result["header"]["metadata"]["section"] == "B"
    assert result["header"]["metadata"]["room"] == "LHC 206"
    assert result["header"]["metadata"]["term"] == "2026-27"
    assert result["grid"]["bbox"] == {
        "x": 50, "y": 200, "width": 900, "height": 100,
    }
    assert result["legend"]["status"] == "located"
    assert result["legend"]["course_rows_detected"] == 2
    assert result["legend"]["bbox"]["y"] >= 300


def test_locates_legend_with_reordered_columns_and_missing_metadata():
    words = [
        word("FACULTY", 90, 410),
        word("COURSE NAME", 370, 410, width=130),
        word("COURSE CODE", 760, 410, width=130),
        word("INSTRUCTOR", 90, 450),
        word("RELATIONAL", 365, 450),
        word("DATABASES", 455, 450),
        word("IT204", 780, 450),
        word("IT205", 780, 485),
        word("OPERATING", 365, 485),
        word("SYSTEMS", 450, 485),
    ]
    result = locate_regions({"words": words}, geometry())

    assert result["legend"]["status"] == "located"
    assert result["legend"]["course_rows_detected"] == 2
    assert result["legend"]["anchors"]["faculty"]["bbox"]["x"] == 90
    assert result["header"]["metadata"] == {
        "semester": None,
        "section": None,
        "room": None,
        "term": None,
    }


def test_missing_heading_anchors_or_grid_geometry_is_explicitly_unresolved():
    no_anchors = locate_regions(
        {"words": [word("COURSE", 50, 320), word("MATH", 80, 360)]},
        geometry(),
    )
    assert no_anchors["legend"]["status"] == "unresolved"
    assert no_anchors["legend"]["reason"] == (
        "legend_heading_anchors_not_found_below_grid"
    )

    no_geometry = locate_regions({"words": []}, {})
    assert no_geometry["header"]["status"] == "unresolved"
    assert no_geometry["grid"]["status"] == "unresolved"
    assert no_geometry["legend"]["status"] == "unresolved"


def test_semester_five_image_regression_locates_legend(tmp_path):
    image_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "uploads"
        / "cse_sem5_a.png"
    )
    if not image_path.is_file():
        pytest.skip("Semester 5 regression image is not available.")

    tesseract = os.getenv("TESSERACT_CMD")
    default_tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not tesseract and default_tesseract.is_file():
        tesseract = str(default_tesseract)
    if not tesseract and not shutil.which("tesseract"):
        pytest.skip("Tesseract is required for the image regression test.")

    evidence = process_timetable_image(
        image_path,
        tesseract_cmd=tesseract,
    )
    detected = detect_grid_coordinates(
        image_path,
        output_path=tmp_path / "grid-overlay.png",
    )
    result = locate_regions(evidence, detected)

    assert result["grid"]["bbox"]["y"] == detected["schedule_top"]
    assert result["grid"]["bbox"]["height"] == (
        detected["schedule_bottom"] - detected["schedule_top"]
    )
    assert result["header"]["metadata"]["semester"] == "Vv"
    assert result["header"]["metadata"]["section"] == "A"
    assert result["legend"]["status"] == "located"
    assert {"course_code", "course_name", "faculty"} <= set(
        result["legend"]["anchors"]
    )
    extracted = extract_legend(
        image_path,
        evidence,
        result["legend"],
    )
    assert extracted["review_status"] == "needs_review"
    assert extracted["status"] in {"resolved", "partial", "unresolved"}
    for offering in extracted["offerings"]:
        assert offering.source_bbox is not None
