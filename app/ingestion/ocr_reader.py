
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps, ImageFilter


TESSERACT_PATH = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_PATH.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)


def extract_text_from_image(image_path):
    """Extract text from a timetable image after basic preprocessing."""
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    with Image.open(image_path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")

        # Enlarge small text before OCR.
        image = image.resize(
            (image.width * 2, image.height * 2),
            Image.Resampling.LANCZOS,
        )

        # Improve contrast and convert to grayscale.
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)

        # Preserve the original image structure as much as possible.
        text = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--psm 11",
        )

    return text.strip()
