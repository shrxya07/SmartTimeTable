
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps, ImageFilter

from app.ingestion.timetable_registry import get_available_timetables
from app.ingestion.grid_extractor import crop_timetable_area

tesseract_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if tesseract_path.exists():
    pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

output_dir = Path("data/uploads/all_timetable_ocr")
output_dir.mkdir(parents=True, exist_ok=True)

for item in get_available_timetables():
    crop_path = crop_timetable_area(item["image_path"])

    with Image.open(crop_path) as original:
        image = ImageOps.exif_transpose(original).convert("L")
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--psm 6",
        )

    output_path = output_dir / f"{item['timetable_id']}.txt"
    output_path.write_text(text, encoding="utf-8")

    print(f"{item['image']}: {len(text.splitlines())} OCR lines -> {output_path}")
