"""Extract reviewable course offerings from timetable legend tables."""

from dataclasses import dataclass
import json
import re
import tempfile
from pathlib import Path
from statistics import median

import cv2
import numpy as np

from app.ingestion.dynamic_grid import (
    inspect_local_boundaries,
    inspect_morphological_boundaries,
)
from app.ingestion.grid_extractor import (
    _group_positions,
    detect_timetable_grid,
)
from app.ingestion.layout_regions import (
    _bbox,
    _column_ranges,
    _extract_header_metadata,
    _find_legend_anchors,
    _text_norm,
    _union_bbox,
    _visual_lines,
)
from app.ingestion.timetable_resolver import CourseOffering


_CODE_RE = re.compile(r"(?=.*[A-Z])(?=.*\d)[A-Z0-9/&-]{2,16}\Z", re.I)
_SECTION_RE = re.compile(
    r"\bSECTION\s*([A-Z0-9]+)\s*[:\-]?\s*",
    re.I,
)
_ROOM_RE = re.compile(
    r"\b(?:CSE\s*LAB\s*\d+|LHC\s*\d+|AB\s*\d+"
    r"(?:\s*(?:&|AND)\s*AB\s*\d+)?)\b",
    re.I,
)
_INITIAL_RE = re.compile(r"\(([A-Z][A-Z .'-]{0,5})\)", re.I)
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)*")
_LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class LegendCourseOffering(CourseOffering):
    course_code: str = None


