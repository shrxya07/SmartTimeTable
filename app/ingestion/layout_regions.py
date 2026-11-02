"""Locate timetable page regions from detected geometry and OCR evidence."""

import re
from statistics import median


_COLUMN_ALIASES = {
    "course_code": {
        "COURSECODE", "COURSEID", "COURSENO", "SUBJECTCODE",
        "SUBJECTID", "PAPERNO",
    },
    "course_name": {
        "COURSENAME", "COURSETITLE", "SUBJECTNAME", "SUBJECTTITLE",
        "PAPERTITLE",
    },
    "faculty": {"FACULTY", "FACULTYNAME", "TEACHER", "INSTRUCTOR", "STAFF"},
}
_CODE_RE = re.compile(r"(?=.*[A-Z])(?=.*\d)[A-Z0-9/&-]{2,16}\Z", re.I)


def _bbox(word):
    bbox = word.get("original_bbox") or word.get("bbox") or {}
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        width = float(bbox["width"])
        height = float(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _word_center(word):
    bbox = _bbox(word)
    if bbox is None:
        return None
    return (
        bbox["x"] + bbox["width"] / 2,
        bbox["y"] + bbox["height"] / 2,
    )


def _text_norm(text):
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _union_bbox(words):
    boxes = [_bbox(word) for word in words]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["width"] for box in boxes)
    bottom = max(box["y"] + box["height"] for box in boxes)
    return {
        "x": round(left),
        "y": round(top),
        "width": round(right - left),
        "height": round(bottom - top),
    }


def _visual_lines(words):
    items = []
    heights = []
    for word in words:
        center = _word_center(word)
        bbox = _bbox(word)
        text = str(word.get("text", "")).strip()
        if center is None or bbox is None or not text:
            continue
        items.append((center[1], center[0], word))
        heights.append(bbox["height"])
    if not items:
        return []

    tolerance = max(1.0, median(heights) * 0.72)
    items.sort(key=lambda item: (item[0], item[1]))
    lines = []
    for center_y, _, word in items:
        matching = next(
            (
                line for line in reversed(lines)
                if center_y - line["center_y"] <= tolerance
            ),
            None,
        )
        if matching is None:
            lines.append({"center_y": center_y, "words": [word]})
        else:
            matching["words"].append(word)
            matching["center_y"] = sum(
                _word_center(item)[1] for item in matching["words"]
            ) / len(matching["words"])

    for line in lines:
        line["words"].sort(key=lambda item: _word_center(item)[0])
        line["text"] = " ".join(
            str(item.get("text", "")).strip() for item in line["words"]
        )
        line["bbox"] = _union_bbox(line["words"])
    return lines


def _line_anchors(line):
    words = line["words"]
    tokens = [_text_norm(word.get("text", "")) for word in words]
    matches = {}

    for index, token in enumerate(tokens):
        if not token:
            continue
        for key, aliases in _COLUMN_ALIASES.items():
            if token in aliases:
                matches.setdefault(key, []).append([words[index]])

    for index in range(len(words) - 1):
        left, right = tokens[index:index + 2]
        if not left or not right:
            continue
        left_center = _word_center(words[index])
        right_center = _word_center(words[index + 1])
        tolerance = max(
            _bbox(words[index])["height"],
            _bbox(words[index + 1])["height"],
        ) * 1.5
        if abs(left_center[1] - right_center[1]) > tolerance:
            continue
        phrase = left + right
        for key, aliases in _COLUMN_ALIASES.items():
            if phrase in aliases:
                matches.setdefault(key, []).append(
                    [words[index], words[index + 1]]
                )
    return matches


def _find_legend_anchors(words, grid_bottom):
    eligible = [
        word for word in words
        if _word_center(word) is not None
        and _word_center(word)[1] >= grid_bottom
    ]
    lines = _visual_lines(eligible)
    candidates = []
    for line in lines:
        anchors = {}
        for key, groups in _line_anchors(line).items():
            group = min(
                groups,
                key=lambda items: _union_bbox(items)["width"],
            )
            anchors[key] = {
                "bbox": _union_bbox(group),
                "text": " ".join(
                    str(item.get("text", "")).strip() for item in group
                ),
                "words": group,
            }
        if anchors:
            candidates.append({
                "center_y": line["center_y"],
                "anchors": anchors,
            })

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            len(item["anchors"]),
            "course_code" in item["anchors"],
            -item["center_y"],
        ),
    )


