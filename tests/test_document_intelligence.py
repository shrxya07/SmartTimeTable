
import pytest

from app.ingestion.document_intelligence import (
    build_extraction_prompt,
    interpret_timetable_document,
    validate_extraction,
)


def mock_llm(system_prompt, user_prompt):
    return {
        "records": [
            {
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "subject": "Data Structures",
                "teacher_name": "Dr Rao",
                "room": "LHC 301",
                "original_text": "DS (DR) LHC 301",
                "confidence": 0.95,
                "uncertainties": [],
            }
        ],
        "document_uncertainties": [],
    }


def test_prompt_contains_ocr_evidence():
    prompt = build_extraction_prompt("Monday DS 09:00")

    assert "Monday DS 09:00" in prompt


def test_llm_interpretation_returns_records():
    result = interpret_timetable_document(
        "Monday DS 09:00",
        mock_llm,
    )

    assert len(result["records"]) == 1
    assert result["records"][0]["subject"] == "Data Structures"


def test_uncertain_records_need_review():
    result = validate_extraction({
        "records": [{
            "day": "Monday",
            "start_time": "09:00",
            "end_time": "09:55",
            "subject": "UNKNOWN",
            "teacher_name": "UNKNOWN",
            "room": "UNKNOWN",
            "original_text": "unclear text",
            "confidence": 0.4,
        }]
    })

    assert result["records"][0]["review_status"] == "needs_review"


def test_missing_fields_need_review():
    result = validate_extraction({
        "records": [{
            "day": "Monday",
            "subject": "DBMS",
            "confidence": 0.9,
        }]
    })

    assert result["records"][0]["review_status"] == "needs_review"


def test_invalid_json_is_rejected():
    def bad_llm(system_prompt, user_prompt):
        return "not valid json"

    with pytest.raises(ValueError, match="invalid JSON"):
        interpret_timetable_document("Monday DS", bad_llm)


def test_empty_ocr_is_rejected():
    with pytest.raises(ValueError, match="cannot be empty"):
        interpret_timetable_document("", mock_llm)
