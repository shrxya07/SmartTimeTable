
"""Provider-independent LLM-assisted timetable interpretation."""

import json
import re
from dataclasses import asdict, is_dataclass


SYSTEM_PROMPT = """
You are a timetable document understanding assistant.

Convert OCR evidence from a college timetable into structured timetable
records.

Rules:
1. Extract only information supported by the supplied OCR evidence.
2. Do not invent teachers, subjects, rooms, days or times.
3. Preserve the original OCR text for every extracted record.
4. Identify uncertain or ambiguous information.
5. Handle merged timetable cells by preserving their actual start and end
   times when the evidence supports them.
6. Return JSON only, following the requested structure.
7. Use needs_review whenever information is uncertain.
8. The OCR payload may contain both timetable cell evidence and a separately
   parsed course/faculty reference table. Treat these as different regions.
9. Resolve abbreviations and initials only when an explicit reference-table
   row supports the match; never infer a faculty member from subject alone.
10. Preserve section/subgroup identifiers as structured fields when present.
11. Do not turn activities (industry talk, proctor meeting, partial delivery)
    into ordinary courses or invent faculty/rooms for them.
"""


def build_extraction_prompt(ocr_text, metadata=None):
    """Build a provider-independent prompt from OCR evidence."""
    metadata = metadata or {}

    return f"""
Interpret this timetable OCR evidence.

The OCR payload may include:
- cell_evidence: timetable grid cells, with day/time and original cell text
- reference_table: course-code, course-name and faculty mappings extracted
  from the lower reference table on the same page

Use reference_table only to resolve matching timetable entries. Keep course
rows that are not scheduled out of the timetable records. If a course has
section-specific faculty assignments, preserve each section separately.
Do not collapse simultaneous electives (for example OOAD/BDA) into one
ordinary course record when the reference table distinguishes their groups.

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
      "review_status": "verified or needs_review",
      "uncertainties": []
    }}
  ],
  "document_uncertainties": []
}}

Confidence must be between 0 and 1.
Never mark a record verified if its essential information is uncertain.
"""


def _extract_json(response):
    """Parse JSON from a string or a provider response."""
    if isinstance(response, dict):
        return response

    if not isinstance(response, str):
        raise TypeError("LLM response must be a string or dictionary")

    content = response.strip()

    # Support JSON enclosed in Markdown code fences.
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
    """Validate the structure and flag incomplete records."""
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
            if not isinstance(record.get(field), str) or not record[field].strip():
                uncertainties.append(f"Missing or invalid {field}")

        confidence = record.get("confidence", 0)

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0

        if not 0 <= confidence <= 1:
            confidence = 0
            uncertainties.append("Invalid confidence score")

        record["confidence"] = confidence
        record["uncertainties"] = sorted(set(uncertainties))

        if record["uncertainties"] or confidence < 0.8:
            record["review_status"] = "needs_review"
        else:
            record["review_status"] = "verified"

        validated.append(record)

    return {
        "records": validated,
        "document_uncertainties": result.get(
            "document_uncertainties", []
        ),
    }


def interpret_timetable_document(
    ocr_evidence,
    llm_client,
    metadata=None,
):
    """
    Interpret OCR evidence using an injected LLM client.

    Client contract:
        llm_client(system_prompt, user_prompt) -> JSON string or dict

    The application does not depend on a specific LLM provider.
    """
    if not callable(llm_client):
        raise TypeError("llm_client must be callable")

    if not ocr_evidence:
        raise ValueError("OCR evidence cannot be empty")

    prompt = build_extraction_prompt(
        ocr_evidence,
        metadata=metadata,
    )

    response = llm_client(SYSTEM_PROMPT, prompt)
    result = _extract_json(response)

    return validate_extraction(result)


