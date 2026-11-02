"""Shared display helpers for the Streamlit pages."""

import pandas as pd

from app.ingestion.timetable_validator import DAY_NAMES


def _minutes(clock):
    hour, minute = str(clock).split(":")
    return int(hour) * 60 + int(minute)


def _cell_label(entry, show_class=False):
    subject = entry.get("course_abbrev") or entry.get("subject") or ""
    details = [
        value
        for value in (
            entry.get("class_name") if show_class else None,
            entry.get("teacher_name"),
            entry.get("room"),
            entry.get("subgroup"),
        )
        if value
    ]
    return f"{subject} ({', '.join(details)})" if details else subject


def week_grid(entries, show_class=False):
    """Pivot entries into a time x day table; parallel entries share a cell."""
    if not entries:
        return pd.DataFrame()

    days = [day for day in DAY_NAMES if any(e["day"] == day for e in entries)]
    slots = sorted(
        {(e["start_time"], e["end_time"]) for e in entries},
        key=lambda span: (_minutes(span[0]), _minutes(span[1])),
    )
    rows = []
    for start, end in slots:
        row = {"Time": f"{start}-{end}"}
        for day in days:
            labels = [
                _cell_label(entry, show_class)
                for entry in entries
                if entry["day"] == day
                and (entry["start_time"], entry["end_time"]) == (start, end)
            ]
            row[day] = " | ".join(labels)
        rows.append(row)
    return pd.DataFrame(rows).set_index("Time")


def present_days(entries):
    return [day for day in DAY_NAMES if any(e["day"] == day for e in entries)]
