
import cv2
import numpy as np

import app.ingestion.cell_extractor as cell_extractor
from app.ingestion.cell_extractor import (
    MERGED_CELLS,
    get_merged_periods,
)


def test_monday_ddco_lab_spans_two_periods():
    merged = next(
        cell for cell in MERGED_CELLS
        if cell["day"] == "Monday"
        and cell["text_contains"] == "DD&CO Lab"
    )

    periods = get_merged_periods(merged)

    assert len(periods) == 2
    assert periods[0][2] == "11:05"
    assert periods[-1][3] == "12:55"


def test_thursday_industry_training_spans_three_periods():
    merged = next(
        cell for cell in MERGED_CELLS
        if cell["day"] == "Thursday"
        and cell["text_contains"] == "INDUSTRY"
    )

    periods = get_merged_periods(merged)

    assert len(periods) == 3
    assert periods[0][2] == "13:45"
    assert periods[-1][3] == "16:30"


def test_all_merged_cells_have_valid_periods():
    for merged in MERGED_CELLS:
        periods = get_merged_periods(merged)

        assert periods, f"No periods found for {merged}"
        assert periods[0][2] == merged["start"]
        assert periods[-1][3] == merged["end"]


def _word(text, x, y, width=None, height=10):
    return {
        "text": text,
        "original_bbox": {
            "x": x,
            "y": y,
            "width": width or max(8, len(text) * 6),
            "height": height,
        },
    }


def _mock_grid(monkeypatch, rows=None):
    geometry = {
        "x_lines": [0, 100, 200, 300, 400, 500],
        "y_lines": [50, 100, 150],
        "schedule_top": 50,
        "schedule_bottom": 150,
    }
    if rows is None:
        rows = [
            {
                "row_index": 81,
                "y_start": 50,
                "y_end": 100,
                "boundaries": [
                    {"x": x, "likely_present": True}
                    for x in geometry["x_lines"]
                ],
            },
            {
                "row_index": 4,
                "y_start": 100,
                "y_end": 150,
                "boundaries": [
                    {"x": x, "likely_present": True}
                    for x in geometry["x_lines"]
                ],
            },
        ]
    monkeypatch.setattr(
        cell_extractor,
        "detect_grid_geometry",
        lambda image_path: geometry,
    )
    monkeypatch.setattr(
        cell_extractor,
        "inspect_row_wise_boundaries",
        lambda image_path, geometry=None: rows,
    )
    monkeypatch.setattr(
        cell_extractor,
        "infer_merged_cells",
        lambda row_results: [],
    )
    return geometry


def test_extracts_slot_times_days_breaks_and_positioned_lines(
    tmp_path, monkeypatch,
):
    image_path = tmp_path / "synthetic_grid.png"
    assert cv2.imwrite(
        str(image_path),
        np.full((160, 510, 3), 255, dtype=np.uint8),
    )
    _mock_grid(monkeypatch)
    evidence = {
        "source_document": "sample.png",
        "source_page": 1,
        "words": [
            _word("09:00-09:55", 110, 10, width=80),
            _word("09:55-10:50", 210, 10, width=80),
            _word("11:05-12:00", 410, 10, width=80),
            _word("Monday", 10, 70),
            _word("DBMS", 120, 70),
            _word("BREAK", 320, 70),
            _word("OOP", 420, 68),
            _word("(SB)", 420, 82),
            _word("Tuesday", 10, 120),
            _word("Networks", 420, 120),
        ],
    }

    result = cell_extractor.extract_cells_from_evidence(
        image_path,
        evidence,
    )

    monday_dbms = next(
        cell for cell in result["cells"] if cell["text"] == "DBMS"
    )
    break_slot = next(
        cell for cell in result["cells"] if cell["text"] == "BREAK"
    )
    lab = next(cell for cell in result["cells"] if "(SB)" in cell["text"])
    tuesday = next(
        cell for cell in result["cells"] if cell["text"] == "Networks"
    )

    assert monday_dbms["day"] == "Monday"
    assert (monday_dbms["start_time"], monday_dbms["end_time"]) == (
        "09:00", "09:55",
    )
    assert break_slot["is_break"] is True
    assert (break_slot["start_time"], break_slot["end_time"]) == (
        "10:50", "11:05",
    )
    assert lab["lines"][0]["text"] == "OOP"
    assert lab["lines"][0]["bbox"]["x"] == 420
    assert tuesday["day"] == "Tuesday"
    assert tuesday["row_index"] == 4
    assert result["break_columns"] == [3]
    assert all(cell["day"] != "UNKNOWN" for cell in result["cells"])


def test_split_header_times_are_assigned_by_column_geometry(
    tmp_path, monkeypatch,
):
    image_path = tmp_path / "split_times.png"
    assert cv2.imwrite(
        str(image_path),
        np.full((160, 510, 3), 255, dtype=np.uint8),
    )
    _mock_grid(monkeypatch)
    evidence = {
        "words": [
            _word("09.00", 120, 8),
            _word("09.55", 120, 24),
            _word("10.50", 220, 8),
            _word("11.05", 220, 24),
            _word("12.00", 420, 8),
            _word("12.55", 420, 24),
            _word("Monday", 10, 70),
            _word("DS", 120, 70),
        ],
    }

    result = cell_extractor.extract_cells_from_evidence(
        image_path,
        evidence,
    )

    cell = next(cell for cell in result["cells"] if cell["text"] == "DS")
    assert (cell["start_time"], cell["end_time"]) == ("09:00", "09:55")
    assert result["break_columns"] == [3]


def test_compacted_ocr_time_range_is_recovered_from_header():
    periods = cell_extractor._parse_header_times(
        [
            _word("3.35430", 110, 10, width=48),
        ],
        [0, 100, 200],
        schedule_top=50,
    )

    assert periods == [{
        "column_index": 1,
        "start_time": "15:35",
        "end_time": "16:30",
        "x_start": 100,
        "x_end": 200,
    }]


def test_merged_cell_uses_all_overlapped_periods_and_bbox(
    tmp_path, monkeypatch,
):
    image_path = tmp_path / "merged_grid.png"
    assert cv2.imwrite(
        str(image_path),
        np.full((160, 510, 3), 255, dtype=np.uint8),
    )
    rows = [
        {
            "row_index": 17,
            "y_start": 50,
            "y_end": 100,
            "boundaries": [
                {"x": x, "likely_present": True}
                for x in (0, 100, 300, 400, 500)
            ],
        },
    ]
    _mock_grid(monkeypatch, rows=rows)
    evidence = {
        "words": [
            _word("09:00-09:55", 110, 10, width=80),
            _word("09:55-10:50", 210, 10, width=80),
            _word("11:05-12:00", 410, 10, width=80),
            _word("Monday", 10, 70),
            _word("LONG", 120, 70),
            _word("LAB", 220, 70),
        ],
    }

    result = cell_extractor.extract_cells_from_evidence(
        image_path,
        evidence,
    )

    merged = next(
        cell for cell in result["cells"] if cell["text"] == "LONG LAB"
    )
    assert merged["period_indices"] == [1, 2]
    assert (merged["start_time"], merged["end_time"]) == (
        "09:00", "10:50",
    )
    assert merged["bbox"] == {
        "x": 100,
        "y": 50,
        "width": 200,
        "height": 50,
    }
