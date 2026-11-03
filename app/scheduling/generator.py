"""Generate a clash-free weekly timetable for one class.

Hard constraints (never broken):
- a class, teacher or room is never booked twice at the same time, including
  against other classes' approved timetables (``busy``);
- a multi-period session (lab, 2-hour tutorial) uses consecutive periods and
  never spans a break;
- every teacher and room of a session is free for its whole span, so parallel
  sections/batches (several teachers and rooms at once) stay together.

Preferences (used to order choices, relaxed only if no solution exists):
- at most one session of the same course per day;
- spread the class's load evenly over the week.

The search is a depth-first backtracking search that always places the
session with the fewest remaining options next (minimum remaining values).
"""

from dataclasses import dataclass, field
import re

from app.scheduling.engine import split_rooms


@dataclass
class Requirement:
    """One kind of session a class needs every week."""

    course: str
    sessions_per_week: int
    periods_per_session: int = 1
    kind: str = "class"
    teachers: list = field(default_factory=list)
    rooms: list = field(default_factory=list)
    course_abbrev: str = None
    subgroup: str = None


def _minutes(clock):
    hour, minute = str(clock).split(":")
    return int(hour) * 60 + int(minute)


def _key(value):
    """Same teacher/room despite spelling: 'CSE LAB1' == 'cse lab 1'."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _by_key(bookings):
    """Group bookings by name key, merging spellings of the same name."""
    grouped = {}
    for name, intervals in (bookings or {}).items():
        grouped.setdefault(_key(name), []).extend(intervals)
    return grouped


def _overlaps(first, second):
    return first[0] < second[1] and second[0] < first[1]


class _Problem:
    def __init__(self, days, periods, requirements, busy, fixed, day_bounds=None):
        self.days = list(days)
        # Optional per-day teaching hours, e.g. a half-day Saturday.
        self.day_bounds = {
            day: (_minutes(start), _minutes(end))
            for day, (start, end) in (day_bounds or {}).items()
        }
        self.periods = sorted(periods, key=lambda span: _minutes(span[0]))
        self.spans = [
            (_minutes(start), _minutes(end)) for start, end in self.periods
        ]
        self.busy = {
            "teacher": _by_key((busy or {}).get("teacher")),
            "room": _by_key((busy or {}).get("room")),
        }
        self.sessions = [
            (index, requirement)
            for requirement in requirements
            for index in range(requirement.sessions_per_week)
        ]
        # Class periods taken by fixed events (activities kept in place).
        self.fixed = [
            (day, (_minutes(start), _minutes(end)))
            for day, start, end in fixed or []
        ]

    def placements(self, requirement):
        """All (day, first_period) where the session fits the period grid."""
        length = requirement.periods_per_session
        result = []
        for day in self.days:
            for first in range(len(self.spans) - length + 1):
                block = self.spans[first:first + length]
                bounds = self.day_bounds.get(day)
                if bounds and not (
                    bounds[0] <= block[0][0] and block[-1][1] <= bounds[1]
                ):
                    continue
                if all(
                    block[i][1] == block[i + 1][0] for i in range(length - 1)
                ):
                    result.append((day, first))
        return result

    def interval(self, first, requirement):
        return (
            self.spans[first][0],
            self.spans[first + requirement.periods_per_session - 1][1],
        )


def _external_conflict(problem, requirement, day, span):
    for teacher in requirement.teachers:
        for busy_day, busy_span in problem.busy["teacher"].get(_key(teacher), []):
            if busy_day == day and _overlaps(span, busy_span):
                return f"teacher {teacher} is teaching another class"
    for room in requirement.rooms:
        for busy_day, busy_span in problem.busy["room"].get(_key(room), []):
            if busy_day == day and _overlaps(span, busy_span):
                return f"room {room} is used by another class"
    for fixed_day, fixed_span in problem.fixed:
        if fixed_day == day and _overlaps(span, fixed_span):
            return "the class has a fixed activity"
    return None


def _solve(problem, per_day_limit, node_limit):
    """Backtracking search; returns (assignment, nodes, complete)."""
    options = {
        id(requirement): [
            (day, first, problem.interval(first, requirement))
            for day, first in problem.placements(requirement)
            if _external_conflict(
                problem, requirement, day, problem.interval(first, requirement)
            ) is None
        ]
        for _, requirement in problem.sessions
    }

    assignment = {}
    class_load = {day: 0 for day in problem.days}
    taken = []  # (day, span, teachers, rooms)
    course_days = {}
    nodes = 0
    best = {}

    def fits(requirement, day, span):
        if course_days.get((_key(requirement.course), day), 0) >= per_day_limit:
            return False
        # One class: nothing may overlap (parallel groups are one session),
        # which also keeps its own teachers and rooms single-booked.
        return not any(
            taken_day == day and _overlaps(span, taken_span)
            for taken_day, taken_span, _, _ in taken
        )

    def candidates(slot):
        _, requirement = problem.sessions[slot]
        return [
            option for option in options[id(requirement)]
            if fits(requirement, option[0], option[2])
        ]

    def search():
        nonlocal nodes, best
        nodes += 1
        if len(assignment) > len(best):
            best = dict(assignment)
        if len(assignment) == len(problem.sessions):
            return True
        if nodes > node_limit:
            return False

        open_slots = [
            slot for slot in range(len(problem.sessions)) if slot not in assignment
        ]
        scored = [(slot, candidates(slot)) for slot in open_slots]
        slot, choices = min(
            scored,
            key=lambda item: (
                len(item[1]),
                -problem.sessions[item[0]][1].periods_per_session,
            ),
        )
        if not choices:
            return False

        requirement = problem.sessions[slot][1]
        course = _key(requirement.course)
        choices.sort(key=lambda option: (
            course_days.get((course, option[0]), 0),
            class_load[option[0]],
            option[1],
            problem.days.index(option[0]),
        ))
        for day, first, span in choices:
            assignment[slot] = (day, first, span)
            taken.append((day, span, requirement.teachers, requirement.rooms))
            class_load[day] += requirement.periods_per_session
            course_days[(course, day)] = course_days.get((course, day), 0) + 1

            if search():
                return True

            assignment.pop(slot)
            taken.pop()
            class_load[day] -= requirement.periods_per_session
            course_days[(course, day)] -= 1
        return False

    complete = search()
    return (assignment if complete else best), nodes, complete


def _why_unplaced(problem, requirement):
    reasons = {}
    for day, first in problem.placements(requirement):
        reason = _external_conflict(
            problem, requirement, day, problem.interval(first, requirement)
        )
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    if not problem.placements(requirement):
        return "no run of consecutive periods is long enough"
    if reasons and sum(reasons.values()) == len(problem.placements(requirement)):
        return "; ".join(sorted(reasons))
    return "the class's week is already full at every remaining time"


def generate_timetable(
    class_name,
    days,
    periods,
    requirements,
    busy=None,
    fixed=None,
    node_limit=50000,
    day_bounds=None,
):
    """Build a weekly timetable.

    ``periods`` is a list of (start, end) teaching periods (breaks left out).
    ``busy`` maps "teacher"/"room" -> name -> [(day, (start_min, end_min))]
    from other classes. ``fixed`` lists (day, start, end) the class already
    spends on activities. ``day_bounds`` maps day -> (start, end) teaching
    hours when days differ (e.g. Saturday ends at noon). Returns records ready for the review page plus a
    summary of how the constraints were met.
    """
    requirements = [r for r in requirements if r.sessions_per_week > 0]
    problem = _Problem(days, periods, requirements, busy, fixed, day_bounds)

    needed = sum(r.sessions_per_week * r.periods_per_session for r in requirements)
    capacity = sum(
        1
        for day in problem.days
        for span in problem.spans
        if not problem.day_bounds.get(day)
        or (problem.day_bounds[day][0] <= span[0]
            and span[1] <= problem.day_bounds[day][1])
    ) - sum(
        sum(1 for span in problem.spans if _overlaps(span, fixed_span))
        for _, fixed_span in problem.fixed
    )

    per_day_limit = 1
    assignment, nodes, complete = _solve(problem, per_day_limit, node_limit)
    relaxed = False
    if not complete:
        per_day_limit = 2
        relaxed_assignment, more_nodes, complete = _solve(
            problem, per_day_limit, node_limit
        )
        nodes += more_nodes
        if complete or len(relaxed_assignment) > len(assignment):
            assignment, relaxed = relaxed_assignment, True

    records = []
    for slot, (day, first, span) in sorted(
        assignment.items(),
        key=lambda item: (problem.days.index(item[1][0]), item[1][1]),
    ):
        _, requirement = problem.sessions[slot]
        start = problem.periods[first][0]
        end = problem.periods[first + requirement.periods_per_session - 1][1]
        teachers = [{"teacher_name": name, "initials": None} for name in requirement.teachers]
        records.append({
            "day": day,
            "start_time": start,
            "end_time": end,
            "entry_type": requirement.kind,
            "course": requirement.course,
            "subject": requirement.course,
            "course_abbrev": requirement.course_abbrev,
            "teacher_name": requirement.teachers[0] if requirement.teachers else None,
            "teachers": teachers,
            "room": " & ".join(requirement.rooms) or None,
            "subgroup": requirement.subgroup,
            "class_name": class_name,
            "original_text": f"Generated: {requirement.course}",
            "evidence": f"Generated: {requirement.course}",
            "resolution_method": "generated",
            "review_tier": "auto",
            "review_status": "needs_review",
            "unresolved_fields": {},
            "uncertainties": [],
            "source_bbox": None,
            "legend_source_bbox": None,
        })

    unplaced = []
    placed_counts = {}
    for slot in assignment:
        requirement = problem.sessions[slot][1]
        placed_counts[id(requirement)] = placed_counts.get(id(requirement), 0) + 1
    for requirement in requirements:
        missing = requirement.sessions_per_week - placed_counts.get(id(requirement), 0)
        if missing > 0:
            unplaced.append({
                "course": requirement.course,
                "missing_sessions": missing,
                "reason": _why_unplaced(problem, requirement),
            })

    return {
        "records": records,
        "complete": not unplaced,
        "unplaced": unplaced,
        "relaxed_one_per_day": relaxed,
        "periods_needed": needed,
        "periods_available": capacity,
        "search_nodes": nodes,
        "load_per_day": {
            day: sum(
                problem.sessions[slot][1].periods_per_session
                for slot, (assigned_day, _, _) in assignment.items()
                if assigned_day == day
            )
            for day in problem.days
        },
    }


def check_timetable(records, busy=None):
    """Independently verify a generated week; returns a list of violations."""
    violations = []
    timed = [
        (record, (_minutes(record["start_time"]), _minutes(record["end_time"])))
        for record in records
    ]
    for index, (first, first_span) in enumerate(timed):
        for second, second_span in timed[index + 1:]:
            if first["day"] == second["day"] and _overlaps(first_span, second_span):
                violations.append(
                    f"class double-booked on {first['day']} "
                    f"{first['start_time']}: {first['course']} / {second['course']}"
                )
    busy = busy or {}
    teacher_busy = _by_key(busy.get("teacher"))
    room_busy = _by_key(busy.get("room"))
    for record, span in timed:
        for teacher in record.get("teachers") or []:
            name = _key(teacher.get("teacher_name"))
            for day, busy_span in teacher_busy.get(name, []):
                if day == record["day"] and _overlaps(span, busy_span):
                    violations.append(
                        f"{teacher['teacher_name']} also teaches elsewhere on "
                        f"{day} {record['start_time']}"
                    )
        for room in split_rooms(record.get("room")):
            for day, busy_span in room_busy.get(_key(room), []):
                if room and day == record["day"] and _overlaps(span, busy_span):
                    violations.append(f"room {room} is taken on {day} {record['start_time']}")
    return violations