_UNRESOLVED_VALUES = {
    "",
    "unknown",
    "none",
    "null",
    "unresolved",
    "not found",
}
_EVIDENCE_FIELDS = {
    "teacher_name": ("teacher_name", "faculty_name"),
    "teacher_initials": (
        "teacher_initials",
        "faculty_initials",
        "teacher_alias",
        "faculty_alias",
        "initials",
    ),
    "room": ("room",),
}
_TEXT_EVIDENCE_FIELDS = (
    "subject",
    "course",
    "course_abbrev",
    "course_code",
    "day",
    "start_time",
    "end_time",
)
_REQUIRED_INTERPRETATION_FIELDS = {
    "subject",
    "day",
    "start_time",
    "end_time",
}


def _normalized_tokens(value):
    return re.findall(r"[A-Z0-9]+", str(value or "").upper())


def _contains_evidence(value, evidence):
    candidate = _normalized_tokens(value)
    source = _normalized_tokens(evidence)
    if not candidate or len(candidate) > len(source):
        return False
    return any(
        source[index:index + len(candidate)] == candidate
        for index in range(len(source) - len(candidate) + 1)
    )


def _is_unresolved_value(value):
    return value is None or (
        isinstance(value, str)
        and value.strip().casefold() in _UNRESOLVED_VALUES
    )


def _legend_evidence_text(legend):
    if legend is None:
        return ""
    if isinstance(legend, str):
        return legend
    if is_dataclass(legend):
        return _legend_evidence_text(asdict(legend))
    if isinstance(legend, dict):
        values = []
        for key, value in legend.items():
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, dict):
                values.extend(
                    str(item)
                    for item in value.values()
                    if isinstance(item, (str, int, float))
                )
            elif isinstance(value, (list, tuple, set)):
                values.extend(
                    str(item)
                    if isinstance(item, (str, int, float))
                    else _legend_evidence_text(item)
                    for item in value
                )
        return " ".join(values)
    if isinstance(legend, (list, tuple, set)):
        return " ".join(_legend_evidence_text(item) for item in legend)
    return ""


def _line_evidence_text(lines):
    values = []
    for line in lines:
        if isinstance(line, str):
            values.append(line)
        elif isinstance(line, dict):
            for key in ("text", "original_text", "line", "ocr_text"):
                value = line.get(key)
                if isinstance(value, str):
                    values.append(value)
            for key in _TEXT_EVIDENCE_FIELDS:
                value = line.get(key)
                if isinstance(value, str):
                    values.append(value)
            for word in line.get("words", []) or []:
                if isinstance(word, dict) and isinstance(word.get("text"), str):
                    values.append(word["text"])
            for field in ("evidence", "ocr_evidence"):
                value = line.get(field)
                if isinstance(value, str):
                    values.append(value)
                elif isinstance(value, dict):
                    values.extend(
                        str(word["text"])
                        for word in value.get("words", []) or []
                        if isinstance(word, dict)
                        and isinstance(word.get("text"), str)
                    )
                    if isinstance(value.get("text"), str):
                        values.append(value["text"])
    return " ".join(values)


def _is_deterministically_resolved(line):
    if not isinstance(line, dict):
        return False
    if any(
        line.get(key) is True
        for key in ("deterministic_resolved", "is_resolved", "resolved")
    ):
        return True
    return str(line.get("resolution_status", line.get("status", ""))).casefold() in {
        "resolved",
        "verified",
        "approved",
        "deterministically_resolved",
    }


def _unresolved_line_text(line):
    if isinstance(line, str):
        return line
    if isinstance(line, dict):
        for key in ("original_text", "text", "line", "ocr_text"):
            if isinstance(line.get(key), str):
                return line[key]
    return ""


