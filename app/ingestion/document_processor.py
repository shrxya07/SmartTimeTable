
"""Preprocess timetable images and produce structured OCR evidence."""

import os
from pathlib import Path

import cv2
import pytesseract
from pytesseract import Output


def preprocess_image(image):
    """Improve image readability without changing its layout."""
    if image is None:
        raise ValueError("Input image is empty")

    original_height, original_width = image.shape[:2]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Upscale small images to improve OCR readability.
    scale = 1.0
    if original_width < 1800:
        scale = 1800 / original_width
        gray = cv2.resize(
            gray,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )

    # Improve contrast while preserving table boundaries.
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )
    enhanced = clahe.apply(gray)

    return enhanced, {
        "original_width": original_width,
        "original_height": original_height,
        "processed_width": enhanced.shape[1],
        "processed_height": enhanced.shape[0],
        "scale": scale,
    }


def process_timetable_image(
    image_path,
    lang="eng",
    psm=11,
    tesseract_cmd=None,
):
    """
    Extract OCR text, bounding boxes and confidence from an image.

    Used to cross-check text reported by the vision model.
    Coordinates are provided in both original and processed image space.
    """
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Could not read image: {image_path}")

    command = tesseract_cmd or os.getenv("TESSERACT_CMD")

    if command:
        pytesseract.pytesseract.tesseract_cmd = command

    processed, dimensions = preprocess_image(image)

    data = pytesseract.image_to_data(
        processed,
        lang=lang,
        config=f"--oem 3 --psm {psm}",
        output_type=Output.DICT,
    )

    scale = dimensions["scale"]
    words = []

    for index, raw_text in enumerate(data["text"]):
        word = raw_text.strip()

        if not word:
            continue

        try:
            raw_confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            raw_confidence = -1

        # Tesseract uses -1 for non-word entries.
        if raw_confidence < 0:
            continue

        x = int(data["left"][index])
        y = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])

        words.append({
            "text": word,
            "confidence": round(raw_confidence / 100, 4),
            "bbox": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            "original_bbox": {
                "x": round(x / scale),
                "y": round(y / scale),
                "width": round(width / scale),
                "height": round(height / scale),
            },
            "block_num": data["block_num"][index],
            "par_num": data["par_num"][index],
            "line_num": data["line_num"][index],
        })

    text = " ".join(item["text"] for item in words)

    confidence_values = [
        item["confidence"] for item in words
    ]

    average_confidence = (
        round(sum(confidence_values) / len(confidence_values), 4)
        if confidence_values
        else 0.0
    )

    return {
        "schema_version": "1.0",
        "source_document": image_path.name,
        "source_path": str(image_path),
        "document_type": "image",
        "dimensions": dimensions,
        "text": text,
        "words": words,
        "word_count": len(words),
        "ocr_confidence": average_confidence,
        "status": "needs_review" if not words else "ocr_completed",
    }
