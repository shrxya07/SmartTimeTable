"""Check vision-extracted timetable pages and turn slots into review records.

Nothing here invents data. Every check that fails is recorded on the record
(``unresolved_fields`` / ``uncertainties``) and moves it to the ``check``
review tier. No record is ever marked verified; that is the reviewer's job.
"""

import re

from app.database.db import CHOICE_TYPES, TEACHER_OPTIONAL_TYPES


DAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)
_DAY_ALIASES = {
    alias: day
    for day in DAY_NAMES
    for alias in (day.casefold(), day[:3].casefold())
}
_DAY_ALIASES.update({"tues": "Tuesday", "wed": "Wednesday", "thur": "Thursday",
                     "thurs": "Thursday", "sat": "Saturday"})

ENTRY_TYPES = {
    "lecture": "class",
    "class": "class",
    "theory": "class",
    "lab": "lab",
    "laboratory": "lab",
    "practical": "lab",
    "tutorial": "tutorial",
    "elective": "elective",
    "open_elective": "open_elective",
    "open elective": "open_elective",
    "lateral_entry": "lateral_entry",
    "lateral entry": "lateral_entry",
    "activity": "activity",
    "break": "break",
}
OPTIONAL_TEACHER_ROOM_TYPES = TEACHER_OPTIONAL_TYPES

_LATERAL_ENTRY = re.compile(r"lateral\s*entry", re.IGNORECASE)
_OPEN_ELECTIVE = re.compile(r"\bopen\s*elective\b|^\s*OE\s*$", re.IGNORECASE)


def special_entry_type(*texts):
    """Sessions with no single class teacher, recognised from their text.

    Lateral entry: diploma students taking first-year courses. Open
    elective: every student attends a different elective course.
    """
    for text in texts:
        if text and _LATERAL_ENTRY.search(str(text)):
            return "lateral_entry"
    for text in texts:
        if text and _OPEN_ELECTIVE.search(str(text)):
            return "open_elective"
    return None

_CLOCK = re.compile(
    r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*(a\.?m\.?|p\.?m\.?)?\s*$",
    re.IGNORECASE,
)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
# College timetables write afternoon periods as 1.45, 2.40 ... without PM.
_ASSUME_PM_BEFORE_HOUR = 8


