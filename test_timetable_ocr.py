
from pathlib import Path

from app.ingestion.ocr_reader import extract_text_from_image

image_path = Path("data/uploads/timetable.jpeg")

text = extract_text_from_image(image_path)

output_path = Path("data/uploads/timetable_ocr.txt")
output_path.write_text(text, encoding="utf-8")

print("OCR extraction complete.")
print(f"Extracted text saved to: {output_path}")
print("\n--- EXTRACTED TEXT ---\n")
print(text)