def _column_ranges(anchors, left, right):
    centers = sorted(
        (
            anchor["bbox"]["x"] + anchor["bbox"]["width"] / 2,
            key,
        )
        for key, anchor in anchors.items()
    )
    ranges = {}
    for index, (center, key) in enumerate(centers):
        start = (
            (centers[index - 1][0] + center) / 2
            if index
            else left
        )
        end = (
            (center + centers[index + 1][0]) / 2
            if index + 1 < len(centers)
            else right
        )
        ranges[key] = (start, end)
    return ranges


def _course_code_lines(lines, anchors, region_left, region_right, header_y):
    if "course_code" not in anchors:
        return []
    ranges = _column_ranges(anchors, region_left, region_right)
    left, right = ranges["course_code"]
    results = []
    for line in lines:
        if line["center_y"] <= header_y:
            continue
        code_words = [
            word for word in line["words"]
            if (center := _word_center(word)) is not None
            and left <= center[0] < right
            and _CODE_RE.fullmatch(_text_norm(word.get("text", "")))
        ]
        if code_words:
            results.append((line, code_words))
    return results


def _extract_header_metadata(words, schedule_top):
    header_words = [
        word for word in words
        if _word_center(word) is not None
        and _word_center(word)[1] < schedule_top
    ]
    lines = _visual_lines(header_words)
    text = "\n".join(line["text"] for line in lines)
    compact = re.sub(r"\s+", " ", text).strip()

    def field(pattern):
        match = re.search(pattern, compact, flags=re.I)
        return match.group(1).strip(" :-,") if match else None

    metadata = {
        "semester": field(
            r"\bSEMESTER(?:\s*(?:NO\.?|NUMBER))?\s*[:\-]?\s*([A-Z0-9]+)"
        ),
        "section": field(
            r"\bSECTION(?:\s*(?:NO\.?|NUMBER))?\s*[:\-]?\s*([A-Z0-9]+)"
        ),
        "room": field(
            r"\bROOM(?:\s*(?:NO\.?|NUMBER))?\s*[:\-]?\s*"
            r"(.+?)(?=\b(?:TERM|SEMESTER|SECTION|ACADEMIC\s+YEAR)\b|$)"
        ),
        "term": field(
            r"\bTERM\s*[:\-]?\s*(?:TERM\s*[:\-]?\s*)?"
            r"(.+?)(?=\b(?:SEMESTER|SECTION|ROOM|ACADEMIC\s+YEAR)\b|$)"
        ),
    }
    if metadata["room"]:
        metadata["room"] = re.sub(r"\s+", " ", metadata["room"]).strip()
    if metadata["term"]:
        metadata["term"] = re.sub(r"\s+", " ", metadata["term"]).strip()
    return metadata


def _region_bbox(words, image_width, image_height, minimum_y, padding):
    boxes = [_bbox(word) for word in words]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = max(0, min(box["x"] for box in boxes) - padding)
    top = max(minimum_y, min(box["y"] for box in boxes) - padding)
    right = min(
        image_width,
        max(box["x"] + box["width"] for box in boxes) + padding,
    )
    bottom = min(
        image_height,
        max(box["y"] + box["height"] for box in boxes) + padding,
    )
    if right <= left or bottom <= top:
        return None
    return {
        "x": round(left),
        "y": round(top),
        "width": round(right - left),
        "height": round(bottom - top),
    }


