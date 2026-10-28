
from pathlib import Path

from app.ingestion.position_ocr import extract_positioned_words


IMAGE_PATH = Path("data/uploads/cse_sem3_a.png")

# Approximate vertical boundaries from the inspected timetable crop.

ROWS = [
    ("HEADER", 42, 122),
    ("Monday", 122, 184),
    ("Tuesday", 184, 240),
    ("Wednesday", 240, 308),
    ("Thursday", 308, 355),
    ("Friday", 355, 432),
    ("Saturday", 432, 474),
]



def build_row_preview(image_path=IMAGE_PATH):
    words = extract_positioned_words(image_path)
    output = []

    for day, top, bottom in ROWS:
        row_words = [
            word for word in words
            if top <= word["y"] < bottom
        ]
        row_words.sort(key=lambda word: word["x"])

        line = " ".join(word["text"] for word in row_words)
        output.append(f"{day}: {line}")

    return "\n".join(output)


def show_row_word_positions(image_path=IMAGE_PATH):
    words = extract_positioned_words(image_path)

    for day, top, bottom in ROWS[1:]:
        row_words = [
            word for word in words
            if top <= word["y"] < bottom
        ]
        row_words.sort(key=lambda word: word["x"])

        print(f"\n{day}")
        for word in row_words:
            print(
                f"x={word['x']:4} "
                f"text={word['text']}"
            )

TIME_COLUMNS = [
    (263, 445, "09:00", "09:55"),
    (445, 617, "09:55", "10:50"),
    (617, 820, "11:05", "12:00"),
    (820, 994, "12:00", "12:55"),
    (994, 1054, None, None),  # Break area
    (1054, 1245, "13:45", "14:40"),
    (1245, 1388, "14:40", "15:35"),
    (1388, 1568, "15:35", "16:30"),
]



def build_column_preview(image_path=IMAGE_PATH):
    """Preview OCR words by day and period, preserving multiline labels."""
    words = extract_positioned_words(image_path)
    output = []

    # Keep the original approximate period boundaries for this preview.
    for day, top, bottom in ROWS[1:]:
        row_words = [
            word for word in words
            if top <= word["y"] < bottom
            and word["text"].upper() != day.upper()
        ]

        output.append(f"\n{day}")

        # Group words into visual text lines using their y-coordinates.
        lines = []
        for word in sorted(row_words, key=lambda w: (w["y"], w["x"])):
            matching_line = next(
                (
                    line for line in lines
                    if abs(line["y"] - word["y"]) <= 12
                ),
                None,
            )

            if matching_line is None:
                lines.append({"y": word["y"], "words": [word]})
            else:
                matching_line["words"].append(word)
                matching_line["y"] = sum(
                    item["y"] for item in matching_line["words"]
                ) / len(matching_line["words"])

        # Assign each word line to approximate periods.
        for left, right, start, end in TIME_COLUMNS:
            if start is None:
                continue

            period_lines = []
            for line in lines:
                line_words = sorted(line["words"], key=lambda w: w["x"])
                selected = [
                    word["text"] for word in line_words
                    if left <= word["x"] < right
                ]
                if selected:
                    period_lines.append(" ".join(selected))

            if period_lines:
                output.append(
                    f"  {start}-{end}: {' / '.join(period_lines)}"
                )

    return "\n".join(output)

def inspect_day_coordinates(day_name="Wednesday", image_path=IMAGE_PATH):
    words = extract_positioned_words(image_path)

    row_bounds = {
        day: (top, bottom)
        for day, top, bottom in ROWS
    }

    if day_name not in row_bounds:
        print(f"Unknown day: {day_name}")
        return

    top, bottom = row_bounds[day_name]

    row_words = [
        word for word in words
        if top <= word["y"] < bottom
        and word["text"].upper() != day_name.upper()
    ]

    for word in sorted(row_words, key=lambda item: (item["y"], item["x"])):
        print(
            f"x={word['x']:4} "
            f"y={word['y']:3} "
            f"text={word['text']}"
        )
def inspect_horizontal_gaps(day_name="Wednesday", image_path=IMAGE_PATH):
    words = extract_positioned_words(image_path)

    row_bounds = {
        day: (top, bottom)
        for day, top, bottom in ROWS
    }

    top, bottom = row_bounds[day_name]

    row_words = [
        word for word in words
        if top <= word["y"] < bottom
        and word["text"].upper() != day_name.upper()
    ]

    row_words.sort(key=lambda item: item["x"])

    print(f"\nHorizontal gaps for {day_name}:\n")

    for left, right in zip(row_words, row_words[1:]):
        gap = right["x"] - (
            left["x"] + left["width"]
        )

        print(
            f"{left['text']:<18} -> "
            f"{right['text']:<18} "
            f"gap={gap:4}"
        )
if __name__ == "__main__":
    print(build_column_preview())