
import cv2
import numpy as np
import pytest

from app.ingestion.document_processor import (
    preprocess_image,
    process_timetable_image,
)


def test_preprocess_image_returns_dimensions():
    image = np.full((400, 800, 3), 255, dtype=np.uint8)

    processed, metadata = preprocess_image(image)

    assert processed is not None
    assert metadata["original_width"] == 800
    assert metadata["original_height"] == 400
    assert metadata["processed_width"] == 1800


def test_preprocess_rejects_empty_image():
    with pytest.raises(ValueError, match="empty"):
        preprocess_image(None)


def test_missing_image_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        process_timetable_image(tmp_path / "missing.png")


def test_ocr_evidence_structure(tmp_path, monkeypatch):
    image_path = tmp_path / "sample.png"

    image = np.full((400, 800, 3), 255, dtype=np.uint8)
    assert cv2.imwrite(str(image_path), image)

    def fake_ocr(*args, **kwargs):
        return {
            "text": ["Monday", "DS"],
            "conf": ["95", "90"],
            "left": [100, 250],
            "top": [100, 100],
            "width": [80, 40],
            "height": [25, 25],
            "block_num": [1, 1],
            "par_num": [1, 1],
            "line_num": [1, 1],
        }

    monkeypatch.setattr(
        "app.ingestion.document_processor.pytesseract.image_to_data",
        fake_ocr,
    )

    result = process_timetable_image(image_path)

    assert result["text"] == "Monday DS"
    assert result["word_count"] == 2
    assert result["ocr_confidence"] == 0.925
    assert result["words"][0]["text"] == "Monday"
    assert "original_bbox" in result["words"][0]
    assert result["status"] == "ocr_completed"
