
from pathlib import Path
import re
import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from app.ingestion.dynamic_grid import (
    detect_grid_geometry,
    inspect_row_wise_boundaries,
    infer_merged_cells,
)
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
from app.ingestion.grid_extractor import crop_timetable_area



IMAGE_PATH = Path("data/uploads/cse_sem3_a.png")

# Time boundaries for the seven teaching periods, in crop coordinates.
PERIODS = [
    (261, 446, "09:00", "09:55"),
    (446, 617, "09:55", "10:50"),
    (617, 820, "11:05", "12:00"),
    (820, 994, "12:00", "12:55"),
    (1054, 1244, "13:45", "14:40"),
    (1244, 1388, "14:40", "15:35"),
    (1388, 1568, "15:35", "16:30"),
]

ROWS = [
    ("Monday", 122, 184),
    ("Tuesday", 184, 240),
    ("Wednesday", 240, 308),
    ("Thursday", 308, 355),
    ("Friday", 355, 432),
    ("Saturday", 432, 474),
]

# Known merged cells verified against the Semester III-A timetable image.
MERGED_CELLS = [
    {
        "day": "Monday",
        "start": "11:05",
        "end": "12:55",
        "text_contains": "DD&CO Lab",
    },
    {
        "day": "Monday",
        "start": "14:40",
        "end": "16:30",
        "text_contains": "INDUSTRY",
    },
    {
        "day": "Tuesday",
        "start": "11:05",
        "end": "12:55",
        "text_contains": "MATHS",
    },
    {
        "day": "Wednesday",
        "start": "09:00",
        "end": "10:50",
        "text_contains": "DS LAB",
    },
    {
        "day": "Thursday",
        "start": "13:45",
        "end": "16:30",
        "text_contains": "INDUSTRY",
    },
    {
        "day": "Friday",
        "start": "09:00",
        "end": "10:50",
        "text_contains": "DMS",
    },
    {
        "day": "Friday",
        "start": "11:05",
        "end": "12:55",
        "text_contains": "OOP",
    },
    {
        "day": "Friday",
        "start": "13:45",
        "end": "16:30",
        "text_contains": "AM31",
    },
    {
        "day": "Saturday",
        "start": "09:00",
        "end": "10:50",
        "text_contains": "SKILL",
    },
    {
        "day": "Saturday",
        "start": "11:05",
        "end": "12:55",
        "text_contains": "PARTIAL",
    },
]


def get_merged_periods(merged_cell):
    """Return the teaching periods covered by a merged cell."""
    return [
        period
        for period in PERIODS
        if period[2] >= merged_cell["start"]
        and period[3] <= merged_cell["end"]
    ]


def extract_cells(image_path=IMAGE_PATH, merged_cells=None):
    """Extract timetable cells and combine known merged periods."""
    crop_path = crop_timetable_area(
    image_path,
    output_path=Path(image_path).with_name(
        f"{Path(image_path).stem}_crop.png"
    ),
)
    image = cv2.imread(str(crop_path))

    if image is None:
        raise ValueError(f"Could not read image: {crop_path}")
    # Existing PERIODS and ROWS coordinates are calibrated for
    # # the Semester III-A crop. Normalize dimensions before using them.
    reference_width = PERIODS[-1][1]
    reference_height = ROWS[-1][2]
    image = cv2.resize(
      image,
      (reference_width, reference_height),
      interpolation=cv2.INTER_CUBIC,
    )
    data = pytesseract.image_to_data(
        image,
        config="--psm 11",
        output_type=Output.DICT,
    )

    # Collect valid OCR words with their center coordinates.
    words = []
    for i, raw_text in enumerate(data["text"]):
        text = raw_text.strip()
        if not text:
            continue

        x = data["left"][i] + data["width"][i] / 2
        y = data["top"][i] + data["height"][i] / 2
        words.append((y, x, text))

    records = []

    for day, top, bottom in ROWS:
        day_words = [
            word for word in words
            if top <= word[0] < bottom
        ]

        # Period indexes already represented by a merged cell.
        merged_period_indexes = set()

        # First extract known merged cells as single records.
        # Apply default merged-cell definitions only to Semester 3-A.
        if merged_cells is not None:
            active_merged_cells = merged_cells
        elif Path(image_path).name.lower() == "cse_sem3_a.png":
            active_merged_cells = MERGED_CELLS
        else:
            active_merged_cells = []

        for merged in active_merged_cells:
            if merged["day"] != day:
                continue

            covered = [
                (index, period)
                for index, period in enumerate(PERIODS)
                if period[2] >= merged["start"]
                and period[3] <= merged["end"]
            ]

            if not covered:
                continue

            first_index, first_period = covered[0]
            last_index, last_period = covered[-1]

            left = first_period[0]
            right = last_period[1]

            cell_words = [
                word for word in day_words
                if left <= word[1] < right
            ]

            cell_words.sort(key=lambda word: (word[0], word[1]))
            text = " ".join(word[2] for word in cell_words)

            if text:
                records.append({
                    "day": day,
                    "start_time": merged["start"],
                    "end_time": merged["end"],
                    "text": text,
                    "review_status": "needs_review",
                })

            merged_period_indexes.update(
                index for index, _ in covered
            )

        # Extract ordinary periods not covered by a merged cell.
        for index, (left, right, start, end) in enumerate(PERIODS):
            if index in merged_period_indexes:
                continue

            cell_words = [
                word for word in day_words
                if left <= word[1] < right
            ]

            cell_words.sort(key=lambda word: (word[0], word[1]))

            if cell_words:
                records.append({
                    "day": day,
                    "start_time": start,
                    "end_time": end,
                    "text": " ".join(word[2] for word in cell_words),
                    "review_status": "needs_review",
                })

    return sorted(
        records,
        key=lambda record: (
            ROWS.index(next(
                row for row in ROWS if row[0] == record["day"]
            )),
            record["start_time"],
        ),
    )