def locate_regions(ocr_evidence, grid_geometry):
    """Return header, grid, and legend bounds from OCR and grid geometry."""
    ocr_evidence = ocr_evidence or {}
    grid_geometry = grid_geometry or {}
    words = ocr_evidence.get("words", [])
    image_width = grid_geometry.get("width")
    image_height = grid_geometry.get("height")
    x_lines = grid_geometry.get("x_lines") or []
    y_lines = grid_geometry.get("y_lines") or []
    schedule_top = grid_geometry.get("schedule_top")
    schedule_bottom = grid_geometry.get("schedule_bottom")

    if schedule_top is None and y_lines:
        schedule_top = min(y_lines)
    if schedule_bottom is None and y_lines:
        schedule_bottom = max(y_lines)

    valid_geometry = (
        image_width and image_height and x_lines and y_lines
        and schedule_top is not None and schedule_bottom is not None
        and schedule_bottom > schedule_top
    )
    if not valid_geometry:
        return {
            "header": {
                "bbox": None,
                "metadata": {},
                "status": "unresolved",
                "reason": "grid_geometry_incomplete",
            },
            "grid": {
                "bbox": None,
                "status": "unresolved",
                "reason": "grid_geometry_incomplete",
            },
            "legend": {
                "bbox": None,
                "anchors": {},
                "status": "unresolved",
                "reason": "grid_geometry_incomplete",
            },
        }

    header = {
        "bbox": {
            "x": 0,
            "y": 0,
            "width": int(image_width),
            "height": int(schedule_top),
        },
        "metadata": _extract_header_metadata(words, schedule_top),
        "status": "located",
    }
    grid = {
        "bbox": {
            "x": round(min(x_lines)),
            "y": round(schedule_top),
            "width": round(max(x_lines) - min(x_lines)),
            "height": round(schedule_bottom - schedule_top),
        },
        "status": "located",
        "source": "detected_grid_geometry",
    }

    anchors = _find_legend_anchors(words, schedule_bottom)
    if anchors is None:
        return {
            "header": header,
            "grid": grid,
            "legend": {
                "bbox": None,
                "anchors": {},
                "status": "unresolved",
                "reason": "legend_heading_anchors_not_found_below_grid",
            },
        }

    anchor_items = list(anchors["anchors"].values())
    anchor_words = [
        word
        for anchor in anchor_items
        for word in anchor["words"]
    ]
    anchor_bbox = _union_bbox(anchor_words)
    median_height = median(
        _bbox(word)["height"]
        for word in anchor_words
        if _bbox(word) is not None
    )
    padding = max(1, round(median_height))
    body_lines = [
        line for line in _visual_lines(words)
        if line["center_y"] >= anchor_bbox["y"]
    ]
    code_lines = _course_code_lines(
        body_lines,
        anchors["anchors"],
        0,
        image_width,
        anchors["center_y"],
    )
    reasons = []
    if "course_code" not in anchors["anchors"]:
        reasons.append("course_code_heading_not_found")
    if "course_name" not in anchors["anchors"]:
        reasons.append("course_name_heading_not_found")
    if "faculty" not in anchors["anchors"]:
        reasons.append("faculty_heading_not_found")

    if not code_lines:
        reasons.append("course_rows_not_found")
        region_words = [
            word for word in words
            if _word_center(word) is not None
            and _word_center(word)[1] >= anchor_bbox["y"]
        ]
    else:
        code_y = max(line["center_y"] for line, _ in code_lines)
        candidate_lines = [
            line for line in body_lines if line["center_y"] <= code_y
        ]
        spacings = [
            right["center_y"] - left["center_y"]
            for left, right in zip(candidate_lines, candidate_lines[1:])
            if right["center_y"] > left["center_y"]
        ]
        typical_spacing = median(spacings) if spacings else median_height * 2
        end_gap = max(median_height * 3, typical_spacing * 2.5)
        last_line = max(
            (line for line, _ in code_lines),
            key=lambda line: line["center_y"],
        )
        last_index = body_lines.index(last_line)
        end_index = last_index
        for index in range(last_index + 1, len(body_lines)):
            if (
                body_lines[index]["center_y"]
                - body_lines[index - 1]["center_y"]
                > end_gap
            ):
                break
            end_index = index
        region_words = [
            word
            for line in body_lines[:end_index + 1]
            for word in line["words"]
        ]

    legend_bbox = _region_bbox(
        region_words + anchor_words,
        image_width,
        image_height,
        minimum_y=schedule_bottom,
        padding=padding,
    )
    resolved = not reasons and bool(code_lines) and legend_bbox is not None
    return {
        "header": header,
        "grid": grid,
        "legend": {
            "bbox": legend_bbox,
            "anchors": {
                key: {
                    "text": anchor["text"],
                    "bbox": anchor["bbox"],
                }
                for key, anchor in anchors["anchors"].items()
            },
            "course_rows_detected": len(code_lines),
            "status": "located" if resolved else "unresolved",
            "reason": None if resolved else ", ".join(reasons),
            "review_status": "needs_review",
        },
    }
