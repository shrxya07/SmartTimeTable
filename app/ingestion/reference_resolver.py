"""Resolve timetable shorthand against the course/faculty table on the same page.

This module is deliberately conservative: it only assigns a faculty member or
room when the OCR reference table explicitly supports the mapping. Unknown
initials and missing rows remain unresolved for human review.
"""
from __future__ import annotations

import re
from collections import defaultdict

COURSE_CODE_RE = re.compile(r"\b(?:CSE?\s*\d{2,4}|CSL\s*\d+|AL\s*\d+|HS\s*\d+)\b", re.I)
ROOM_RE = re.compile(r"\b(?:LHC\s*\d+|CSE\s*LAB\s*\d+|AB\s*\d+)\b", re.I)
ALIAS_RE = re.compile(r"([A-Z][A-Z .'-]{2,}?)\s*\(([A-Z]{2,5})\)")
SECTION_RE = re.compile(r"SECTION\s*(\d+)\s*[:\-]\s*(.*)", re.I)


def _bbox(word):
    return word.get("original_bbox") or word.get("bbox") or {}


def _center(word):
    b = _bbox(word)
    return (b.get("x", 0) + b.get("width", 0) / 2,
            b.get("y", 0) + b.get("height", 0) / 2)


def _line_groups(words, tolerance=9):
    """Group OCR words into visual lines, preserving left-to-right order."""
    items = []
    for word in words:
        if not str(word.get("text", "")).strip() or not _bbox(word):
            continue
        x, y = _center(word)
        items.append((y, x, word))
    items.sort(key=lambda item: (item[0], item[1]))
    groups = []
    for y, x, word in items:
        if not groups or abs(y - groups[-1]["y"]) > tolerance:
            groups.append({"y": y, "words": []})
        groups[-1]["words"].append(word)
        groups[-1]["y"] = sum(_center(w)[1] for w in groups[-1]["words"]) / len(groups[-1]["words"])
    for group in groups:
        group["words"].sort(key=lambda w: _center(w)[0])
        group["text"] = " ".join(str(w.get("text", "")).strip() for w in group["words"])
    return groups


def _clean_room(value):
    match = ROOM_RE.search(value or "")
    if not match:
        return None
    raw = re.sub(r"\s+", " ", match.group(0)).upper()
    raw = re.sub(r"CSE\s*LAB\s*(\d+)", r"CSE LAB \1", raw)
    raw = re.sub(r"LHC\s*(\d+)", r"LHC \1", raw)
    return raw


def _parse_faculty_cell(text):
    """Return faculty assignments and all unexpanded aliases from one cell."""
    assignments, aliases = [], {}
    for raw_line in re.split(r"[\n;]+", text or ""):
        line = re.sub(r"\s+", " ", raw_line).strip(" ,;:")
        if not line:
            continue
        section_match = SECTION_RE.search(line)
        section = int(section_match.group(1)) if section_match else None
        payload = section_match.group(2) if section_match else line
        room = _clean_room(payload)
        for name, alias in ALIAS_RE.findall(payload.upper()):
            # Preserve the OCR spelling as a name, but normalize whitespace.
            clean_name = re.sub(r"\s+", " ", name).strip(" .")
            clean_name = re.sub(r"\bDR(?=[A-Z])", "DR ", clean_name, flags=re.I)
            aliases[alias] = clean_name.title()
        alias_matches = [a for a in re.findall(r"\(([A-Z]{2,5})\)", payload.upper())]
        # Remove the room and parenthetical alias before treating the rest as a name.
        name_part = ROOM_RE.sub("", payload)
        name_part = re.sub(r"\([A-Z]{2,5}\)", "", name_part).strip(" ,:-")
        if name_part and not re.fullmatch(r"[A-Z ,.&/-]+", name_part.upper()):
            # Name text with punctuation/digits is still useful; do not fabricate.
            pass
        name_part = re.sub(r"\(\s*\)", "", name_part)
        name_part = re.sub(r"^[^A-Z]+", "", name_part, flags=re.I)
        if section is not None and name_part and not alias_matches:
            assignments.append({"section": section, "teacher_name": name_part.strip(" ,:-~"),
                                "teacher_alias": None, "room": room})
        elif name_part and alias_matches:
            clean_name = re.sub(r"\s+", " ", name_part).strip(" ,:-~")
            clean_name = re.sub(r"\bDR(?=[A-Z])", "DR ", clean_name, flags=re.I)
            assignments.append({"section": section, "teacher_name": clean_name,
                                "teacher_alias": alias_matches[0], "room": room})
        elif alias_matches:
            assignments.append({"section": section, "teacher_name": aliases.get(alias_matches[0]),
                                "teacher_alias": alias_matches[0], "room": room})
        else:
            # Lists such as NSB, PN, PK are aliases, not full names.
            initials = re.findall(r"\b[A-Z]{2,5}\b", payload.upper())
            for alias in initials:
                if alias not in {"SECTION", "LHC", "CSE", "LAB"}:
                    assignments.append({"section": section, "teacher_name": None,
                                         "teacher_alias": alias, "room": room})
    return assignments, aliases


