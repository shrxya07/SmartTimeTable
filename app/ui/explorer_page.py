"""Schedules & Conflicts page: weekly views, free periods and clashes."""

import sqlite3

import pandas as pd
import streamlit as st

from app.database.retrieval import search_entries
from app.genai_assistant import (
    get_class_free_slots,
    get_room_free_slots,
    get_teacher_free_slots,
    list_known_timetable_data,
)
from app.ingestion.timetable_validator import DAY_NAMES
from app.scheduling.engine import (
    detect_conflicts,
    find_class_schedule,
    find_teacher_schedule,
)
from app.ui.components import present_days, week_grid


def _free_table(days, lookup):
    rows = []
    for day in days:
        result = lookup(day)
        if isinstance(result, dict):
            rows.append({"Day": day, "Free": result.get("note") or result.get("error")})
            continue
        rows.append({
            "Day": day,
            "Free": ", ".join(
                f"{slot['start_time']}-{slot['end_time']}" for slot in result
            ) or "No free period",
        })
    return pd.DataFrame(rows)


def _show(entries, free_lookup, show_class):
    if not entries:
        st.info("No approved records.")
        return
    st.dataframe(week_grid(entries, show_class=show_class), width="stretch")
    st.markdown("**Free periods** (breaks and hours taken from the timetables)")
    st.dataframe(
        _free_table(present_days(entries), free_lookup),
        hide_index=True,
        width="stretch",
    )


def render():
    st.title("Schedules & Conflicts")

    try:
        known = list_known_timetable_data()
    except sqlite3.Error as error:
        st.error(f"Could not read the database: {error}")
        return
    if not known["teachers"] and not known["classes"]:
        st.info("No approved timetable data yet.")
        return

    teacher_tab, class_tab, room_tab, conflict_tab = st.tabs(
        ["Teacher", "Class", "Room", "Conflicts"]
    )

    with teacher_tab:
        teacher = st.selectbox("Teacher", known["teachers"], key="explore_teacher")
        if teacher:
            entries = [
                entry
                for day in DAY_NAMES
                for entry in find_teacher_schedule(teacher, day)
            ]
            _show(
                entries,
                lambda day: get_teacher_free_slots(teacher, day),
                show_class=True,
            )

    with class_tab:
        class_name = st.selectbox("Class", known["classes"], key="explore_class")
        if class_name:
            entries = [
                entry
                for day in DAY_NAMES
                for entry in find_class_schedule(class_name=class_name, day=day)
            ]
            _show(
                entries,
                lambda day: get_class_free_slots(day, class_name=class_name),
                show_class=False,
            )

    with room_tab:
        room = st.selectbox("Room", known["rooms"], key="explore_room")
        if room:
            entries = [
                entry for entry in search_entries(room=room)
                if entry["room"] and entry["room"].casefold() == room.casefold()
            ]
            _show(
                entries,
                lambda day: get_room_free_slots(room, day),
                show_class=True,
            )

    with conflict_tab:
        conflicts = detect_conflicts()
        if not conflicts:
            st.success(
                "No clashes found: no teacher, room or class is booked twice "
                "at the same time in the approved records."
            )
        else:
            st.error(f"{len(conflicts)} clash(es) in the approved records.")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Day": item["entry_1"]["day"],
                        "Clash": ", ".join(item["conflict_types"]),
                        "First": (
                            f"{item['entry_1']['start_time']}-"
                            f"{item['entry_1']['end_time']} "
                            f"{item['entry_1']['subject']} "
                            f"({item['entry_1']['class_name']}, "
                            f"{item['entry_1']['teacher_name']}, "
                            f"{item['entry_1']['room']})"
                        ),
                        "Second": (
                            f"{item['entry_2']['start_time']}-"
                            f"{item['entry_2']['end_time']} "
                            f"{item['entry_2']['subject']} "
                            f"({item['entry_2']['class_name']}, "
                            f"{item['entry_2']['teacher_name']}, "
                            f"{item['entry_2']['room']})"
                        ),
                    }
                    for item in conflicts
                ]),
                hide_index=True,
                width="stretch",
            )