def _bbox_arg(value):
    if isinstance(value, dict) and isinstance(value.get("bbox"), dict):
        value = value["bbox"]
    if not isinstance(value, dict):
        return None
    try:
        x = int(value["x"])
        y = int(value["y"])
        width = int(value["width"])
        height = int(value["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _word_confidence(word):
    try:
        return float(word.get("confidence", 1.0))
    except (TypeError, ValueError):
        return 0.0


def _inside_region(word, region):
    box = _bbox(word)
    if box is None:
        return False
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    return (
        region["x"] <= center_x <= region["x"] + region["width"]
        and region["y"] <= center_y <= region["y"] + region["height"]
    )


def _detect_table_lines(image_path, region, words):
    with tempfile.TemporaryDirectory(prefix="smart-timetable-lines-") as temp:
        mask_path = Path(temp) / "table-lines.png"
        line_detection = detect_timetable_grid(
            image_path,
            output_path=mask_path,
        )
        mask = cv2.imread(
            line_detection["debug_image"],
            cv2.IMREAD_GRAYSCALE,
        )
    if mask is None:
        raise OSError("Could not read the detected timetable line mask.")

    left = max(0, region["x"])
    top = max(0, region["y"])
    right = min(mask.shape[1], left + region["width"])
    bottom = min(mask.shape[0], top + region["height"])
    crop = mask[top:bottom, left:right]
    if crop.size == 0:
        raise ValueError("Legend bounding box is outside the source image.")

    heights = [
        _bbox(word)["height"]
        for word in words
        if _bbox(word) is not None
    ]
    tolerance = max(1, round(median(heights) * 0.65)) if heights else 2

    horizontal_profile = np.count_nonzero(crop, axis=1)
    vertical_profile = np.count_nonzero(crop, axis=0)

    def candidates(profile, offset):
        peak = int(profile.max()) if profile.size else 0
        if peak == 0:
            return []
        positions = np.where(profile >= peak * 0.35)[0] + offset
        return _group_positions(positions, tolerance=tolerance)

    x_lines = sorted(set(
        [left, right]
        + candidates(vertical_profile, left)
    ))
    y_lines = sorted(set(
        [top, bottom]
        + candidates(horizontal_profile, top)
    ))
    if len(x_lines) < 2 or len(y_lines) < 2:
        raise ValueError("Could not derive candidate legend table boundaries.")

    geometry = {
        "x_lines": x_lines,
        "y_lines": y_lines,
    }
    local = inspect_local_boundaries(image_path, geometry=geometry)
    morphological = inspect_morphological_boundaries(
        image_path,
        geometry=geometry,
    )
    return {
        "x_lines": x_lines,
        "y_lines": y_lines,
        "local": local,
        "morphological": morphological,
    }


def _line_strengths(line_evidence, axis):
    key = "x" if axis == "vertical" else "y"
    values = {}
    for source in ("local", "morphological"):
        for item in line_evidence[source][axis]:
            values[item[key]] = max(
                values.get(item[key], 0.0),
                item["coverage"],
            )
    return values


def _column_ranges_from_lines(anchors, region, line_evidence):
    fallback = _column_ranges(
        anchors,
        region["x"],
        region["x"] + region["width"],
    )
    centers = sorted(
        (
            anchor["bbox"]["x"] + anchor["bbox"]["width"] / 2,
            key,
        )
        for key, anchor in anchors.items()
    )
    line_strength = _line_strengths(line_evidence, "vertical")
    boundaries = []
    for left, right in zip(centers, centers[1:]):
        midpoint = (left[0] + right[0]) / 2
        candidates = [
            x for x in line_evidence["x_lines"]
            if left[0] < x < right[0] and line_strength.get(x, 0) >= 0.2
        ]
        boundary = (
            min(candidates, key=lambda x: abs(x - midpoint))
            if candidates else midpoint
        )
        boundaries.append(boundary)

    ranges = {}
    for index, (center, key) in enumerate(centers):
        start = (
            boundaries[index - 1]
            if index else fallback[key][0]
        )
        end = (
            boundaries[index]
            if index < len(boundaries) else fallback[key][1]
        )
        ranges[key] = (start, end)
    return ranges


def _field_words(lines, row_start, row_end, column_range):
    left, right = column_range
    selected = []
    for line in lines:
        if not row_start <= line["center_y"] < row_end:
            continue
        selected.extend(
            word for word in line["words"]
            if (center := _center(word)) is not None
            and left <= center[0] < right
        )
    return selected


def _center(word):
    bbox = _bbox(word)
    if bbox is None:
        return None
    return (
        bbox["x"] + bbox["width"] / 2,
        bbox["y"] + bbox["height"] / 2,
    )


def _joined_text(words):
    return " ".join(
        str(word.get("text", "")).strip()
        for word in sorted(
            words,
            key=lambda item: (
                _center(item)[1],
                _center(item)[0],
            ),
        )
        if str(word.get("text", "")).strip()
    ).strip()


def _normalize_room(value):
    match = _ROOM_RE.search(value or "")
    if not match:
        return None
    normalized = re.sub(r"\s+", " ", match.group(0)).upper()
    normalized = re.sub(r"CSE\s*LAB\s*(\d+)", r"CSE LAB \1", normalized)
    normalized = re.sub(r"LHC\s*(\d+)", r"LHC \1", normalized)
    normalized = re.sub(r"AB\s*(\d+)", r"AB \1", normalized)
    return normalized


def _parenthetical_abbrev(course_name):
    matches = re.findall(r"\(([^()]*)\)", course_name or "")
    for value in reversed(matches):
        value = value.strip()
        if re.fullmatch(
            r"[A-Za-z][A-Za-z0-9/&-]*(?:\s+(?:LAB|TUT(?:ORIAL)?))?",
            value,
            flags=re.I,
        ):
            return value
    return None


def _course_kind(course_name):
    upper = str(course_name or "").upper()
    if re.search(r"\b(?:LAB|LABORATORY)\b", upper):
        return "laboratory"
    if re.search(r"\b(?:TUTORIAL|TUT)\b", upper):
        return "tutorial"
    if re.search(r"\bELECTIVE\b", upper):
        return "elective"
    return None


def _source_bbox(words):
    return _union_bbox(words)


def _matching_words(words, values):
    tokens = {
        token
        for value in values
        for token in re.findall(r"[A-Z0-9]+", str(value or "").upper())
    }
    return [
        word for word in words
        if tokens.intersection(
            re.findall(r"[A-Z0-9]+", str(word.get("text", "")).upper())
        )
    ]


def _faculty_assignments(faculty_words, default_section=None, default_room=None):
    if not faculty_words:
        return [{
            "section": default_section,
            "name": None,
            "initials": (),
            "room": default_room,
            "words": [],
            "unresolved": "faculty_cell_empty",
        }]

    lines = _visual_lines(faculty_words)
    chunks = []
    for line in lines:
        raw = line["text"].strip()
        sectioned = bool(_SECTION_RE.search(raw))
        room_only = (
            bool(_ROOM_RE.search(raw))
            and not _INITIAL_RE.search(raw)
            and not _NAME_RE.search(_ROOM_RE.sub("", raw).strip(" ,;:-"))
        )
        initials_only = bool(
            re.fullmatch(
                r"(?:[A-Za-z]{1,5}\s*[,/&]\s*)*[A-Za-z]{1,5}",
                _ROOM_RE.sub("", _SECTION_RE.sub("", raw)).strip(" ,;:-"),
            )
        )
        has_initials = bool(_INITIAL_RE.search(raw))
        if room_only and chunks:
            chunks[-1]["words"].extend(line["words"])
            chunks[-1]["text"] += " " + raw
            continue
        if sectioned or not chunks:
            chunks.append({
                "text": raw,
                "words": list(line["words"]),
                "has_initials": has_initials,
                "initials_only": initials_only,
            })
            continue
        previous = chunks[-1]
        if initials_only and previous["initials_only"]:
            previous["text"] += " " + raw
            previous["words"].extend(line["words"])
        elif not previous["has_initials"] and not previous["initials_only"]:
            previous["text"] += " " + raw
            previous["words"].extend(line["words"])
            previous["has_initials"] = has_initials
            previous["initials_only"] = initials_only
        else:
            chunks.append({
                "text": raw,
                "words": list(line["words"]),
                "has_initials": has_initials,
                "initials_only": initials_only,
            })

    assignments = []
    for chunk in chunks:
        raw = chunk["text"].strip()
        section_match = _SECTION_RE.search(raw)
        section = section_match.group(1) if section_match else default_section
        payload = (
            raw[section_match.end():].strip(" :-")
            if section_match else raw
        )
        room = _normalize_room(payload) or default_room
        payload_without_room = _ROOM_RE.sub("", payload).strip(" ,;:-")
        initials_matches = list(_INITIAL_RE.finditer(payload_without_room))
        if initials_matches:
            previous_end = 0
            for match in initials_matches:
                name = payload_without_room[
                    previous_end:match.start()
                ].strip(" ,;:-")
                initials = tuple(
                    token.upper()
                    for token in re.findall(
                        r"[A-Za-z]+",
                        match.group(1),
                    )
                )
                previous_end = match.end()
                if not initials:
                    continue
                assignments.append({
                    "section": section,
                    "name": name or None,
                    "initials": initials,
                    "room": room,
                    "words": chunk["words"],
                    "name_words": _matching_words(
                        chunk["words"], [name]
                    ),
                    "initial_words": _matching_words(
                        chunk["words"], initials
                    ),
                    "unresolved": (
                        "initials_without_faculty_name" if not name else None
                    ),
                })
            continue

        payload_without_room = payload_without_room.strip(" ,;:-")
        tokens = [
            token.strip(" ,;:-")
            for token in re.split(r"[,/&]+", payload_without_room)
            if token.strip(" ,;:-")
        ]
        if tokens and all(re.fullmatch(r"[A-Za-z]{1,5}", token) for token in tokens):
            assignments.append({
                "section": section,
                "name": None,
                "initials": tuple(token.upper() for token in tokens),
                "room": room,
                "words": chunk["words"],
                "name_words": [],
                "initial_words": _matching_words(
                    chunk["words"], tokens
                ),
                "unresolved": "initials_without_faculty_name",
            })
            continue

        name = re.sub(r"\s+", " ", payload_without_room).strip()
        if name:
            assignments.append({
                "section": section,
                "name": name,
                "initials": (),
                "room": room,
                "words": chunk["words"],
                "name_words": _matching_words(
                    chunk["words"], [name]
                ),
                "initial_words": [],
                "unresolved": None,
            })
    return assignments or [{
        "section": default_section,
        "name": None,
        "initials": (),
        "room": default_room,
        "words": faculty_words,
        "name_words": [],
        "initial_words": [],
        "unresolved": "faculty_cell_unparsed",
    }]


def _course_code_candidates(line, column_range):
    left, right = column_range
    return [
        word for word in line["words"]
        if (center := _center(word)) is not None
        and left <= center[0] < right
        and _CODE_RE.fullmatch(_text_norm(word.get("text", "")))
    ]


def _code_column_boundaries(code_range, header_y, region, line_evidence):
    left, right = code_range
    boundaries = []
    for y in line_evidence["y_lines"]:
        if y <= header_y or y >= region["y"] + region["height"]:
            continue
        scores = []
        for source in ("local", "morphological"):
            for item in line_evidence[source]["horizontal"]:
                if item["y"] != y:
                    continue
                segment_left = item.get(
                    "x1",
                    line_evidence["x_lines"][item["column_index"]],
                )
                segment_right = item.get(
                    "x2",
                    line_evidence["x_lines"][item["column_index"] + 1],
                )
                overlap = max(
                    0,
                    min(right, segment_right) - max(left, segment_left),
                )
                if overlap > 0:
                    scores.append(item["coverage"])
        if scores and max(scores) >= 0.25:
            boundaries.append(y)
    return sorted(set(boundaries))


def _code_cell_rows(lines, code_range, header_y, region, line_evidence):
    top = region["y"]
    bottom = region["y"] + region["height"]
    boundaries = [top] + _code_column_boundaries(
        code_range,
        header_y,
        region,
        line_evidence,
    ) + [bottom]
    rows = []
    for cell_top, cell_bottom in zip(boundaries, boundaries[1:]):
        if cell_bottom <= header_y:
            continue
        code_words = [
            word
            for line in lines
            for word in line["words"]
            if (center := _center(word)) is not None
            and code_range[0] <= center[0] < code_range[1]
            and center[1] > header_y
            and cell_top <= center[1] < cell_bottom
        ]
        code_lines = _visual_lines(code_words)
        cell_rows = []
        for line in code_lines:
            raw_code = _joined_text(line["words"])
            normalized = _text_norm(raw_code)
            if not normalized:
                continue
            if _CODE_RE.fullmatch(normalized):
                cell_rows.append({
                    "center_y": line["center_y"],
                    "code": normalized,
                    "code_words": line["words"],
                    "issue": None,
                })
            elif re.fullmatch(r"[A-Z0-9/&-]{2,16}", normalized):
                cell_rows.append({
                    "center_y": line["center_y"],
                    "code": None,
                    "code_words": line["words"],
                    "issue": "ambiguous_course_code",
                })
        if not cell_rows:
            continue
        cell_rows.sort(key=lambda row: row["center_y"])
        for index, row in enumerate(cell_rows):
            row["y_start"] = (
                cell_top
                if index == 0
                else (cell_rows[index - 1]["center_y"] + row["center_y"]) / 2
            )
            row["y_end"] = (
                cell_bottom
                if index + 1 == len(cell_rows)
                else (row["center_y"] + cell_rows[index + 1]["center_y"]) / 2
            )
            rows.append(row)

    if not rows:
        rows = []
        for line in lines:
            if line["center_y"] <= header_y:
                continue
            code_words = _course_code_candidates(line, code_range)
            if code_words:
                rows.append({
                    "center_y": line["center_y"],
                    "code": _text_norm(_joined_text(code_words)),
                    "code_words": code_words,
                    "issue": None,
                    "y_start": line["center_y"]
                    - median(_bbox(word)["height"] for word in code_words),
                    "y_end": bottom,
                })
    return sorted(rows, key=lambda row: row["center_y"])


def _confidence_issue(words, field):
    if words and min(_word_confidence(word) for word in words) < _LOW_CONFIDENCE:
        return f"low_confidence_{field}"
    return None


def _parse_rows(words, region, anchors, header_metadata, line_evidence):
    missing_columns = {
        key for key in ("course_code", "course_name", "faculty")
        if key not in anchors
    }
    if missing_columns:
        return [], [{
            "reason": "legend_columns_not_found",
            "missing_columns": sorted(missing_columns),
            "source_bbox": region,
        }]

    lines = _visual_lines(words)
    header_y = max(
        anchor["bbox"]["y"] + anchor["bbox"]["height"] / 2
        for anchor in anchors.values()
    )
    column_ranges = _column_ranges_from_lines(
        anchors,
        region,
        line_evidence,
    )
    if "course_code" not in column_ranges:
        return [], [{
            "reason": "course_code_column_not_found",
            "source_bbox": region,
        }]

    rows = _code_cell_rows(
        lines,
        column_ranges["course_code"],
        header_y,
        region,
        line_evidence,
    )
    if not rows:
        return [], [{
            "reason": "course_code_rows_not_found",
            "source_bbox": region,
        }]

    offerings = []
    unresolved = []
    default_section = header_metadata.get("section")
    default_room = header_metadata.get("room")
    for index, row in enumerate(rows):
        code_words = row["code_words"]
        row_start = row["y_start"]
        row_end = (
            rows[index + 1]["y_start"]
            if index + 1 < len(rows)
            else row["y_end"] + 1
        )
        row_lines = [
            line for line in lines
            if row_start <= line["center_y"] < row_end
        ]
        row_words = [
            word for line in row_lines for word in line["words"]
        ]
        code = row["code"]
        code_issue = (
            row["issue"]
            if row["issue"]
            else "ambiguous_course_code"
            if code is None
            else _confidence_issue(code_words, "course_code")
        )

        name_words = _field_words(
            row_lines,
            row_start,
            row_end,
            column_ranges["course_name"],
        )
        course_name = re.sub(r"\s+", " ", _joined_text(name_words)).strip()
        if course_name:
            course_name = course_name.strip(" \t\r\n-:;,")
        if not course_name:
            unresolved.append({
                "reason": "course_name_not_found",
                "course_code": code,
                "raw_text": _joined_text(row_words),
                "source_bbox": _source_bbox(row_words),
            })
            continue
        name_issue = _confidence_issue(name_words, "course_name")
        if name_issue:
            unresolved.append({
                "reason": name_issue,
                "course_code": code,
                "course_name": course_name,
                "source_bbox": _source_bbox(name_words),
            })

        faculty_words = _field_words(
            row_lines,
            row_start,
            row_end,
            column_ranges["faculty"],
        )
        assignments = _faculty_assignments(
            faculty_words,
            default_section=default_section,
            default_room=default_room,
        )
        course_bbox = _source_bbox(row_words)
        if code_issue:
            unresolved.append({
                "reason": code_issue,
                "course_code": code,
                "raw_text": _joined_text(code_words),
                "source_bbox": _source_bbox(code_words),
            })

        for assignment in assignments:
            issues = []
            if assignment["unresolved"]:
                issues.append(assignment["unresolved"])
            initials_issue = _confidence_issue(
                assignment["initial_words"],
                "faculty_initials",
            )
            faculty_name_issue = _confidence_issue(
                assignment["name_words"],
                "faculty_name",
            )
            if initials_issue and assignment["initials"]:
                issues.append(initials_issue)
            if faculty_name_issue and assignment["name"]:
                issues.append(faculty_name_issue)
            if issues:
                unresolved.append({
                    "reason": ", ".join(issues),
                    "course_code": code,
                    "course_name": course_name,
                    "faculty_initials": assignment["initials"],
                    "raw_text": _joined_text(assignment["words"]),
                    "source_bbox": _source_bbox(assignment["words"]),
                })
            offerings.append(LegendCourseOffering(
                course_name=course_name,
                abbrev=_parenthetical_abbrev(course_name),
                kind=_course_kind(course_name),
                section_label=assignment["section"],
                faculty_name=assignment["name"],
                faculty_initials=assignment["initials"],
                room=assignment["room"],
                source_bbox=course_bbox,
                course_code=code,
                review_required=bool(
                    code_issue or name_issue or faculty_name_issue or issues
                ),
                faculty_initials_unverified=bool(initials_issue),
            ))
    return offerings, unresolved


def _contains_evidence(value, words):
    normalized_value = _text_norm(value)
    if not normalized_value:
        return False
    normalized_evidence = _text_norm(
        " ".join(str(word.get("text", "")) for word in words)
    )
    return normalized_value in normalized_evidence


def _has_clear_token(value, words):
    normalized = _text_norm(value)
    matches = [
        word for word in words
        if _text_norm(word.get("text", "")) == normalized
    ]
    return bool(matches) and max(
        _word_confidence(word) for word in matches
    ) >= _LOW_CONFIDENCE


def _validated_llm_offerings(response, words):
    if isinstance(response, str):
        response = json.loads(response)
    if isinstance(response, dict):
        values = response.get("offerings", response.get("courses", []))
    elif isinstance(response, list):
        values = response
    else:
        raise ValueError("Legend LLM response must be an object or list.")
    if not isinstance(values, list):
        raise ValueError("Legend LLM response must contain an offerings list.")

    offerings = []
    rejected = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            rejected.append({
                "index": index,
                "reason": "llm_offering_not_an_object",
            })
            continue
        fields = {
            "course_name": item.get("course_name"),
            "course_code": item.get("course_code"),
            "abbrev": item.get("abbrev", item.get("course_abbrev")),
            "kind": item.get("kind"),
            "section_label": item.get("section_label", item.get("section")),
            "faculty_name": item.get("faculty_name"),
            "room": item.get("room"),
        }
        invalid = [
            key for key, value in fields.items()
            if value is not None and not _contains_evidence(value, words)
        ]
        if (
            fields["course_code"]
            and _contains_evidence(fields["course_code"], words)
            and not _has_clear_token(fields["course_code"], words)
        ):
            invalid.append("course_code_confidence")
        initials = item.get("faculty_initials", item.get("initials", ()))
        if isinstance(initials, str):
            initials = [initials]
        if not isinstance(initials, (list, tuple)):
            invalid.append("faculty_initials")
            initials = []
        invalid.extend(
            "faculty_initials"
            for value in initials
            if (
                not _contains_evidence(value, words)
                or not _has_clear_token(value, words)
            )
        )
        course_name = fields["course_name"]
        if not course_name or not _contains_evidence(course_name, words):
            invalid.append("course_name")
        if invalid:
            rejected.append({
                "index": index,
                "reason": "llm_values_not_grounded_in_legend_ocr",
                "fields": sorted(set(invalid)),
            })
            continue

        grounding_text = " ".join(
            str(value)
            for value in (
                fields["course_code"],
                fields["course_name"],
                fields["abbrev"],
                fields["faculty_name"],
                fields["room"],
                *initials,
            )
            if value
        )
        normalized_grounding = _text_norm(grounding_text)
        source_bbox = _source_bbox([
            word for word in words
            if (
                token := _text_norm(word.get("text", ""))
            ) and token in normalized_grounding
        ])
        offerings.append(LegendCourseOffering(
            course_name=str(course_name).strip(),
            abbrev=fields["abbrev"],
            kind=fields["kind"],
            section_label=fields["section_label"],
            faculty_name=fields["faculty_name"],
            faculty_initials=tuple(str(value).strip().upper() for value in initials),
            room=fields["room"],
            source_bbox=source_bbox,
            course_code=fields["course_code"],
        ))
    return offerings, rejected


def _result_status(offerings, unresolved):
    if unresolved and offerings:
        return "partial"
    if unresolved or not offerings:
        return "unresolved"
    return "resolved"


def extract_legend(image_path, ocr_evidence, legend_bbox, llm_client=None):
    """Extract CourseOffering records from a positioned OCR legend region."""
    region = _bbox_arg(legend_bbox)
    if region is None:
        return {
            "status": "unresolved",
            "review_status": "needs_review",
            "offerings": [],
            "unresolved": [{
                "reason": "legend_bbox_missing_or_invalid",
            }],
            "line_geometry": None,
        }

    words = [
        word for word in (ocr_evidence or {}).get("words", [])
        if isinstance(word, dict) and _inside_region(word, region)
    ]
    if not words:
        return {
            "status": "unresolved",
            "review_status": "needs_review",
            "offerings": [],
            "unresolved": [{
                "reason": "no_positioned_ocr_words_in_legend_bbox",
                "source_bbox": region,
            }],
            "line_geometry": None,
        }

    anchors = _find_legend_anchors(words, region["y"])
    if anchors is None:
        result = {
            "status": "unresolved",
            "review_status": "needs_review",
            "offerings": [],
            "unresolved": [{
                "reason": "legend_column_headings_not_found",
                "source_bbox": region,
            }],
            "line_geometry": None,
        }
    else:
        image_lines = _detect_table_lines(image_path, region, words)
        selected_words = [
            word
            for word in words
            if _center(word) is not None
            and _center(word)[1] >= anchors["center_y"]
        ]
        header_metadata = _extract_header_metadata(
            (ocr_evidence or {}).get("words", []),
            region["y"],
        )
        offerings, unresolved = _parse_rows(
            selected_words,
            region,
            anchors["anchors"],
            header_metadata,
            image_lines,
        )
        result = {
            "status": _result_status(offerings, unresolved),
            "review_status": "needs_review",
            "offerings": offerings,
            "unresolved": unresolved,
            "line_geometry": {
                "x_lines": image_lines["x_lines"],
                "y_lines": image_lines["y_lines"],
            },
        }

    if llm_client is not None and result["status"] != "resolved":
        system_prompt = (
            "Parse the supplied positioned OCR evidence into course offerings. "
            "Do not infer or repair text that is not present in the evidence. "
            "Return JSON with an offerings array."
        )
        user_prompt = json.dumps({
            "legend_bbox": region,
            "words": [
                {
                    "text": word.get("text", ""),
                    "bbox": _bbox(word),
                    "confidence": word.get("confidence"),
                }
                for word in words
            ],
            "required_fields": [
                "course_code", "course_name", "abbrev", "kind",
                "section_label", "faculty_name", "faculty_initials", "room",
            ],
        }, ensure_ascii=False)
        response = llm_client(system_prompt, user_prompt)
        try:
            llm_offerings, rejected = _validated_llm_offerings(
                response,
                words,
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            result["unresolved"].append({
                "reason": "invalid_llm_legend_response",
                "detail": str(error),
            })
        else:
            existing = {
                (
                    _text_norm(item.course_code),
                    _text_norm(item.course_name),
                    _text_norm(item.section_label),
                    _text_norm(item.faculty_name),
                )
                for item in result["offerings"]
            }
            for offering in llm_offerings:
                key = (
                    _text_norm(offering.course_code),
                    _text_norm(offering.course_name),
                    _text_norm(offering.section_label),
                    _text_norm(offering.faculty_name),
                )
                if key not in existing:
                    result["offerings"].append(offering)
                    existing.add(key)
            result["unresolved"].extend(rejected)
            result["status"] = _result_status(
                result["offerings"],
                result["unresolved"],
            )
    return result
