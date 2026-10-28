
from app.ingestion.ocr_reader import TESSERACT_PATH
import pytesseract

pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)

print("Tesseract version:", pytesseract.get_tesseract_version())
print("OCR connection successful!")
