"""Generate Timetable page: build a clash-free week for a class."""

import sqlite3
import time
import uuid

import pandas as pd
import streamlit as st

from app.database.db import register_timetable, save_pending_review
from app.database.retrieval import get_verified_classes
from app.ingestion.timetable_validator import DAY_NAMES, normalize_clock
from app.scheduling.generator import Requirement, check_timetable, generate_timetable
from app.scheduling.generator_data import (
    existing_commitments,
    requirements_from_class,
    teaching_days,
    teaching_periods,
)
from app.ui.components import week_grid

KINDS = ["class", "lab", "tutorial", "elective"]
DEFAULT_PERIODS = [
    ("09:00", "09:55"), ("09:55", "10:50"), ("11:05", "12:00"),
    ("12:00", "12:55"), ("13:45", "14:40"), ("14:40", "15:35"),
    ("15:35", "16:30"),
]


def _split(text):
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def _load_inputs(source):
    """Prefill editors from an approved class timetable (or a blank form)."""
    if source is None:
        return {
            "periods": DEFAULT_PERIODS,
            "requirements": [Requirement("New course", 3, teachers=["Teacher"], rooms=["Room"])],
            "fixed": [],
        }
    periods = teaching_periods(source) or DEFAULT_PERIODS
    requirements, fixed = requirements_from_class(source, periods)
    return {"periods": periods, "requirements": requirements, "fixed": fixed}


