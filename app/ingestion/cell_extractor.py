
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


def extract_cells(image_path=IMAGE_PATH):
    """Extract OCR words and group them by weekday and time period."""
    crop_path = crop_timetable_area(image_path)
    image = cv2.imread(str(crop_path))

    if image is None:
        raise ValueError(f"Could not read image: {crop_path}")

    data = pytesseract.image_to_data(
        image,
        config="--psm 11",
        output_type=Output.DICT,
    )

    records = []

    for day, top, bottom in ROWS:
        for left, right, start, end in PERIODS:
            cell_words = []

            for i, raw_text in enumerate(data["text"]):
                text = raw_text.strip()
                if not text:
                    continue

                x = data["left"][i] + data["width"][i] / 2
                y = data["top"][i] + data["height"][i] / 2

                if top <= y < bottom and left <= x < right:
                    cell_words.append((y, x, text))

            if cell_words:
                cell_words.sort(key=lambda item: (item[0], item[1]))

                records.append({
                    "day": day,
                    "start_time": start,
                    "end_time": end,
                    "text": " ".join(item[2] for item in cell_words),
                    "review_status": "needs_review",
                })

    return records


if __name__ == "__main__":
    for record in extract_cells():
        print(
            f"{record['day']:10} "
            f"{record['start_time']}-{record['end_time']}: "
            f"{record['text']}"
        )