def _clean(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    if not value or value.casefold() in {"unknown", "none", "null", "n/a", "-"}:
        return None
    return value


def _key(value):
    """Comparison key that ignores case, spacing and punctuation."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def normalize_day(value):
    text = _clean(value)
    if text is None:
        return None
    return _DAY_ALIASES.get(text.casefold().rstrip("."))


def normalize_clock(value):
    """Return ``(HH:MM, assumed_pm)`` or ``(None, False)`` if unreadable."""
    text = _clean(value)
    if text is None:
        return None, False
    match = _CLOCK.match(text)
    if not match:
        return None, False

    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or "").replace(".", "").casefold()
    assumed_pm = False
    if meridiem:
        if not 1 <= hour <= 12:
            return None, False
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif 1 <= hour < _ASSUME_PM_BEFORE_HOUR:
        hour += 12
        assumed_pm = True

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, False
    return f"{hour:02d}:{minute:02d}", assumed_pm


def parse_semester(value):
    text = _clean(value)
    if text is None:
        return None
    text = re.sub(r"(?i)\b(sem(ester)?)\b", "", text).strip(" :.-").upper()
    if text.isdigit() and 1 <= int(text) <= 8:
        return int(text)
    return _ROMAN.get(text)


def parse_section(value):
    text = _clean(value)
    if text is None:
        return None
    text = re.sub(r"(?i)\b(sec(tion)?|class|group|division|div)\b", "", text)
    return text.strip(" :.-").upper() or None


def class_name_for(semester, section):
    """'3' + 'A' -> '3A'; without a semester the section/group alone."""
    if not section:
        return None
    return f"{semester}{section}" if semester is not None else section


def _minutes(clock):
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def _legend_index(legend):
    """Known course keys, full faculty names and initials from the course table."""
    courses, names, initials = set(), {}, {}
    for row in legend or []:
        for field in ("course_code", "course_abbrev", "course_name"):
            if _clean(row.get(field)):
                courses.add(_key(row[field]))
        people = list(row.get("faculty") or [])
        people += [
            {"name": item.get("teacher_name"), "initials": item.get("initials")}
            for item in row.get("section_assignments") or []
        ]
        for person in people:
            name = _clean(person.get("name"))
            short = _clean(person.get("initials"))
            if name:
                names[_key(name)] = name
            if name and short:
                initials[_key(short)] = name
    return courses, names, initials


def _ocr_supports(cell_text, ocr_key):
    """True when most words of the cell text also appear in the page OCR."""
    words = [_key(word) for word in re.findall(r"[A-Za-z0-9&]{3,}", cell_text or "")]
    words = [word for word in words if word]
    if not words:
        return True
    found = sum(word in ocr_key for word in words)
    return found / len(words) >= 0.5


def _initials_key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _review_tier(record):
    return (
        "auto"
        if not record["unresolved_fields"] and not record["uncertainties"]
        else "check"
    )


def apply_faculty_directory(records, directory):
    """Fill initials-only teachers from the department's other course tables.

    A name is filled in only when the initials map to exactly one person
    department-wide; ambiguous initials stay unresolved with the candidates
    listed. Each filled name is noted with its source timetable. Returns the
    number of teachers filled in.
    """
    filled = 0
    for record in records:
        notes = record.setdefault("directory_notes", [])
        for teacher in record.get("teachers") or []:
            if teacher.get("teacher_name") or not teacher.get("initials"):
                continue
            match = (directory or {}).get(_initials_key(teacher["initials"]))
            if match and len(match["names"]) == 1:
                teacher["teacher_name"] = match["names"][0]
                sources = ", ".join(match["sources"][:2]) or "approved records"
                notes.append(
                    f"{teacher['initials']} = {teacher['teacher_name']} (from {sources})"
                )
                filled += 1
        _refresh_teacher_checks(record, directory or {})
    return filled


def fill_initials_in_pending_reviews(pending_reviews, directory):
    """Apply the directory to every pending review.

    ``pending_reviews`` is the list from ``db.get_pending_reviews``. Returns
    ``[(timetable_id, payload, filled_count)]`` for reviews that changed, so
    the caller can save them.
    """
    changed = []
    for item in pending_reviews:
        payload = dict(item["payload"])
        records = []
        for record in payload.get("records") or []:
            record = dict(record)
            record["teachers"] = [
                dict(teacher) for teacher in record.get("teachers") or []
                if isinstance(teacher, dict)
            ]
            record["unresolved_fields"] = dict(record.get("unresolved_fields") or {})
            record["uncertainties"] = list(record.get("uncertainties") or [])
            record["directory_notes"] = list(record.get("directory_notes") or [])
            records.append(record)
        filled = apply_faculty_directory(records, directory)
        if filled:
            payload["records"] = records
            changed.append((item["timetable_id"], payload, filled))
    return changed


def _refresh_teacher_checks(record, directory):
    teachers = record.get("teachers") or []
    unknown = [
        teacher["initials"] for teacher in teachers
        if not teacher.get("teacher_name") and teacher.get("initials")
    ]
    record["unresolved_fields"].pop("teachers", None)
    if unknown:
        reasons = []
        for initials in unknown:
            match = directory.get(_initials_key(initials))
            if match and len(match["names"]) > 1:
                reasons.append(
                    f"Initials {initials} match several faculty: "
                    f"{', '.join(match['names'])}"
                )
            else:
                reasons.append(f"Initials {initials} are not defined in any course table")
        record["unresolved_fields"]["teachers"] = "; ".join(reasons)

    named = next((t["teacher_name"] for t in teachers if t.get("teacher_name")), None)
    if named:
        record["teacher_name"] = named
        record["unresolved_fields"].pop("teacher_name", None)
    record["review_tier"] = _review_tier(record)


def build_records(page, metadata=None, ocr_text=None,
                  source_document=None, source_page=1,
                  faculty_directory=None):
    """Validate one extracted page and return review records and diagnostics."""
    metadata = dict(metadata or {})
    semester = metadata.get("semester") or parse_semester(page.get("semester"))
    section = metadata.get("section") or parse_section(page.get("section"))
    class_name = metadata.get("class_name") or class_name_for(semester, section)
    legend = page.get("legend") or []
    course_keys, legend_names, legend_initials = _legend_index(legend)
    ocr_key = _key(ocr_text) if ocr_text else None

    records = []
    for slot in page.get("slots") or []:
        records.append(_slot_record(
            slot, class_name, semester, section, course_keys,
            legend_names, legend_initials, bool(legend), ocr_key,
            source_document, source_page,
        ))

    records = _mark_parallel_and_overlaps(records)
    # Also sets each record's review tier.
    apply_faculty_directory(records, faculty_directory)

    header = {
        **_day_layout(page.get("periods") or [], records),
        "institution": _clean(page.get("institution")),
        "semester": semester,
        "section": section,
        "class_name": class_name,
        "default_room": _clean(page.get("default_room")),
        "academic_year": _clean(page.get("academic_year")),
    }
    return {
        "records": records,
        "header": header,
        "legend": legend,
        "course_offerings": _course_offerings(legend),
        # Problems live on each record ("issues" in the review table).
        "unresolved_items": [],
        "document_uncertainties": list(page.get("uncertainties") or []),
        "ocr_checked": ocr_key is not None,
    }


def _slot_record(slot, class_name, semester, section, course_keys,
                 legend_names, legend_initials, has_legend, ocr_key,
                 source_document, source_page):
    unresolved = {}
    uncertainties = []
    cell_text = _clean(slot.get("cell_text")) or ""

    day = normalize_day(slot.get("day"))
    if day is None:
        unresolved["day"] = f"Unreadable day: {slot.get('day')!r}"
    start, start_pm = normalize_clock(slot.get("start_time"))
    end, end_pm = normalize_clock(slot.get("end_time"))
    if start is None:
        unresolved["start_time"] = f"Unreadable time: {slot.get('start_time')!r}"
    if end is None:
        unresolved["end_time"] = f"Unreadable time: {slot.get('end_time')!r}"
    if start and end and _minutes(start) >= _minutes(end):
        unresolved["end_time"] = f"Ends ({end}) before it starts ({start})"
    if start_pm or end_pm:
        uncertainties.append("Afternoon time assumed from a time written without AM/PM")

    raw_type = (_clean(slot.get("entry_type")) or "").casefold()
    entry_type = special_entry_type(
        cell_text, slot.get("course_abbrev"), slot.get("course_name"),
    ) or ENTRY_TYPES.get(raw_type, "class")
    optional = entry_type in OPTIONAL_TEACHER_ROOM_TYPES

    course_abbrev = _clean(slot.get("course_abbrev"))
    course = _clean(slot.get("course_name")) or course_abbrev or cell_text or None
    if course is None:
        unresolved["course"] = "No course text in the cell"
    elif has_legend and not optional and not any(
        _key(value) in course_keys
        for value in (slot.get("course_code"), course_abbrev, slot.get("course_name"))
        if _clean(value)
    ):
        uncertainties.append("Course not found in the page's course table")

    teachers = []
    for person in slot.get("teachers") or []:
        name = _clean(person.get("name"))
        short = _clean(person.get("initials"))
        if not name and short and _key(short) in legend_initials:
            name = legend_initials[_key(short)]
        if not name and not short:
            continue
        teachers.append({"teacher_name": name, "initials": short})
        if name is None:
            unresolved.setdefault(
                "teachers", f"Initials {short} are not defined in the course table"
            )
        elif has_legend and _key(name) not in legend_names:
            uncertainties.append(f"Teacher '{name}' not found in the course table")

    teacher_name = next(
        (teacher["teacher_name"] for teacher in teachers if teacher["teacher_name"]),
        teachers[0]["initials"] if teachers else None,
    )
    if not optional and teacher_name is None:
        unresolved["teacher_name"] = "No teacher found for this class"

    room = _clean(slot.get("room"))
    if not optional and room is None:
        unresolved["room"] = "No room found for this class"

    if ocr_key is not None and not _ocr_supports(cell_text, ocr_key):
        uncertainties.append("Cell text was not confirmed by OCR of the page")

    return {
        "day": day,
        "start_time": start,
        "end_time": end,
        "entry_type": entry_type,
        "course": course,
        "subject": course,
        "course_abbrev": course_abbrev,
        "course_code": _clean(slot.get("course_code")),
        "teacher_name": teacher_name,
        "teachers": teachers,
        "room": room,
        "subgroup": _clean(slot.get("group")),
        "parallel_group": None,
        "class_name": class_name,
        "semester": semester,
        "section": section,
        "original_text": cell_text,
        "evidence": cell_text,
        "notes": _clean(slot.get("notes")),
        "source_document": source_document,
        "source_page": source_page,
        "source_bbox": None,
        "legend_source_bbox": None,
        "resolution_method": "gemini_vision",
        "unresolved_fields": unresolved,
        "uncertainties": uncertainties,
        "review_status": "needs_review",
        "verified": False,
        "approved": False,
    }


def _is_alternative(first, second):
    """Students choose between these, so sharing a time is not a clash."""
    return (
        first["entry_type"] in CHOICE_TYPES and second["entry_type"] in CHOICE_TYPES
    ) or "lateral_entry" in (first["entry_type"], second["entry_type"])


def _mark_parallel_and_overlaps(records):
    """Group simultaneous sections/batches and flag genuine overlaps.

    Returns the records, minus a whole-cell record (no group) that the model
    also split into sections ("AEC NOSQL/PE" plus Section 1-4).
    """
    by_cell = {}
    for record in records:
        by_cell.setdefault(
            (record["day"], record["start_time"], record["end_time"],
             _key(record["original_text"])),
            [],
        ).append(record)
    duplicates = {
        id(record)
        for group in by_cell.values()
        if sum(1 for record in group if record["subgroup"]) >= 2
        for record in group
        if not record["subgroup"]
    }
    records = [record for record in records if id(record) not in duplicates]

    timed = [r for r in records if r["day"] and r["start_time"] and r["end_time"]]
    by_slot = {}
    for record in timed:
        by_slot.setdefault(
            (record["day"], record["start_time"], record["end_time"]), []
        ).append(record)
    for (day, start, end), group in by_slot.items():
        subgroups = [record["subgroup"] for record in group]
        same_cell = len({_key(record["original_text"]) for record in group}) == 1
        if len(group) > 1 and (
            (all(subgroups) and len(set(subgroups)) == len(group)) or same_cell
        ):
            for record in group:
                record["parallel_group"] = f"{day} {start}-{end}"

    for index, first in enumerate(timed):
        for second in timed[index + 1:]:
            if first["day"] != second["day"]:
                continue
            if first["parallel_group"] and first["parallel_group"] == second["parallel_group"]:
                continue
            if not (
                _minutes(first["start_time"]) < _minutes(second["end_time"])
                and _minutes(second["start_time"]) < _minutes(first["end_time"])
            ):
                continue
            if first["subgroup"] and second["subgroup"] and first["subgroup"] != second["subgroup"]:
                continue
            if _is_alternative(first, second):
                continue
            for record, other in ((first, second), (second, first)):
                record["uncertainties"].append(
                    f"Overlaps '{other['original_text']}' "
                    f"({other['start_time']}-{other['end_time']})"
                )
    return records


def revalidate_records(records, directory=None):
    """Re-apply the current rules to records extracted earlier.

    Used for reviews still pending when rules change: session types are
    re-recognised, overlap flags recomputed, and initials re-filled.
    """
    checked = []
    for original in records:
        record = dict(original)
        record["teachers"] = [
            dict(teacher) for teacher in record.get("teachers") or []
            if isinstance(teacher, dict)
        ]
        record["unresolved_fields"] = dict(record.get("unresolved_fields") or {})
        record["uncertainties"] = [
            item for item in record.get("uncertainties") or []
            if not str(item).startswith("Overlaps ")
        ]
        record["directory_notes"] = list(record.get("directory_notes") or [])
        record["parallel_group"] = None
        special = special_entry_type(
            record.get("original_text"), record.get("course_abbrev"),
            record.get("course"),
        )
        if special:
            record["entry_type"] = special
        if record.get("entry_type") in OPTIONAL_TEACHER_ROOM_TYPES:
            for field in ("teacher_name", "room"):
                record["unresolved_fields"].pop(field, None)
            record["uncertainties"] = [
                item for item in record["uncertainties"]
                if item != "Course not found in the page's course table"
            ]
        checked.append(record)
    checked = _mark_parallel_and_overlaps(checked)
    apply_faculty_directory(checked, directory or {})
    return checked


def _day_layout(periods, records):
    """Teaching-day bounds and breaks from the period header.

    Falls back to the earliest/latest slot when the header has no usable
    times. Breaks are only taken from the header, never guessed here.
    """
    spans, breaks = [], []
    for period in periods:
        start, _ = normalize_clock(period.get("start_time"))
        end, _ = normalize_clock(period.get("end_time"))
        if not start or not end or _minutes(start) >= _minutes(end):
            continue
        spans.append((start, end))
        if period.get("is_break"):
            breaks.append([start, end])
    if not spans:
        spans = [
            (record["start_time"], record["end_time"])
            for record in records
            if record["start_time"] and record["end_time"]
        ]
    if not spans:
        return {"day_start": None, "day_end": None, "breaks": [], "periods": []}
    teaching = [
        list(span) for span in spans if list(span) not in breaks
    ] if periods else []
    return {
        "day_start": min(spans, key=lambda span: _minutes(span[0]))[0],
        "day_end": max(spans, key=lambda span: _minutes(span[1]))[1],
        "breaks": sorted(breaks, key=lambda item: _minutes(item[0])),
        "periods": sorted(teaching, key=lambda item: _minutes(item[0])),
    }


def _course_offerings(legend):
    """Flatten the course table into rows for the timetable_courses table."""
    offerings = []
    for row in legend or []:
        base = {
            "course_name": row.get("course_name"),
            "abbrev": row.get("course_abbrev") or row.get("course_code"),
            "kind": None,
        }
        assignments = row.get("section_assignments") or []
        if assignments:
            for item in assignments:
                offerings.append({
                    **base,
                    "section_label": item.get("section"),
                    "faculty_name": item.get("teacher_name"),
                    "faculty_initials": item.get("initials"),
                    "room": item.get("room"),
                })
            continue
        for person in row.get("faculty") or [{}]:
            offerings.append({
                **base,
                "section_label": None,
                "faculty_name": person.get("name"),
                "faculty_initials": person.get("initials"),
                "room": None,
            })
    return offerings