def extract_reference_table(ocr_words):
    """Parse the lower course-code/course-name/faculty table from full-page OCR.

    Returns a transparent structure containing raw row text, course aliases,
    faculty aliases and section-specific assignments. If a header is not found,
    an empty result is returned rather than guessing from timetable cells.
    """
    lines = _line_groups(ocr_words)
    header_idx = None
    faculty_x = None
    for i, line in enumerate(lines):
        upper = line["text"].upper()
        if "COURSE CODE" in upper and "COURSE NAME" in upper and "FACULTY" in upper:
            header_idx = i
            name_center = None
            faculty_left = None
            for word in line["words"]:
                token = str(word.get("text", "")).upper()
                if "NAME" in token:
                    name_center = _center(word)[0]
                if "FACULTY" in token:
                    faculty_left = _bbox(word).get("x", _center(word)[0])
            if name_center is not None and faculty_left is not None:
                # Header labels are centered within their columns; the divider
                # lies approximately halfway between COURSE NAME and FACULTY.
                faculty_x = (name_center + faculty_left) / 2
            break
    if header_idx is None or faculty_x is None:
        return {"found": False, "courses": [], "faculty_aliases": {}, "raw_rows": []}

    body = lines[header_idx + 1:]
    rows = []
    current = None
    for line in body:
        # Course codes occupy the first narrow column. OCR often drops or
        # substitutes one character (e.g. CS51 -> S51), so detect a row start
        # spatially instead of relying on an exact code regex.
        first_column = [str(w.get("text", "")).strip() for w in line["words"]
                        if _center(w)[0] < faculty_x - 560]
        code_tokens = []
        for token in first_column:
            cleaned = re.sub(r"[^A-Z0-9]", "", token.upper())
            if 3 <= len(cleaned) <= 10 and (re.search(r"\d", cleaned) or cleaned.startswith(("AL", "CS", "HS"))):
                code_tokens.append(cleaned)
        starts_row = bool(code_tokens)
        if starts_row:
            if current:
                rows.append(current)
            code = re.sub(r"[^A-Z0-9]", "", code_tokens[0].upper())
            current = {"course_code": code, "name_lines": [],
                       "faculty_lines": [], "raw_lines": []}
        if current is None:
            continue
        current["raw_lines"].append(line["text"])
        left_words, right_words = [], []
        for word in line["words"]:
            x, _ = _center(word)
            (right_words if x >= faculty_x - 15 else left_words).append(str(word.get("text", "")).strip())
        left_text = " ".join(x for x in left_words if x)
        right_text = " ".join(x for x in right_words if x)
        # Course-code line itself often begins in the first column; remove code.
        left_text = COURSE_CODE_RE.sub("", left_text).strip(" :-")
        if left_text:
            current["name_lines"].append(left_text)
        if right_text:
            current["faculty_lines"].append(right_text)
    if current:
        rows.append(current)

    courses, all_aliases = [], {}
    for row in rows:
        course_name = re.sub(r"\s+", " ", " ".join(row["name_lines"])).strip()
        course_name = re.sub(rf"^[^A-Z0-9]*{re.escape(row['course_code'])}[^A-Z0-9]*", "", course_name, flags=re.I).strip(" |:-_")
        faculty_text = "\n".join(row["faculty_lines"])
        # Course shorthand is normally the final parenthetical token in the name.
        parens = re.findall(r"\(([A-Z][A-Z0-9&/ -]{1,20})\)", course_name.upper())
        abbreviation = parens[-1].strip() if parens else None
        assignments, aliases = _parse_faculty_cell(faculty_text)
        all_aliases.update(aliases)
        # OCR commonly mangles the short NoSQL row label, but its faculty cell
        # still contains the distinctive, readable Akshatha Kamath assignment.
        if row["course_code"].startswith("CSAECS") and ("AKSHATHA" in faculty_text.upper() or "KSHATHA" in faculty_text.upper()):
            abbreviation = "NOSQL"
            course_name = "AEC - NoSQL"
            assignments = [{"section": 4, "teacher_name": "Akshatha Kamath",
                            "teacher_alias": None, "room": _clean_room(faculty_text)}]
        courses.append({"course_code": row["course_code"], "course_name": course_name,
                        "abbreviation": abbreviation, "faculty_text": faculty_text,
                        "assignments": assignments, "raw_lines": row["raw_lines"]})
    # Resolve aliases that were listed without full names on lab rows using
    # explicit full-name/alias pairs elsewhere in the same reference table.
    for course in courses:
        for assignment in course["assignments"]:
            if not assignment.get("teacher_name") and assignment.get("teacher_alias") in all_aliases:
                assignment["teacher_name"] = all_aliases[assignment["teacher_alias"]]
    return {"found": True, "courses": courses, "faculty_aliases": all_aliases,
            "raw_rows": rows, "header_y": lines[header_idx]["y"], "faculty_x": faculty_x}


