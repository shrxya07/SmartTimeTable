"""Check vision-extracted timetable pages and turn slots into review records.

Nothing here invents data. Every check that fails is recorded on the record
(``unresolved_fields`` / ``uncertainties``) and moves it to the ``check``
review tier. No record is ever marked verified; that is the reviewer's job.
"""

import re


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
    "activity": "activity",
    "break": "break",
}
OPTIONAL_TEACHER_ROOM_TYPES = {"activity", "break"}

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


def build_records(page, metadata=None, ocr_text=None,
                  source_document=None, source_page=1):
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

    _mark_parallel_and_overlaps(records)

    for record in records:
        record["review_tier"] = (
            "auto"
            if not record["unresolved_fields"] and not record["uncertainties"]
            else "check"
        )

    header = {
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
    entry_type = ENTRY_TYPES.get(raw_type, "class")
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


def _mark_parallel_and_overlaps(records):
    """Group simultaneous sections/batches and flag genuine overlaps."""
    timed = [r for r in records if r["day"] and r["start_time"] and r["end_time"]]
    by_slot = {}
    for record in timed:
        by_slot.setdefault(
            (record["day"], record["start_time"], record["end_time"]), []
        ).append(record)
    for (day, start, end), group in by_slot.items():
        subgroups = [record["subgroup"] for record in group]
        if len(group) > 1 and all(subgroups) and len(set(subgroups)) == len(group):
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
            for record, other in ((first, second), (second, first)):
                record["uncertainties"].append(
                    f"Overlaps '{other['original_text']}' "
                    f"({other['start_time']}-{other['end_time']})"
                )


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
