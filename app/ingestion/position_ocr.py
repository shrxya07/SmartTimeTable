
from pathlib import Path

import cv2
import pytesseract
from pytesseract import Output


IMAGE_PATH = Path("data/uploads/cse_sem3_a.png")
TESSERACT_PATH = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def extract_positioned_words(image_path):
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")

    # Use the existing crop function to focus on the schedule.
    from app.ingestion.grid_extractor import crop_timetable_area

    crop_path = crop_timetable_area(image_path)
    crop = cv2.imread(str(crop_path))
    if crop is None:
        raise FileNotFoundError(
        f"Cannot read cropped timetable image: {crop_path}"
    )
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray, None, fx=1.5, fy=1.5,
        interpolation=cv2.INTER_CUBIC,
    )

    data = pytesseract.image_to_data(
        gray,
        lang="eng",
        config="--psm 11",
        output_type=Output.DICT,
    )

    words = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue

        words.append({
            "text": text,
            "x": round(data["left"][i] / 1.5),
            "y": round(data["top"][i] / 1.5),
            "width": round(data["width"][i] / 1.5),
            "height": round(data["height"][i] / 1.5),
            "confidence": data["conf"][i],
        })

    return words


if __name__ == "__main__":
    words = extract_positioned_words(IMAGE_PATH)

    output_path = IMAGE_PATH.with_name(
        "cse_sem3_a_positioned_words.txt"
    )

    output_path.write_text(
        "\n".join(
            f"y={w['y']:3} x={w['x']:4} "
            f"conf={w['confidence']:>3} text={w['text']}"
            for w in words
        ),
        encoding="utf-8",
    )

    print(f"Detected {len(words)} words")
    print(f"Saved coordinates to: {output_path}")
    for word in words[:30]:
        print(word)