def render():
    st.title("Generate Timetable")
    st.write(
        "Builds a clash-free week for one class. Teachers and rooms already "
        "booked by other classes' approved timetables are respected, labs get "
        "consecutive periods that never cross a break, and parallel sections "
        "stay together."
    )

    try:
        classes = [item["class_name"] for item in get_verified_classes()]
        days_available, day_bounds = teaching_days()
    except sqlite3.Error as error:
        st.error(f"Could not read the database: {error}")
        return

    source = st.selectbox(
        "Start from",
        [None, *classes],
        format_func=lambda value: (
            "A blank form" if value is None
            else f"Class {value}'s approved timetable (courses, teachers, rooms)"
        ),
    )
    if st.session_state.get("generator_source", "unset") != source:
        st.session_state.generator_source = source
        st.session_state.generator_inputs = _load_inputs(source)
        st.session_state.generator_result = None
        st.session_state.generator_revision = (
            st.session_state.get("generator_revision", 0) + 1
        )
    inputs = st.session_state.generator_inputs
    revision = st.session_state.generator_revision

    target_class = st.text_input(
        "Class name for the new timetable",
        value=source or "",
        placeholder="e.g. 5D",
    ).strip()

    days = st.multiselect(
        "Teaching days",
        DAY_NAMES,
        default=days_available or DAY_NAMES[:5],
    )

    st.markdown("**Courses needed each week**")
    st.caption(
        "One row per kind of session. A row with several teachers/rooms is a "
        "parallel session (e.g. elective sections or lab batches) that needs "
        "all of them at the same time."
    )
    requirement_rows = st.data_editor(
        pd.DataFrame([
            {
                "Course": r.course,
                "Type": r.kind if r.kind in KINDS else "class",
                "Sessions per week": r.sessions_per_week,
                "Periods per session": r.periods_per_session,
                "Teachers": ", ".join(r.teachers),
                "Rooms": ", ".join(r.rooms),
            }
            for r in inputs["requirements"]
        ]),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key=f"generator_requirements_{revision}",
        column_config={
            "Type": st.column_config.SelectboxColumn(options=KINDS),
            "Sessions per week": st.column_config.NumberColumn(min_value=0, max_value=10, step=1),
            "Periods per session": st.column_config.NumberColumn(min_value=1, max_value=4, step=1),
        },
    )

    period_col, fixed_col = st.columns(2)
    with period_col:
        st.markdown("**Teaching periods** (leave breaks out)")
        period_rows = st.data_editor(
            pd.DataFrame(inputs["periods"], columns=["Start", "End"]),
            num_rows="dynamic",
            hide_index=True,
            key=f"generator_periods_{revision}",
        )
    with fixed_col:
        st.markdown("**Fixed activities** (kept where they are)")
        fixed_rows = st.data_editor(
            pd.DataFrame(inputs["fixed"], columns=["Day", "Start", "End"]),
            num_rows="dynamic",
            hide_index=True,
            key=f"generator_fixed_{revision}",
            column_config={"Day": st.column_config.SelectboxColumn(options=DAY_NAMES)},
        )

    respect_others = st.checkbox(
        "Avoid times when these teachers and rooms are booked by other classes",
        value=True,
    )

    if st.button("Generate timetable", type="primary"):
        errors = []
        periods = []
        for row in period_rows.to_dict("records"):
            start, _ = normalize_clock(row.get("Start"))
            end, _ = normalize_clock(row.get("End"))
            if start and end:
                periods.append((start, end))
            elif row.get("Start") or row.get("End"):
                errors.append(f"Unreadable period {row.get('Start')}-{row.get('End')}")
        fixed = []
        for row in fixed_rows.to_dict("records"):
            start, _ = normalize_clock(row.get("Start"))
            end, _ = normalize_clock(row.get("End"))
            if row.get("Day") and start and end:
                fixed.append((row["Day"], start, end))
        requirements = []
        for row in requirement_rows.to_dict("records"):
            course = str(row.get("Course") or "").strip()
            if not course:
                continue
            try:
                sessions = int(row.get("Sessions per week") or 0)
                length = int(row.get("Periods per session") or 1)
            except (TypeError, ValueError):
                errors.append(f"{course}: sessions and periods must be numbers")
                continue
            requirements.append(Requirement(
                course=course,
                sessions_per_week=sessions,
                periods_per_session=max(1, length),
                kind=row.get("Type") or "class",
                teachers=_split(row.get("Teachers")),
                rooms=_split(row.get("Rooms")),
            ))
        if not target_class:
            errors.append("Enter a class name for the new timetable.")
        if not days or not periods:
            errors.append("Choose at least one day and one period.")

        if errors:
            for error in errors:
                st.error(error)
        else:
            busy = existing_commitments(exclude_class=source or target_class) if respect_others else None
            started = time.perf_counter()
            result = generate_timetable(
                target_class, days, periods, requirements,
                busy=busy, fixed=fixed,
                day_bounds={day: day_bounds[day] for day in days if day in day_bounds},
            )
            result["seconds"] = time.perf_counter() - started
            result["violations"] = check_timetable(result["records"], busy)
            st.session_state.generator_result = result

    result = st.session_state.get("generator_result")
    if not result:
        return

    if result["complete"]:
        st.success(
            f"Placed every session ({result['periods_needed']} of "
            f"{result['periods_available']} available periods) in "
            f"{result['seconds']:.2f} s."
        )
    else:
        st.warning("Some sessions could not be placed:")
        st.dataframe(pd.DataFrame(result["unplaced"]), hide_index=True, width="stretch")
    if result["relaxed_one_per_day"]:
        st.info("Some courses needed two sessions on one day to fit.")
    if result["violations"]:
        st.error("Independent check found clashes: " + "; ".join(result["violations"]))
    else:
        st.caption(
            "Independent check: no class, teacher or room is double-booked, "
            "including against other classes' approved timetables."
        )

    st.dataframe(week_grid(result["records"]), width="stretch")
    st.bar_chart(pd.Series(result["load_per_day"], name="Periods per day"))

    if classes and target_class in classes:
        st.warning(
            f"Class {target_class} already has an approved timetable. Approving "
            "this one adds a second; delete the old one on Manage Timetables first."
        )
    if st.button("Send to review", disabled=not result["records"]):
        timetable_id = f"generated_{uuid.uuid4().hex[:12]}"
        for record in result["records"]:
            record["source_document"] = f"Generated timetable for {target_class}"
            record["source_page"] = None
        register_timetable(
            timetable_id=timetable_id,
            original_filename=f"Generated timetable for {target_class}",
            stored_path="generated",
            status="needs_review",
        )
        save_pending_review(timetable_id, {
            "records": result["records"],
            "unresolved_items": [],
            "metadata": {
                "class_name": target_class,
                "source_document": f"Generated timetable for {target_class}",
            },
            "message": "Generated timetable: review it, then approve the records.",
        })
        st.session_state.current_timetable_id = None
        st.success("Sent to Review Extracted Records, where you can approve it.")