if __name__ == "__main__":
    for record in extract_cells():
        print(
            f"{record['day']:10} "
            f"{record['start_time']}-{record['end_time']}: "
            f"{record['text']}"
        )

def _parse_header_times(ocr_words, x_lines, schedule_top):
    """Map OCR time labels to grid columns using their positioned boxes."""
    if len(x_lines) < 2:
        return []

    range_pattern = re.compile(
        r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*"
        r"(\d{1,2})[.:](\d{2})"
    )
    compact_range_pattern = re.compile(
        r"(\d{1,2})[.:](\d{2})[-–—]?(\d{1,2})[.:]?(\d{2})"
    )
    time_pattern = re.compile(r"(\d{1,2})[.:](\d{2})")

    def format_time(hour, minute):
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        if hour <= 7:
            hour += 12
        return f"{hour:02d}:{minute:02d}"

    def column_for_x(x):
        return min(
            range(len(x_lines) - 1),
            key=lambda index: abs(
                x - (x_lines[index] + x_lines[index + 1]) / 2
            ),
        )

    parsed_ranges = []
    standalone_by_column = {}
    for word in ocr_words:
        text = str(word.get("text", "")).strip()
        bbox = word.get("original_bbox") or word.get("bbox") or {}
        if not bbox:
            continue
        x = bbox["x"] + bbox["width"] / 2
        y = bbox["y"] + bbox["height"] / 2
        if y >= schedule_top:
            continue

        column_index = column_for_x(x)
        ranges = list(range_pattern.finditer(text))
        if ranges:
            for match in ranges:
                start = format_time(int(match.group(1)), int(match.group(2)))
                end = format_time(int(match.group(3)), int(match.group(4)))
                if start and end and start < end:
                    parsed_ranges.append({
                        "column_index": column_index,
                        "start_time": start,
                        "end_time": end,
                    })
            continue

        compact_range = compact_range_pattern.fullmatch(
            re.sub(r"\s+", "", text)
        )
        if compact_range:
            start = format_time(
                int(compact_range.group(1)),
                int(compact_range.group(2)),
            )
            end = format_time(
                int(compact_range.group(3)),
                int(compact_range.group(4)),
            )
            if start and end and start < end:
                parsed_ranges.append({
                    "column_index": column_index,
                    "start_time": start,
                    "end_time": end,
                })
                continue

        times = list(time_pattern.finditer(text))
        remainder = time_pattern.sub("", text)
        if times and re.fullmatch(r"[\s,;/|]*", remainder):
            for match in times:
                hour, minute = map(int, match.groups())
                value = format_time(hour, minute)
                if value:
                    standalone_by_column.setdefault(
                        column_index, []
                    ).append((y, x + match.start() / 1000, value))

    for column_index, values in standalone_by_column.items():
        ordered = sorted(values)
        if len(ordered) < 2:
            continue
        start, end = ordered[0][2], ordered[1][2]
        if start < end:
            parsed_ranges.append({
                "column_index": column_index,
                "start_time": start,
                "end_time": end,
            })

    unique = {}
    for period in sorted(
        parsed_ranges,
        key=lambda item: (
            item["column_index"],
            item["start_time"],
            item["end_time"],
        ),
    ):
        existing = unique.get(period["column_index"])
        if existing and existing != period:
            continue
        unique[period["column_index"]] = {
            **period,
            "x_start": x_lines[period["column_index"]],
            "x_end": x_lines[period["column_index"] + 1],
        }

    return [unique[index] for index in sorted(unique)]

