"""Parse structured fields from timetable cell text without model calls."""

import re


_ROOM_PATTERN = re.compile(
    r"\b(?:"
    r"CSE\s*LAB\s*\d+"
    r"|LHC\s*\d+"
    r"|AB\s*\d+\s*(?:&|AND)\s*AB\s*\d+"
    r")\b",
    re.IGNORECASE,
)
_BATCH_PATTERN = re.compile(r"\bS\s*([1-4]|[IL])\b", re.IGNORECASE)
_INITIALS_PATTERN = re.compile(r"\(([A-Z][A-Z0-9]*(?:\s*[,/&]\s*[A-Z][A-Z0-9]*)*)\)", re.I)
_KIND_PATTERN = re.compile(r"\b(LAB|TUT(?:ORIAL)?)\b", re.IGNORECASE)


def _normalize_room(value):
    value = re.sub(r"\s+", " ", value).strip().upper()
    value = re.sub(r"\bCSE\s*LAB\s*(\d+)\b", r"CSE LAB \1", value)
    value = re.sub(r"\bLHC\s*(\d+)\b", r"LHC \1", value)
    value = re.sub(r"\bAB\s*(\d+)\s*(?:&|AND)\s*AB\s*(\d+)\b",
                   r"AB \1 & AB \2", value)
    return value


def parse_line(text):
    """Return batch, course token, kind, teacher initials, and room.

    Parsing removes recognizable fields before collecting the remaining words
    as a course token, so OCR word-order changes do not bind fields by position.
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return {
            "batch": None,
            "course_token": None,
            "kind": None,
            "initials": [],
            "room": None,
        }

    batch_match = _BATCH_PATTERN.search(raw)
    batch_value = batch_match.group(1).upper() if batch_match else None
    batch = (
        f"S{1 if batch_value in {'I', 'L'} else batch_value}"
        if batch_value
        else None
    )
    room_match = _ROOM_PATTERN.search(raw)
    room = _normalize_room(room_match.group(0)) if room_match else None
    kind_match = _KIND_PATTERN.search(raw)
    kind = None
    if kind_match:
        kind = "LAB" if kind_match.group(1).upper() == "LAB" else "TUT"

    initials = []
    for match in _INITIALS_PATTERN.finditer(raw):
        initials.extend(
            re.findall(r"[A-Z][A-Z0-9]*", match.group(1).upper())
        )
    initials = list(dict.fromkeys(initials))

    course = raw
    if room_match:
        course = course[:room_match.start()] + " " + course[room_match.end():]
    course = _INITIALS_PATTERN.sub(" ", course)
    course = _BATCH_PATTERN.sub(" ", course)
    course = _KIND_PATTERN.sub(" ", course)
    course = re.sub(r"\b(?:SECTION|BATCH)\b", " ", course, flags=re.I)
    course = re.sub(r"[\s,:;()\-]+", " ", course).strip(" /&")
    course = re.sub(r"\s+", " ", course).strip()

    return {
        "batch": batch,
        "course_token": course or None,
        "kind": kind,
        "initials": initials,
        "room": room,
    }
