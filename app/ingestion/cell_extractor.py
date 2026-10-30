
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

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
        for merged in (MERGED_CELLS if merged_cells is None else merged_cells):
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