def _validate_interpretation_fields(interpretation, evidence_text):
    record = dict(interpretation)
    raw_uncertainties = record.get("uncertainties")
    if raw_uncertainties is None:
        uncertainties = []
    elif isinstance(raw_uncertainties, (list, tuple, set)):
        uncertainties = list(raw_uncertainties)
    else:
        uncertainties = [raw_uncertainties]

    raw_unresolved_fields = record.get("unresolved_fields")
    unresolved_fields = (
        dict(raw_unresolved_fields)
        if isinstance(raw_unresolved_fields, dict)
        else {}
    )
    if raw_unresolved_fields is not None and not isinstance(
        raw_unresolved_fields,
        dict,
    ):
        uncertainties.append("Invalid unresolved_fields structure")

    for field in _TEXT_EVIDENCE_FIELDS:
        if field in record and _is_unresolved_value(record[field]):
            record[field] = None
            unresolved_fields.setdefault(
                field,
                {"status": "unresolved", "reason": "not provided"},
            )
        elif field in record and not _contains_evidence(record[field], evidence_text):
            unresolved_fields[field] = {
                "status": "unresolved",
                "reason": "not supported by OCR or legend evidence",
                "proposed_value": record[field],
            }
            record[field] = None
            uncertainties.append(f"Unsupported {field}")
        elif field not in record and field in _REQUIRED_INTERPRETATION_FIELDS:
            unresolved_fields.setdefault(
                field,
                {"status": "unresolved", "reason": "not provided"},
            )

    for field, aliases in _EVIDENCE_FIELDS.items():
        present_key = next(
            (key for key in aliases if key in record),
            field,
        )
        value = record.get(present_key)
        if _is_unresolved_value(value):
            record[present_key] = None
            unresolved_fields.setdefault(
                field,
                {"status": "unresolved", "reason": "not provided"},
            )
            continue

        supported = False
        if field == "teacher_initials":
            values = (
                [item for item in value if item is not None]
                if isinstance(value, (list, tuple, set))
                else re.split(r"[,/&\s]+", str(value).strip("() "))
            )
            values = [str(item).strip("() ") for item in values if str(item).strip("() ")]
            supported = bool(values) and all(
                _contains_evidence(item, evidence_text)
                for item in values
            )
            if supported and not isinstance(value, (list, tuple)):
                record[present_key] = tuple(item.upper() for item in values)
        else:
            supported = _contains_evidence(value, evidence_text)

        if not supported:
            unresolved_fields[field] = {
                "status": "unresolved",
                "reason": "not supported by OCR or legend evidence",
                "proposed_value": value,
            }
            record[present_key] = None
            uncertainties.append(f"Unsupported {field}")
        else:
            unresolved_fields.pop(field, None)

    record["uncertainties"] = sorted(set(str(item) for item in uncertainties))
    record["unresolved_fields"] = unresolved_fields
    record["review_status"] = "needs_review"
    record["verified"] = False
    record["approved"] = False
    if str(record.get("status", "")).casefold() in {
        "verified",
        "approved",
        "confirmed",
    }:
        record["status"] = "needs_review"
    return record


def validate_against_evidence(result, ocr_text, legend):
    """Reject unsupported faculty, initials, and room values without guessing."""
    if isinstance(result, list):
        container = {"interpretations": result}
    elif isinstance(result, dict):
        container = dict(result)
    else:
        raise ValueError("Evidence validation result must be an object or list")

    key = next(
        (
            candidate
            for candidate in ("interpretations", "records")
            if isinstance(container.get(candidate), list)
        ),
        None,
    )
    if key is None:
        raise ValueError("'interpretations' must be a list")

    evidence_text = " ".join(
        value
        for value in (str(ocr_text or ""), _legend_evidence_text(legend))
        if value
    )
    validated = []
    for item in container[key]:
        if not isinstance(item, dict):
            raise ValueError("Each interpretation must be an object")
        validated.append(
            _validate_interpretation_fields(item, evidence_text)
        )
    container[key] = validated
    container["review_status"] = "needs_review"
    container["verified"] = False
    container["approved"] = False
    return container


