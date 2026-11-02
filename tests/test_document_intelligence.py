
import pytest

from app.ingestion.document_intelligence import (
    build_extraction_prompt,
    interpret_unresolved_lines,
    interpret_timetable_document,
    validate_against_evidence,
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


def test_unresolved_line_is_sent_to_llm_and_retains_source_evidence():
    deterministic_line = {
        "text": "DBMS",
        "status": "resolved",
        "subject": "Database Systems",
    }
    line = {
        "text": "DS LAB (AS) LHC 206",
        "bbox": {"x": 10, "y": 20, "width": 150, "height": 20},
        "resolution_status": "unresolved",
    }
    calls = []

    def llm_client(system_prompt, user_prompt):
        calls.append((system_prompt, user_prompt))
        return {
            "interpretations": [{
                "line_index": 1,
                "subject": "Data Structures Lab",
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "teacher_name": "Alice Smith",
                "teacher_initials": ["AS"],
                "room": "LHC 206",
                "confidence": 0.99,
                "uncertainties": [],
            }]
        }

    result = interpret_unresolved_lines(
        [deterministic_line, line],
        {
            "courses": ["Data Structures Lab", "DS LAB"],
            "faculty": ["Alice Smith"],
            "initials": ["AS"],
            "rooms": ["LHC 206"],
        },
        llm_client,
    )

    assert len(calls) == 1
    assert "unresolved timetable lines" in calls[0][0]
    assert "DS LAB (AS) LHC 206" in calls[0][1]
    assert "Database Systems" not in calls[0][1]
    interpretation = result["interpretations"][0]
    assert interpretation["line_index"] == 1
    assert interpretation["evidence"] == line
    assert interpretation["original_text"] == line["text"]
    assert interpretation["teacher_name"] == "Alice Smith"
    assert interpretation["teacher_initials"] == ["AS"]
    assert interpretation["room"] == "LHC 206"
    assert result["skipped_lines"] == [{
        "line_index": 0,
        "evidence": deterministic_line,
    }]


def test_deterministically_resolved_lines_are_not_sent_to_llm():
    calls = []
    line = {
        "text": "DBMS",
        "resolved": True,
        "subject": "Database Systems",
    }

    result = interpret_unresolved_lines(
        [line],
        {},
        lambda *args: calls.append(args),
    )

    assert calls == []
    assert result["interpretations"] == []
    assert result["skipped_lines"] == [{"line_index": 0, "evidence": line}]


def test_evidence_validation_accepts_supported_names_initials_and_rooms():
    result = validate_against_evidence(
        {
            "interpretations": [{
                "subject": "Data Structures",
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "teacher_name": "Dr Alice Smith",
                "teacher_initials": ["AS"],
                "room": "LHC 206",
                "confidence": 0.99,
            }]
        },
        "Monday Data Structures 09:00 to 09:55 in LHC 206, "
        "taught by Dr Alice Smith (AS).",
        {
            "faculty": ["Dr Alice Smith"],
            "initials": ["AS"],
            "rooms": ["LHC 206"],
        },
    )

    record = result["interpretations"][0]
    assert record["teacher_name"] == "Dr Alice Smith"
    assert record["teacher_initials"] == ["AS"]
    assert record["room"] == "LHC 206"
    assert record["unresolved_fields"] == {}
    assert record["review_status"] == "needs_review"


@pytest.mark.parametrize(
    ("field", "proposed", "ocr_text", "legend"),
    [
        ("teacher_name", "Invented Professor", "DS lab with Alice Smith", {}),
        ("teacher_initials", ["ZZ"], "DS lab with Alice Smith", {}),
        ("room", "LHC 999", "DS lab in LHC 206", {}),
        ("subject", "Invented Course", "DS lab with Alice Smith", {}),
    ],
)
def test_evidence_validation_rejects_invented_values(
    field,
    proposed,
    ocr_text,
    legend,
):
    record = {
        "teacher_name": "Alice Smith",
        "teacher_initials": ["AS"],
        "room": "LHC 206",
        field: proposed,
    }
    result = validate_against_evidence(
        {"interpretations": [record]},
        ocr_text,
        {
            **legend,
            "faculty": ["Alice Smith"],
            "initials": ["AS"],
            "rooms": ["LHC 206"],
        },
    )

    validated = result["interpretations"][0]
    canonical_field = field
    assert validated[canonical_field] is None
    assert validated["unresolved_fields"][canonical_field]["proposed_value"] == proposed
    assert f"Unsupported {canonical_field}" in validated["uncertainties"]
    assert validated["review_status"] == "needs_review"


def test_missing_and_ambiguous_information_stays_unresolved():
    result = validate_against_evidence(
        {
            "interpretations": [{
                "subject": "Data Structures",
                "day": "Monday",
                "start_time": "09:00",
                "end_time": "09:55",
                "teacher_name": "UNKNOWN",
                "teacher_initials": None,
                "room": "",
                "uncertainties": ["Faculty not clear"],
            }]
        },
        "Monday Data Structures 09:00 to 09:55, in LHC 206 with A. S.",
        {"faculty": ["Alice Smith"], "initials": ["AS"]},
    )

    record = result["interpretations"][0]
    assert record["teacher_name"] is None
    assert record["teacher_initials"] is None
    assert record["room"] is None
    assert set(record["unresolved_fields"]) == {
        "teacher_name", "teacher_initials", "room",
    }
    assert record["review_status"] == "needs_review"


def test_llm_confidence_never_verifies_or_approves_an_interpretation():
    result = interpret_unresolved_lines(
        ["LAB by Alice Smith (AS) at LHC 206"],
        {"faculty": ["Alice Smith"], "initials": ["AS"], "rooms": ["LHC 206"]},
        lambda *_: {
            "interpretations": [{
                "line_index": 0,
                "subject": "LAB",
                "teacher_name": "Alice Smith",
                "teacher_initials": ["AS"],
                "room": "LHC 206",
                "confidence": 1.0,
                "status": "approved",
                "review_status": "verified",
                "verified": True,
                "approved": True,
            }]
        },
    )

    record = result["interpretations"][0]
    assert record["confidence"] == 1.0
    assert record["status"] == "needs_review"
    assert record["review_status"] == "needs_review"
    assert record["verified"] is False
    assert record["approved"] is False
    assert result["verified"] is False
    assert result["approved"] is False


def test_backward_compatible_interpretation_still_returns_records():
    result = interpret_timetable_document(
        "Monday DS 09:00",
        mock_llm,
    )

    assert set(result) == {"records", "document_uncertainties"}
    assert result["records"][0]["review_status"] == "verified"
