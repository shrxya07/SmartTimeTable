"""Build and persist conservative teacher-initial mappings from legend rows."""

import json
from pathlib import Path
import re


def _value(item, *names, default=None):
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        if value is not None:
            return value
    return default


def _aliases(value):
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace("/", ",").replace("&", ",").split(",")
    else:
        values = list(value)
    return [
        str(alias).strip().strip("()").upper()
        for alias in values
        if str(alias).strip().strip("()")
    ]


def _normalized_name(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _offerings_and_assignments(offerings):
    if isinstance(offerings, dict):
        offerings = offerings.get("courses", [])
    for offering in offerings or []:
        yield offering
        assignments = _value(offering, "assignments", default=[]) or []
        yield from assignments


class TeacherRegistry:
    """Resolve initials only when the legend provides one unambiguous name."""

    def __init__(self, offerings=None, mappings=None):
        self._names_by_initials = {}
        for initials, name in (mappings or {}).items():
            names = name if isinstance(name, (list, tuple, set)) else [name]
            for full_name in names:
                self._add(initials, full_name)
        self.add_offerings(offerings or [])

    def _add(self, initials, name):
        name = str(name or "").strip()
        if not name or name.casefold() in {"unknown", "none", "null"}:
            return
        for alias in _aliases(initials):
            names = self._names_by_initials.setdefault(alias, set())
            existing = next(
                (
                    candidate for candidate in names
                    if _normalized_name(candidate) == _normalized_name(name)
                ),
                None,
            )
            if existing is None:
                names.add(name)
            elif existing != name:
                preferred = max(
                    (existing, name),
                    key=lambda candidate: (
                        len(candidate.split()),
                        len(candidate),
                    ),
                )
                names.remove(existing)
                names.add(preferred)

    def add_offerings(self, offerings):
        for offering in _offerings_and_assignments(offerings):
            if _value(
                offering,
                "faculty_initials_unverified",
                default=False,
            ):
                continue
            name = _value(
                offering, "faculty_name", "teacher_name", "name"
            )
            initials = _value(
                offering, "faculty_initials", "initials",
                "teacher_alias", "faculty_alias",
            )
            self._add(initials, name)

    def resolve(self, initials):
        """Return the unique full name, or the original initials if unknown."""
        key = str(initials or "").strip().strip("()").upper()
        names = self._names_by_initials.get(key, set())
        return next(iter(names)) if len(names) == 1 else initials

    def is_resolved(self, initials):
        key = str(initials or "").strip().strip("()").upper()
        return len(self._names_by_initials.get(key, set())) == 1

    def to_dict(self):
        return {
            alias: sorted(names)
            for alias, names in sorted(self._names_by_initials.items())
        }

    def save(self, path):
        """Persist the registry for reuse when processing later timetables."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path, offerings=None):
        """Load a persisted registry and merge the current timetable legend."""
        path = Path(path)
        mappings = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Teacher registry file must contain an object.")
            mappings = payload
        return cls(offerings=offerings, mappings=mappings)