def interpret_unresolved_lines(lines, legend_vocab, llm_client):
    """Use the injected LLM only for unresolved, non-deterministic lines."""
    if not callable(llm_client):
        raise TypeError("llm_client must be callable")
    if not isinstance(lines, (list, tuple)):
        raise TypeError("lines must be a list or tuple")

    unresolved = [
        (index, line)
        for index, line in enumerate(lines)
        if not _is_deterministically_resolved(line)
    ]
    skipped = [
        {"line_index": index, "evidence": line}
        for index, line in enumerate(lines)
        if _is_deterministically_resolved(line)
    ]
    if not unresolved:
        return {
            "interpretations": [],
            "unresolved_lines": [],
            "skipped_lines": skipped,
            "review_status": "needs_review",
            "verified": False,
            "approved": False,
        }

    unresolved_evidence = [line for _, line in unresolved]
    system_prompt = """
You are a conservative fallback interpreter for unresolved timetable lines.
Use only the supplied OCR line evidence and legend vocabulary. Return JSON
only with an "interpretations" array. Each item must include "line_index"
copied from its input line, "subject", "day", "start_time", "end_time",
"teacher_name", "teacher_initials", "room", and "uncertainties". Use null for any field not clearly supported;
never infer a teacher from a course, expand initials, invent a room, or mark
an item verified or approved. Preserve the source line text as "original_text".
"""
    user_prompt = json.dumps(
        {
            "unresolved_lines": [
                {
                    "line_index": index,
                    "evidence": line,
                }
                for index, line in unresolved
            ],
            "legend_vocab": legend_vocab,
        },
        ensure_ascii=False,
    )
    response = _extract_json(llm_client(system_prompt, user_prompt))
    if isinstance(response, dict):
        raw_interpretations = response.get("interpretations")
    else:
        raw_interpretations = None
    if not isinstance(raw_interpretations, list):
        raise ValueError("LLM response must contain an interpretations list")

    valid_indices = {index for index, _ in unresolved}
    by_index = {}
    unmatched = []
    for item in raw_interpretations:
        if not isinstance(item, dict):
            unmatched.append({
                "reason": "llm_interpretation_not_an_object",
                "evidence": item,
            })
            continue
        try:
            line_index = int(item.get("line_index"))
        except (TypeError, ValueError):
            line_index = None
        if line_index not in valid_indices or line_index in by_index:
            unmatched.append({
                "reason": "invalid_or_duplicate_line_index",
                "evidence": item,
            })
            continue
        by_index[line_index] = item

    validated = []
    unresolved_lines = []
    for line_index, source_line in unresolved:
        llm_item = by_index.get(line_index)
        original_text = _unresolved_line_text(source_line)
        if llm_item is None:
            validated.append({
                "line_index": line_index,
                "original_text": original_text,
                "evidence": source_line,
                "subject": None,
                "teacher_name": None,
                "teacher_initials": None,
                "room": None,
                "uncertainties": ["No interpretation returned for line"],
                "unresolved_fields": {
                    "interpretation": {
                        "status": "unresolved",
                        "reason": "LLM omitted this line",
                    },
                },
                "review_status": "needs_review",
                "verified": False,
                "approved": False,
            })
            unresolved_lines.append({
                "line_index": line_index,
                "evidence": source_line,
                "reason": "llm_omitted_line",
            })
            continue

        record = dict(llm_item)
        record["line_index"] = line_index
        record["original_text"] = original_text or record.get("original_text", "")
        record["evidence"] = source_line
        evidence_text = " ".join(
            (
                _line_evidence_text([source_line]),
                _legend_evidence_text(legend_vocab),
            )
        )
        checked = _validate_interpretation_fields(record, evidence_text)
        if not original_text:
            checked["uncertainties"] = sorted(set(
                checked["uncertainties"] + ["Original line text unavailable"]
            ))
        validated.append(checked)
        if checked["unresolved_fields"] or checked["uncertainties"]:
            unresolved_lines.append({
                "line_index": line_index,
                "evidence": source_line,
                "reason": "interpretation_incomplete_or_unsupported",
            })

    validated_result = {
        "interpretations": validated,
        "unresolved_lines": unresolved_lines + unmatched,
        "skipped_lines": skipped,
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
    }
    return validated_result
