
from app.ingestion.extraction_pipeline import run_extraction_pipeline


def test_pipeline_connects_ocr_and_genai(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    fake_ocr = {
        "text": "Monday 09:00 Data Structures",
        "ocr_confidence": 0.91,
        "words": [],
        "source_document": "sample.png",
    }

    def fake_process(**kwargs):
        return fake_ocr

    def fake_interpret(ocr_evidence, llm_client, metadata):
        assert ocr_evidence["text"] == fake_ocr["text"]
        assert metadata["source_document"] == "sample.png"
        return {
            "records": [
                {
                    "day": "Monday",
                    "start_time": "09:00",
                    "end_time": "09:55",
                    "subject": "Data Structures",
                    "teacher_name": "Unknown",
                    "room": "LHC 301",
                    "original_text": "Monday 09:00 Data Structures",
                    "confidence": 0.95,
                }
            ]
        }

    monkeypatch.setattr(pipeline, "process_timetable_image", fake_process)
    monkeypatch.setattr(
        pipeline, "interpret_timetable_document", fake_interpret
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "sample.png",
        llm_client=lambda system, prompt: "{}",
    )

    assert result["status"] == "extraction_completed"
    assert len(result["records"]) == 1

    record = result["records"][0]
    assert record["source_document"] == "sample.png"
    assert record["ocr_confidence"] == 0.91
    assert record["confidence"] == 0.95
    assert record["review_status"] == "needs_review"


def test_pipeline_handles_empty_ocr(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    def fake_process(**kwargs):
        return {
            "text": "",
            "ocr_confidence": 0.0,
            "words": [],
        }

    def unexpected_interpret(*args, **kwargs):
        raise AssertionError("LLM should not be called for empty OCR")

    monkeypatch.setattr(pipeline, "process_timetable_image", fake_process)
    monkeypatch.setattr(
        pipeline, "interpret_timetable_document", unexpected_interpret
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "blank.png",
        llm_client=lambda system, prompt: "{}",
    )

    assert result["status"] == "needs_review"
    assert result["records"] == []


def test_pipeline_preserves_page_provenance(monkeypatch, tmp_path):
    import app.ingestion.extraction_pipeline as pipeline

    monkeypatch.setattr(
        pipeline,
        "process_timetable_image",
        lambda **kwargs: {
            "text": "Tuesday 10:00 Mathematics",
            "ocr_confidence": 0.88,
            "words": [],
        },
    )

    monkeypatch.setattr(
        pipeline,
        "interpret_timetable_document",
        lambda **kwargs: {
            "records": [
                {
                    "day": "Tuesday",
                    "start_time": "10:00",
                    "end_time": "10:55",
                    "subject": "Mathematics",
                    "teacher_name": "Unknown",
                    "room": "LHC 301",
                    "original_text": "Tuesday 10:00 Mathematics",
                    "confidence": 0.9,
                }
            ]
        },
    )

    result = run_extraction_pipeline(
        image_path=tmp_path / "sample.png",
        llm_client=lambda system, prompt: "{}",
        metadata={
            "source_document": "semester4.pdf",
            "source_page": 2,
        },
    )

    record = result["records"][0]
    assert record["source_document"] == "semester4.pdf"
    assert record["source_page"] == 2
