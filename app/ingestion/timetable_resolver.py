"""Deterministically join timetable slot text to course/faculty offerings."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata

from app.ingestion.cell_line_parser import parse_line
from app.ingestion.teacher_registry import TeacherRegistry


@dataclass(frozen=True)
class SlotCell:
    day: str
    start_time: str
    end_time: str
    text: str = ""
    is_break: bool = False
    bbox: object = None
    lines: tuple = field(default_factory=tuple)
    source_document: str = None
    source_page: int = None


@dataclass(frozen=True)
class CourseOffering:
    course_name: str
    abbrev: str = None
    kind: str = None
    section_label: object = None
    faculty_name: str = None
    faculty_initials: object = field(default_factory=tuple)
    room: str = None
    source_bbox: object = None
    review_required: bool = False
    faculty_initials_unverified: bool = False


_ACTIVITY_TERMS = (
    "INDUSTRY TALK",
    "PROCTOR STUDENT MEETING",
    "COLLEGE DEPT ACTIVITIES",
    "PARTIAL DELIVERY",
)
_FUZZY_CUTOFF = 0.74
_FUZZY_MARGIN = 0.06


def _get(item, *names, default=None):
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        if value is not None:
            return value
    return default


def _usable(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.casefold() in {"unknown", "none", "null"}:
        return None
    return value


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _acronym(value):
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    ignored = {"and", "of", "the", "in", "for", "to"}
    return "".join(
        word[0] for word in words if word.casefold() not in ignored
    ).upper()


def _initial_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return list(dict.fromkeys(
            item.upper()
            for item in re.split(r"[,/&\s]+", value.strip("() "))
            if item
        ))
    return list(dict.fromkeys(
        str(item).strip().strip("()").upper()
        for item in value
        if str(item).strip().strip("()")
    ))


def _flatten_offerings(offerings):
    if isinstance(offerings, dict):
        offerings = offerings.get("courses", [])
    flattened = []
    for offering in offerings or []:
        assignments = _get(offering, "assignments", default=None)
        if assignments:
            for assignment in assignments:
                flattened.append({
                    "course_name": _get(
                        offering, "course_name", "course"
                    ),
                    "abbrev": _get(
                        offering, "abbrev", "abbreviation",
                        "course_abbrev",
                    ),
                    "kind": _get(offering, "kind"),
                    "section_label": _get(
                        assignment, "section_label", "section"
                    ),
                    "faculty_name": _get(
                        assignment, "faculty_name", "teacher_name",
                        "name",
                    ),
                    "faculty_initials": _get(
                        assignment, "faculty_initials", "initials",
                        "teacher_alias", "faculty_alias",
                    ),
                    "room": _get(assignment, "room") or _get(offering, "room"),
                    "source_bbox": _get(
                        assignment, "source_bbox"
                    ) or _get(offering, "source_bbox"),
                    "review_required": bool(
                        _get(assignment, "review_required")
                        or _get(offering, "review_required")
                    ),
                    "faculty_initials_unverified": bool(
                        _get(assignment, "faculty_initials_unverified")
                        or _get(offering, "faculty_initials_unverified")
                    ),
                })
        else:
            flattened.append({
                "course_name": _get(offering, "course_name", "course"),
                "abbrev": _get(
                    offering, "abbrev", "abbreviation", "course_abbrev"
                ),
                "kind": _get(offering, "kind"),
                "section_label": _get(
                    offering, "section_label", "section"
                ),
                "faculty_name": _get(
                    offering, "faculty_name", "teacher_name"
                ),
                "faculty_initials": _get(
                    offering, "faculty_initials", "initials",
                    "teacher_alias", "faculty_alias",
                ),
                "room": _get(offering, "room"),
                "source_bbox": _get(offering, "source_bbox"),
                "review_required": bool(
                    _get(offering, "review_required")
                ),
                "faculty_initials_unverified": bool(
                    _get(offering, "faculty_initials_unverified")
                ),
            })
    return flattened


def _course_groups(offerings):
    groups = {}
    for offering in _flatten_offerings(offerings):
        name = offering.get("course_name")
        abbreviation = offering.get("abbrev")
        key = _norm(abbreviation) or _norm(name)
        if key:
            groups.setdefault(key, {
                "course_name": name,
                "abbrev": abbreviation,
                "offerings": [],
            })["offerings"].append(offering)
    return groups


def _group_aliases(group):
    aliases = [
        ("abbrev", group.get("abbrev")),
        ("course_name", group.get("course_name")),
    ]
    name = str(group.get("course_name") or "")
    without_parentheticals = re.sub(r"\s*\([^()]*\)", "", name).strip()
    without_prefix = re.sub(
        r"^(?:AEC|ELECTIVE)\b\s*[-:]?\s*",
        "",
        without_parentheticals,
        flags=re.I,
    ).strip()
    aliases.extend((
        ("course_name", without_parentheticals),
        ("course_name", without_prefix),
    ))
    unique = []
    seen = set()
    for field_name, alias in aliases:
        key = (field_name, _norm(alias))
        if alias and key not in seen:
            unique.append((field_name, alias))
            seen.add(key)
    return unique


def snap_course(token, legend):
    """Match a cell token to a legend course with an explicit match tier.

    Returns ``None`` for no match or an ambiguous fuzzy match. A match contains
    the canonical course, its offering rows, match tier, and matched field.
    """
    groups = _course_groups(legend)
    raw = str(token or "").strip()
    token_norm = _norm(raw)
    if not token_norm:
        return None

    candidates = []
    for group in groups.values():
        for field_name, alias in _group_aliases(group):
            alias_text = str(alias).strip()
            alias_norm = _norm(alias_text)
            if raw.casefold() == alias_text.casefold():
                candidates.append((group, "exact", field_name))
            elif token_norm == alias_norm:
                candidates.append((group, "normalized", field_name))
    if candidates:
        return _unique_match(candidates)

    # AEC and ELECTIVE are cell labels, not part of the course abbreviation.
    course_token = re.sub(r"^(?:AEC|ELECTIVE)\s+", "", raw, flags=re.I)
    token_norm = _norm(course_token)
    normalized_matches = []
    for group in groups.values():
        for field_name, alias in _group_aliases(group):
            if token_norm == _norm(alias):
                normalized_matches.append((group, "normalized", field_name))
    if normalized_matches:
        return _unique_match(normalized_matches)

    fuzzy = []
    for group in groups.values():
        for field_name, alias in _group_aliases(group):
            alias_norm = _norm(alias)
            score = SequenceMatcher(
                None, token_norm, alias_norm
            ).ratio()
            if (
                len(token_norm) >= 3
                and len(alias_norm) >= 3
                and score >= _FUZZY_CUTOFF
            ):
                fuzzy.append((score, group, field_name))
    if fuzzy:
        fuzzy.sort(key=lambda item: item[0], reverse=True)
        if (
            len(fuzzy) == 1
            or fuzzy[0][0] - fuzzy[1][0] >= _FUZZY_MARGIN
        ):
            _, group, field_name = fuzzy[0]
            return {
                **group,
                "tier": "fuzzy",
                "matched_by": field_name,
            }
        return None

    acronym_matches = []
    for group in groups.values():
        for field_name, alias in _group_aliases(group):
            if field_name == "course_name" and _acronym(alias) == token_norm:
                acronym_matches.append((group, "acronym", field_name))
                break
    if len(acronym_matches) == 1:
        return _unique_match(acronym_matches)
    return None


def _unique_match(candidates):
    unique = {}
    tier_order = {"exact": 0, "normalized": 1, "fuzzy": 2, "acronym": 3}
    for group, tier, field_name in candidates:
        key = _norm(group["abbrev"]) or _norm(group["course_name"])
        current = unique.get(key)
        if current is None or tier_order[tier] < tier_order[current[1]]:
            unique[key] = (group, tier, field_name)
    if len(unique) != 1:
        return None
    group, tier, field_name = next(iter(unique.values()))
    return {**group, "tier": tier, "matched_by": field_name}


def _section_number(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _line_texts(slot):
    lines = _get(slot, "lines", default=()) or ()
    result = []
    for line in lines:
        text = _get(line, "text", default=line if isinstance(line, str) else "")
        if str(text or "").strip():
            result.append(str(text).strip())
    return result


def _slot_text(slot):
    text = _get(slot, "text", "original_text", default="") or ""
    lines = _line_texts(slot)
    if text:
        return str(text).strip()
    return "\n".join(lines)


def _logical_lines(slot):
    lines = _line_texts(slot)
    if not lines:
        return [_slot_text(slot)]

    logical = []
    leading_fragments = []
    for line in lines:
        parsed = parse_line(line)
        token = _norm(parsed["course_token"])
        if token in {"AEC", "ELECTIVE"}:
            leading_fragments.append(line)
            continue
        if not parsed["course_token"]:
            if logical:
                logical[-1] = f"{logical[-1]}\n{line}"
            else:
                leading_fragments.append(line)
            continue
        if leading_fragments:
            line = "\n".join((*leading_fragments, line))
            leading_fragments.clear()
        logical.append(line)

    if leading_fragments:
        if logical:
            logical[-1] = (
                f"{logical[-1]}\n" + "\n".join(leading_fragments)
            )
        else:
            logical.extend(leading_fragments)
    return logical


def _is_activity(text):
    normalized = _norm(text)
    return any(_norm(term) in normalized for term in _ACTIVITY_TERMS)


def _activity_is_legend_course(text, offerings):
    normalized_text = _norm(text)
    for offering in _flatten_offerings(offerings):
        for alias in (offering.get("abbrev"), offering.get("course_name")):
            normalized_alias = _norm(alias)
            if (
                len(normalized_alias) >= 5
                and normalized_alias in normalized_text
            ):
                return True
    return False


def _course_tokens(token, offerings, kind=None):
    if kind:
        kind_match = snap_course(f"{token} {kind}", offerings)
        if kind_match is not None:
            return [(kind_match, False)]

    whole = snap_course(token, offerings)
    components = [
        component.strip()
        for component in str(token or "").split("/")
        if component.strip()
    ]
    if len(components) < 2:
        return [(whole, False)] if whole is not None else []
    result = []
    seen = set()
    for component in components:
        match = snap_course(component, offerings)
        if match is None:
            # AEC is often OCR'd as a prefix to one of the offering aliases.
            match = snap_course("AEC " + component, offerings)
        if match is None:
            continue
        key = _norm(match.get("abbrev")) or _norm(match.get("course_name"))
        if key not in seen:
            seen.add(key)
            result.append((match, True))
    if len(result) > 1:
        return result
    if whole is not None:
        return [(whole, False)]
    return result


def _cell_entries(cell, offerings, registry, header, parallel_group):
    raw = _slot_text(cell)
    parsed_lines = _logical_lines(cell)
    if _is_activity(raw) and not _activity_is_legend_course(raw, offerings):
        return [_make_activity_entry(cell, raw, header)], []
    is_break = bool(_get(cell, "is_break", default=False)) or (
        _norm(raw) == "BREAK"
    )
    if is_break:
        return [_make_break_entry(cell, raw, header)], []

    entries = []
    unresolved = []
    seen_lines = set()
    for line in parsed_lines:
        if not line.strip() or line.strip() in seen_lines:
            continue
        seen_lines.add(line.strip())
        parsed = parse_line(line)
        matches = _course_tokens(
            parsed["course_token"],
            offerings,
            parsed["kind"],
        )
        if not matches:
            unresolved.append({
                "source_bbox": _get(cell, "bbox", "source_bbox"),
                "evidence": raw,
                "line": line,
                "reason": "No unambiguous course match in legend.",
            })
            continue
        token_components = [
            component for component in str(
                parsed["course_token"] or ""
            ).split("/") if component.strip()
        ]
        if len(token_components) > 1 and len(matches) < len(token_components):
            unresolved.append({
                "source_bbox": _get(cell, "bbox", "source_bbox"),
                "evidence": raw,
                "line": line,
                "reason": (
                    "One or more parallel course tokens did not match "
                    "the legend."
                ),
            })
        composite = len(matches) > 1
        for match, is_component in matches:
            rows = match["offerings"]
            batch_number = _section_number(parsed["batch"])
            section_rows = [
                row for row in rows
                if _section_number(row.get("section_label")) is not None
            ]
            selected_rows = rows
            section_mismatch = False
            if batch_number is not None and section_rows:
                selected_rows = [
                    row for row in section_rows
                    if _section_number(row.get("section_label")) == batch_number
                ]
                if not selected_rows:
                    section_mismatch = True
                    unresolved.append({
                        "source_bbox": _get(cell, "bbox", "source_bbox"),
                        "evidence": raw,
                        "line": line,
                        "course": match["course_name"],
                        "reason": (
                            f"Batch {parsed['batch']} has no matching "
                            "legend section."
                        ),
                    })
                    continue
            if not selected_rows:
                selected_rows = [{}]

            for offering in selected_rows:
                entry, issues = _make_course_entry(
                    cell=cell,
                    raw=raw,
                    parsed=parsed,
                    match=match,
                    offering=offering,
                    registry=registry,
                    header=header,
                    composite=composite,
                    parallel_group=parallel_group if composite else None,
                    section_mismatch=section_mismatch,
                )
                entries.append(entry)
                unresolved.extend(issues)
    return entries, unresolved


def _entry_base(cell, raw, header):
    semester = _get(header, "semester")
    section = _get(header, "section")
    class_name = _usable(_get(header, "class_name"))
    if not class_name and semester is not None and section is not None:
        class_name = f"Semester {semester} Section {section}"
    return {
        "day": _get(cell, "day"),
        "start_time": _get(cell, "start_time"),
        "end_time": _get(cell, "end_time"),
        "class_name": class_name,
        "semester": semester,
        "section": section,
        "source_document": (
            _get(cell, "source_document")
            or _get(header, "source_document")
        ),
        "source_page": (
            _get(cell, "source_page")
            or _get(header, "source_page")
        ),
        "source_bbox": _get(cell, "bbox", "source_bbox"),
        "evidence": raw,
        "review_status": "needs_review",
    }


def _slot_is_provenanced(cell, base):
    return (
        bool(base.get("class_name"))
        and all(
            _get(cell, field)
            for field in ("day", "start_time", "end_time")
        )
        and _get(cell, "bbox", "source_bbox") is not None
    )


def _make_activity_entry(cell, raw, header):
    base = _entry_base(cell, raw, header)
    return {
        **base,
        "entry_type": "activity",
        "subgroup": None,
        "parallel_group": None,
        "course": raw,
        "subject": raw,
        "course_abbrev": None,
        "teacher_name": None,
        "teachers": [],
        "room": None,
        "resolution_method": "activity_lexicon",
        "review_tier": (
            "auto" if _slot_is_provenanced(cell, base) else "check"
        ),
    }


def _make_break_entry(cell, raw, header):
    base = _entry_base(cell, raw or "BREAK", header)
    return {
        **base,
        "entry_type": "break",
        "subgroup": None,
        "parallel_group": None,
        "course": "Break",
        "subject": "Break",
        "course_abbrev": None,
        "teacher_name": None,
        "teachers": [],
        "room": None,
        "resolution_method": "slot_metadata",
        "review_tier": (
            "auto" if _slot_is_provenanced(cell, base) else "check"
        ),
    }


def _make_course_entry(
    cell,
    raw,
    parsed,
    match,
    offering,
    registry,
    header,
    composite,
    parallel_group,
    section_mismatch,
):
    cell_initials = parsed["initials"]
    expected_initials = _initial_list(offering.get("faculty_initials"))
    mapped_teachers = []
    if cell_initials:
        for initials in cell_initials:
            mapped_teachers.append({
                "teacher_name": registry.resolve(initials),
                "initials": initials,
                "role": "",
            })
    elif _usable(offering.get("faculty_name")):
        mapped_teachers.append({
            "teacher_name": _usable(offering["faculty_name"]),
            "initials": (
                expected_initials[0] if expected_initials else None
            ),
            "role": "",
        })

    initials_match = (
        not cell_initials
        or not expected_initials
        or offering.get("faculty_initials_unverified")
        or bool(set(cell_initials) & set(expected_initials))
    )
    issues = []
    if cell_initials and not initials_match:
        issues.append({
            "source_bbox": _get(cell, "bbox", "source_bbox"),
            "evidence": raw,
            "line": parsed["course_token"],
            "course": match["course_name"],
            "reason": "Cell teacher initials do not match the legend offering.",
        })

    teacher_name = (
        mapped_teachers[0]["teacher_name"] if mapped_teachers else None
    )
    if teacher_name is not None and str(teacher_name).upper() in cell_initials:
        teacher_resolved = False
    else:
        teacher_resolved = bool(mapped_teachers) and all(
            registry.is_resolved(item["initials"])
            for item in mapped_teachers
            if item.get("initials")
        )
        if not cell_initials and _usable(offering.get("faculty_name")):
            teacher_resolved = True

    kind = parsed["kind"] or offering.get("kind")
    normalized_kind = str(kind or "").casefold()
    is_aec = str(parsed.get("course_token") or "").upper().startswith("AEC ")
    if is_aec and not kind:
        kind = "AEC"
    entry_type = (
        "lab" if normalized_kind == "lab"
        else "tutorial" if normalized_kind in {"tut", "tutorial"}
        else "elective" if composite and not is_aec
        else "class"
    )
    section = (
        parsed["batch"]
        or offering.get("section_label")
    )
    room = (
        _usable(parsed["room"])
        or _usable(offering.get("room"))
        or _usable(_get(header, "default_room", "room"))
    )
    match_tier = match["tier"]
    deterministic = (
        match_tier in {"exact", "normalized"}
        and teacher_resolved
        and room is not None
        and initials_match
        and not section_mismatch
        and _slot_is_provenanced(cell, _entry_base(cell, raw, header))
        and offering.get("source_bbox") is not None
    )
    review_tier = (
        "auto"
        if deterministic and not offering.get("review_required")
        else "check"
    )
    entry = {
        **_entry_base(cell, raw, header),
        "entry_type": entry_type,
        "kind": kind,
        "subgroup": section,
        "parallel_group": parallel_group,
        "course": match["course_name"],
        "subject": match["course_name"],
        "course_abbrev": match["abbrev"],
        "teacher_name": teacher_name,
        "teachers": mapped_teachers,
        "room": room,
        "resolution_method": match_tier,
        "review_tier": review_tier,
        "legend_source_bbox": offering.get("source_bbox"),
    }
    return entry, issues


def _legend_course_keys(offerings):
    keys = {}
    for offering in _flatten_offerings(offerings):
        name = offering.get("course_name")
        abbreviation = offering.get("abbrev")
        key = _norm(abbreviation) or _norm(name)
        if key:
            keys.setdefault(key, {
                "course_name": name,
                "abbrev": abbreviation,
            })
    return keys


def resolve_slots(slots, offerings, header):
    """Resolve slot cells and report unresolved cells and unused legend rows."""
    slots = list(slots or [])
    offerings = list(offerings or [])
    header = header or {}
    registry = TeacherRegistry(offerings=offerings)
    entries = []
    unresolved = []
    matched_courses = set()

    for slot_index, slot in enumerate(slots):
        raw = _slot_text(slot)
        if not raw.strip() and not _get(slot, "is_break", default=False):
            continue
        parsed = parse_line(raw)
        parallel_group = (
            f"parallel:{_get(slot, 'day')}:{_get(slot, 'start_time')}:"
            f"{_get(slot, 'end_time')}:{slot_index}"
        )
        resolved, slot_unresolved = _cell_entries(
            slot, offerings, registry, header, parallel_group
        )
        entries.extend(resolved)
        unresolved.extend(slot_unresolved)
        for entry in resolved:
            key = _norm(entry.get("course_abbrev")) or _norm(
                entry.get("course")
            )
            if key:
                matched_courses.add(key)

    unmatched_courses = []
    for key, course in _legend_course_keys(offerings).items():
        if key not in matched_courses:
            unmatched_courses.append({
                **course,
                "reason": "Legend course was not matched to a non-empty grid cell.",
            })

    return {
        "entries": entries,
        "unresolved_items": unresolved,
        "completeness": {
            "non_empty_slot_count": sum(
                bool(_slot_text(slot).strip())
                or bool(_get(slot, "is_break", default=False))
                for slot in slots
            ),
            "resolved_entry_count": len(entries),
            "unresolved_item_count": len(unresolved),
            "unmatched_legend_courses": unmatched_courses,
            "complete": not unresolved and not unmatched_courses,
        },
    }