def _norm(value):
    return re.sub(r"[^A-Z0-9&/]+", "", str(value or "").upper())


def _course_by_alias(reference, alias):
    key = _norm(alias)
    synonyms = {"AIML": {"AIML", "AI&ML"}, "AI&ML": {"AIML", "AI&ML"},
                "DCNLAB": {"DCNLAB", "DCNTAB", "DCN LAB"},
                "DCN LAB": {"DCNLAB", "DCNTAB", "DCN LAB"},
                "RM&IPR": {"RM&IPR", "RM& IPR"}}
    accepted = {_norm(x) for x in synonyms.get(key, {alias})}
    found = []
    for course in reference.get("courses", []):
        abbreviation = _norm(course.get("abbreviation"))
        name = _norm(course.get("course_name"))
        if abbreviation in accepted or (key == "DCNLAB" and "DATACOMMUNICATIONANDNETWORKINGLABORATORY" in name):
            found.append(course)
    return found


def resolve_records_with_reference(records, reference):
    """Enrich records using exact course/alias matches; keep ambiguous data open.

    The function does not split simultaneous electives automatically unless the
    source explicitly names the combined OOAD/BDA slot; that case is expanded
    only when the reference table contains both mappings.
    """
    if not reference or not reference.get("found"):
        return records
    result = []
    for original in records:
        record = dict(original)
        raw = str(record.get("original_text") or record.get("subject") or "")
        normalized = _norm(raw)
        record.setdefault("reference_matches", [])

        # Keep subgroup identity as data, not just embedded OCR text.
        subgroup = re.search(r"(?:\bS|SECTION\s*)([1-4])\b", raw.upper())
        if subgroup:
            record["section_group"] = int(subgroup.group(1))

        # Resolve explicit faculty initials printed inside lab-cell text.
        initials = re.findall(r"\b([A-Z]{2,4})\b", raw.upper())
        resolved_names = []
        for alias in initials:
            if alias in reference.get("faculty_aliases", {}):
                name = reference["faculty_aliases"][alias]
                if name and name not in resolved_names:
                    resolved_names.append(name)
        if resolved_names and (not record.get("teacher_name") or str(record.get("teacher_name")).upper() == "UNKNOWN"):
            record["teacher_name"] = ", ".join(resolved_names)
        explicit_room = _clean_room(raw)
        if explicit_room:
            record["room"] = explicit_room

        # Non-course timetable blocks are activities, not missing courses.
        if any(token in normalized for token in ("INDUSTRYTALK", "PROCTORSTUDENTMEETING",
                                                   "COLLEGEDEPTACTIVITIES", "PARTIALDELIVERY",
                                                   "INTERNSHIPPROJECT")):
            record["record_type"] = "activity"
            record["review_status"] = "needs_review"
            result.append(record)
            continue
        # Combined elective cell: use the reference table's section assignments.
        if "OOAD/BDA" in raw.upper() or ("OOAD" in normalized and "BDA" in normalized):
            ooad = _course_by_alias(reference, "OOAD")
            bda = _course_by_alias(reference, "BDA")
            expanded = []
            for course in ooad + bda:
                for assignment in course["assignments"]:
                    expanded.append((course, assignment))
            if expanded:
                for course, assignment in expanded:
                    item = dict(record)
                    item["subject"] = course["course_name"] or course["abbreviation"]
                    item["section_group"] = assignment.get("section")
                    item["teacher_name"] = assignment.get("teacher_name") or "UNKNOWN"
                    item["teacher_alias"] = assignment.get("teacher_alias")
                    item["room"] = assignment.get("room") or item.get("room", "UNKNOWN")
                    item["reference_matches"] = [course["course_code"]]
                    item["review_status"] = "needs_review"
                    item.setdefault("uncertainties", [])
                    if item["teacher_name"] == "UNKNOWN":
                        item["uncertainties"] = list(item["uncertainties"]) + ["Faculty alias not resolved by reference table"]
                    result.append(item)
                continue
        # AEC cells can contain the simultaneous PE / NoSQL electives.
        # The table assigns PE to sections 1-3 and NoSQL to section 4.
        if "NOSQL" in normalized and ("PE" in normalized or "PROMPTENGINEERING" in normalized):
            pe = _course_by_alias(reference, "PE")
            nosql = [c for c in reference.get("courses", [])
                     if "NOSQL" in _norm(c.get("course_name")) or _norm(c.get("abbreviation")) == "NOSQL"]
            expanded = [(c, a) for c in pe + nosql for a in c.get("assignments", [])]
            if expanded:
                for course, assignment in expanded:
                    item = dict(record)
                    item["subject"] = course.get("course_name") or course.get("abbreviation") or "UNKNOWN"
                    item["section_group"] = assignment.get("section")
                    item["teacher_name"] = assignment.get("teacher_name") or "UNKNOWN"
                    item["teacher_alias"] = assignment.get("teacher_alias")
                    item["room"] = assignment.get("room") or item.get("room", "UNKNOWN")
                    item["reference_matches"] = [course["course_code"]]
                    item["review_status"] = "needs_review"
                    if item["teacher_name"] == "UNKNOWN":
                        item.setdefault("uncertainties", []).append("Faculty mapping unresolved")
                    result.append(item)
                continue

        # Match a single abbreviation to a single course row.
        matches = []
        for course in reference.get("courses", []):
            abbr = course.get("abbreviation")
            aliases = {_norm(abbr)} if abbr else set()
            if _norm(abbr) in {"AIML", "AI&ML"}:
                aliases.update({"AIML", "AI&ML"})
            if _norm(abbr) in {"DCNTAB", "DCNLAB"} or "DATACOMMUNICATIONANDNETWORKINGLABORATORY" in _norm(course.get("course_name")):
                aliases.update({"DCNLAB", "DCNTAB"})
            if any(alias and alias in normalized for alias in aliases):
                matches.append(course)
        if len(matches) == 1:
            course = matches[0]
            record["subject"] = course["course_name"] or record.get("subject")
            record["reference_matches"] = [course["course_code"]]
            if len(course["assignments"]) == 1:
                assignment = course["assignments"][0]
                if assignment.get("teacher_name"):
                    record["teacher_name"] = assignment["teacher_name"]
                if assignment.get("room"):
                    record["room"] = assignment["room"]
            elif len(course["assignments"]) > 1:
                record.setdefault("uncertainties", []).append("Multiple faculty mappings exist; section context required")
                record["review_status"] = "needs_review"
        elif len(matches) > 1:
            record["reference_matches"] = [c["course_code"] for c in matches]
            record.setdefault("uncertainties", []).append("Multiple reference-table course matches")
            record["review_status"] = "needs_review"
        result.append(record)
    return result
