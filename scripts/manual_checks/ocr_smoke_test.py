
from pathlib import Path

import pytesseract
from PIL import Image

IMAGE_PATH = Path("data/uploads/cse_sem3_a_schedule_crop.png")

TESSERACT_PATH = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def read_cell(image_path):
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        text = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--psm 6",
        )
    return text.strip()


if __name__ == "__main__":
    print(read_cell(IMAGE_PATH))