def _detect_cell_day(ocr_words, y_start, y_end):
    """Identify the weekday label inside a detected timetable row."""

    aliases = {
        "MONDAY": "Monday",
        "MON": "Monday",
        "TUESDAY": "Tuesday",
        "TUE": "Tuesday",
        "TUES": "Tuesday",
        "WEDNESDAY": "Wednesday",
        "WED": "Wednesday",
        "THURSDAY": "Thursday",
        "THU": "Thursday",
        "THUR": "Thursday",
        "FRIDAY": "Friday",
        "FRI": "Friday",
        "SATURDAY": "Saturday",
        "SAT": "Saturday",
        "SATURDA": "Saturday",
        "SUNDAY": "Sunday",
        "SUN": "Sunday",
    }

    for word in ocr_words:
        text = word.get("text", "").strip().upper().strip(".,:")
        bbox = word.get("original_bbox") or word.get("bbox") or {}

        if not bbox:
            continue

        cy = bbox["y"] + bbox["height"] / 2

        if y_start <= cy < y_end and text in aliases:
            return aliases[text]

    return None


def extract_cells_from_evidence(
    image_path,
    ocr_evidence,
    grid_geometry=None,
):
    """
    Return positioned slot cells using detected grid, day, and time evidence.
    """
    geometry = grid_geometry or detect_grid_geometry(image_path)
    row_results = inspect_row_wise_boundaries(
        image_path,
        geometry=geometry,
    )
    merged_candidates = infer_merged_cells(row_results)
    words = ocr_evidence.get("words", [])
    x_lines = geometry["x_lines"]
    periods = _parse_header_times(
        words,
        x_lines,
        geometry["schedule_top"],
    )
    if not periods:
        return {
            "cells": [],
            "break_columns": [],
            "merged_candidates": merged_candidates,
            "grid_geometry": geometry,
            "periods": [],
            "error": "Could not reliably read period times from timetable header.",
            "review_status": "needs_review",
        }

    def column_for_x(x):
        return min(
            range(len(x_lines) - 1),
            key=lambda index: abs(
                x - (x_lines[index] + x_lines[index + 1]) / 2
            ),
        )

    weekday_words = {
        "MONDAY", "MON", "TUESDAY", "TUE", "TUES", "WEDNESDAY", "WED",
        "THURSDAY", "THU", "THUR", "FRIDAY", "FRI", "SATURDAY", "SAT",
        "SATURDA", "SUNDAY", "SUN",
    }
    day_columns = set()
    for word in words:
        text = str(word.get("text", "")).strip().upper().strip(".,:")
        bbox = word.get("original_bbox") or word.get("bbox") or {}
        if text in weekday_words and bbox:
            day_columns.add(
                column_for_x(bbox["x"] + bbox["width"] / 2)
            )

    period_columns = {period["column_index"] for period in periods}
    break_columns = sorted(
        set(range(len(x_lines) - 1)) - period_columns - day_columns
    )

    break_intervals = {}
    for column_index in break_columns:
        previous = [
            period for period in periods
            if period["column_index"] < column_index
        ]
        following = [
            period for period in periods
            if period["column_index"] > column_index
        ]
        if previous and following:
            previous_period = max(
                previous, key=lambda period: period["column_index"]
            )
            following_period = min(
                following, key=lambda period: period["column_index"]
            )
            if previous_period["end_time"] <= following_period["start_time"]:
                break_intervals[column_index] = (
                    previous_period["end_time"],
                    following_period["start_time"],
                )

    def line_records(cell_words):
        groups = []
        for word in cell_words:
            bbox = word.get("original_bbox") or word.get("bbox") or {}
            center_y = bbox["y"] + bbox["height"] / 2
            tolerance = max(6, bbox["height"] * 0.65)
            line = next(
                (
                    group for group in groups
                    if abs(group["center_y"] - center_y) <= tolerance
                ),
                None,
            )
            if line is None:
                groups.append({"center_y": center_y, "words": [word]})
            else:
                line["words"].append(word)
                line["center_y"] = sum(
                    (item.get("original_bbox") or item.get("bbox"))["y"]
                    + (item.get("original_bbox") or item.get("bbox"))["height"] / 2
                    for item in line["words"]
                ) / len(line["words"])

        result = []
        for group in sorted(groups, key=lambda item: item["center_y"]):
            ordered_words = sorted(
                group["words"],
                key=lambda word: (word.get("original_bbox") or word.get("bbox"))["x"],
            )
            boxes = [
                word.get("original_bbox") or word.get("bbox")
                for word in ordered_words
            ]
            left = min(box["x"] for box in boxes)
            top = min(box["y"] for box in boxes)
            right = max(box["x"] + box["width"] for box in boxes)
            bottom = max(box["y"] + box["height"] for box in boxes)
            result.append({
                "text": " ".join(str(word["text"]) for word in ordered_words),
                "bbox": {
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top,
                },
            })
        return result

    cells = []
    for row in row_results:
        boundaries = row["boundaries"]
        present = [
            item for item in boundaries
            if item["likely_present"]
        ]
        if len(present) < 2:
            continue

        y1 = row["y_start"]
        y2 = row["y_end"]
        day = _detect_cell_day(words, y1, y2)
        for left, right in zip(present, present[1:]):
            x1 = left["x"]
            x2 = right["x"]
            cell_words = []
            for word in words:
                bbox = word.get("original_bbox") or word.get("bbox") or {}
                if not bbox:
                    continue
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2
                if x1 <= cx < x2 and y1 <= cy < y2:
                    cell_words.append(word)
            cell_words.sort(
                key=lambda item: (
                    (item.get("original_bbox") or item.get("bbox"))["y"],
                    (item.get("original_bbox") or item.get("bbox"))["x"],
                )
            )
            cell_lines = line_records(cell_words)
            text = "\n".join(line["text"] for line in cell_lines).strip()
            if not text:
                continue

            covered_periods = []
            for period in periods:
                overlap = max(
                    0,
                    min(x2, period["x_end"])
                    - max(x1, period["x_start"]),
                )

                period_width = period["x_end"] - period["x_start"]

                if period_width > 0 and overlap / period_width >= 0.5:
                    covered_periods.append(period)

            covered_break_columns = [
                column_index
                for column_index in break_columns
                if max(
                    0,
                    min(x2, x_lines[column_index + 1])
                    - max(x1, x_lines[column_index]),
                )
                / (x_lines[column_index + 1] - x_lines[column_index])
                >= 0.5
            ]
            if not covered_periods and not covered_break_columns:
                continue

            covered_periods.sort(
                key=lambda period: period["column_index"]
            )
            if covered_periods:
                first_period = covered_periods[0]
                last_period = covered_periods[-1]
                start_time = first_period["start_time"]
                end_time = last_period["end_time"]
            else:
                first_interval = next(
                    (
                        break_intervals[index]
                        for index in covered_break_columns
                        if index in break_intervals
                    ),
                    (None, None),
                )
                start_time, end_time = first_interval

            bbox = {
                "x": x1,
                "y": y1,
                "width": x2 - x1,
                "height": y2 - y1,
            }
            is_merged = len(covered_periods) > 1
            cells.append({
                "row_index": row["row_index"],
                "day": day,
                "start_time": start_time,
                "end_time": end_time,
                "is_break": not covered_periods,
                "bbox": bbox,
                "source_bbox": bbox,
                "source_document": ocr_evidence.get("source_document"),
                "source_page": ocr_evidence.get("source_page"),
                "x_start": x1,
                "x_end": x2,
                "y_start": y1,
                "y_end": y2,
                "text": text,
                "period_count": len(covered_periods),
                "period_indices": [
                    period["column_index"]
                    for period in covered_periods
                ],
                "is_merged_candidate": is_merged,
                "words": cell_words,
                "review_status": "needs_review",
                "lines": cell_lines,
            })

    return {
        "cells": cells,
        "break_columns": break_columns,
        "merged_candidates": merged_candidates,
        "grid_geometry": geometry,
        "periods": periods,
        "error": None,
        "review_status": "needs_review",
    }
