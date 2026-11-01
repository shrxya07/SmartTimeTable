"""Provider-independent LLM-assisted timetable interpretation."""

import json
import re


SYSTEM_PROMPT = """
You are a timetable document understanding assistant.

Convert OCR evidence from a college timetable into structured timetable
records.

Rules:
1. Extract only information supported by the supplied OCR evidence.
2. Do not invent teachers, subjects, rooms, days or times.
3. Preserve the original OCR text for every extracted record.
4. Use the exact string UNKNOWN when a value cannot be read; do not guess.
5. Identify uncertain or ambiguous information.
6. Handle merged timetable cells by preserving their actual start and end
   times when the evidence supports them.
7. Return JSON only, following the requested structure.
8. Use needs_review whenever information is uncertain.
"""


UNKNOWN_VALUES = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL"}


def _is_unknown(value):
    return not isinstance(value, str) or value.strip().upper() in UNKNOWN_VALUES


def build_extraction_prompt(ocr_text, metadata=None):
    """Build a provider-independent prompt from OCR evidence."""
    metadata = metadata or {}

    return f"""
Interpret this timetable OCR evidence.

Document metadata:
{json.dumps(metadata, ensure_ascii=False)}

OCR evidence:
{json.dumps(ocr_text, ensure_ascii=False)}

Return this JSON structure:

{{
  "records": [
    {{
      "day": "Monday",
      "start_time": "09:00",
      "end_time": "09:55",
      "subject": "Subject name or UNKNOWN",
      "teacher_name": "Teacher name or UNKNOWN",
      "room": "Room name or UNKNOWN",
      "original_text": "Original OCR text",
      "confidence": 0.0,
      "review_status": "needs_review",
      "uncertainties": []
    }}
  ],
  "document_uncertainties": []
}}

Use UNKNOWN for unreadable teacher or room values. Do not make up values.
Confidence must be between 0 and 1. The application, not this model,
decides whether a record can be imported after human approval.
"""


def _extract_json(response):
    """Parse JSON from a string or a provider response."""
    if isinstance(response, dict):
        return response

    if not isinstance(response, str):
        raise TypeError("LLM response must be a string or dictionary")

    content = response.strip()
    content = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        content,
        flags=re.IGNORECASE,
    )

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM returned invalid JSON") from exc


def validate_extraction(result):
    """Validate structure and mark unresolved values for human review."""
    if not isinstance(result, dict):
        raise ValueError("Extraction result must be a dictionary")

    records = result.get("records")
    if not isinstance(records, list):
        raise ValueError("'records' must be a list")

    required = (
        "day",
        "start_time",
        "end_time",
        "subject",
        "teacher_name",
        "room",
        "original_text",
    )

    validated = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} must be an object")

        record = record.copy()
        uncertainties = list(record.get("uncertainties") or [])

        for field in required:
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                uncertainties.append(f"Missing or invalid {field}")
            elif _is_unknown(value) and field != "original_text":
                uncertainties.append(f"Unresolved {field}")

        try:
            confidence = float(record.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0

        if not 0 <= confidence <= 1:
            confidence = 0
            uncertainties.append("Invalid confidence score")

        record["confidence"] = confidence
        record["uncertainties"] = sorted(set(uncertainties))
        # Model confidence never verifies timetable facts.
        record["review_status"] = "needs_review"
        validated.append(record)

    return {
        "records": validated,
        "document_uncertainties": result.get("document_uncertainties", []),
    }


def interpret_timetable_document(ocr_evidence, llm_client, metadata=None):
    """
    Interpret OCR evidence using an injected LLM client.

    Client contract:
        llm_client(system_prompt, user_prompt) -> JSON string or dict
    """
    if not callable(llm_client):
        raise TypeError("llm_client must be callable")
    if not ocr_evidence:
        raise ValueError("OCR evidence cannot be empty")

    prompt = build_extraction_prompt(ocr_evidence, metadata=metadata)
    response = llm_client(SYSTEM_PROMPT, prompt)
    return validate_extraction(_extract_json(response))
