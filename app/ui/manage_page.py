"""Manage Timetables page: list uploads, view one as a grid, delete."""

import sqlite3

import pandas as pd
import streamlit as st

from app.database.db import (
    delete_timetable,
    get_pending_reviews,
    get_timetable_overview,
    save_pending_review,
    save_teacher_alias,
)
from app.database.retrieval import (
    get_faculty_directory,
    get_timetable_entries,
    get_verified_teacher_identities,
)
from app.ingestion.timetable_validator import revalidate_records
from app.ui.components import week_grid


def refresh_pending_reviews():
    """Re-check every pending review with the current rules and names."""
    directory = get_faculty_directory()
    changed = 0
    for item in get_pending_reviews():
        payload = dict(item["payload"])
        records = payload.get("records") or []
        if not records:
            continue
        payload["records"] = revalidate_records(records, directory)
        save_pending_review(item["timetable_id"], payload)
        changed += 1
    return changed


def render_teacher_merge():
    st.subheader("Same teacher, different spelling")
    st.caption(
        "Timetables sometimes spell one teacher differently (\"MAMTHA A\" / "
        "\"MAMATHA A\"). Confirm they are the same person once, and every "
        "lookup, free-time answer and initials fill treats them as one."
    )
    if st.session_state.get("merge_notice"):
        st.success(st.session_state.pop("merge_notice"))
    directory = get_faculty_directory()
    suggestions = {
        initials: entry["names"]
        for initials, entry in sorted(directory.items())
        if len(entry["names"]) > 1
    }
    for initials, names in suggestions.items():
        left, right = st.columns([3, 1])
        keep = left.selectbox(
            f"Initials {initials}: {' / '.join(names)}. Keep which spelling?",
            names,
            key=f"merge_keep_{initials}",
        )
        if right.button("Same person", key=f"merge_{initials}"):
            for name in names:
                if name != keep:
                    save_teacher_alias(name, keep)
            refreshed = refresh_pending_reviews()
            st.session_state.merge_notice = (
                f"Merged into {keep}; re-checked {refreshed} pending review(s)."
            )
            st.rerun()

    spellings = sorted(
        {identity["teacher_name"] for identity in get_verified_teacher_identities()}
        | {name for entry in directory.values() for name in entry["names"]}
    )
    with st.expander("Merge two other names"):
        first = st.selectbox("This name", spellings, key="merge_first")
        second = st.selectbox("is the same teacher as", spellings, key="merge_second")
        if st.button(
            "Merge",
            disabled=not first or first == second,
            key="merge_manual",
        ):
            save_teacher_alias(first, second)
            refreshed = refresh_pending_reviews()
            st.session_state.merge_notice = (
                f"{first} is now treated as {second}; re-checked "
                f"{refreshed} pending review(s)."
            )
            st.rerun()


def render():
    st.title("Manage Timetables")
    st.write(
        "Every uploaded timetable, how many of its records are approved, and "
        "how many still wait for review."
    )

    try:
        overview = get_timetable_overview()
    except sqlite3.Error as error:
        st.error(f"Could not read the database: {error}")
        return

    if not overview:
        st.info("No timetables uploaded yet.")
        return

    st.dataframe(
        pd.DataFrame([
            {
                "File": item["original_filename"],
                "Semester": item["semester"],
                "Section": item["section"],
                "Status": item["status"],
                "Approved records": item["verified_count"],
                "Awaiting review": item["pending_count"],
                "Old unapproved rows": item["unreviewed_count"],
                "Uploaded": item["uploaded_at"],
                "ID": item["timetable_id"],
            }
            for item in overview
        ]),
        hide_index=True,
        width="stretch",
    )

    by_id = {item["timetable_id"]: item for item in overview}
    selected = st.selectbox(
        "Open a timetable",
        list(by_id),
        format_func=lambda timetable_id: (
            f"{by_id[timetable_id]['original_filename']} "
            f"({by_id[timetable_id]['verified_count']} approved) - {timetable_id}"
        ),
    )
    if selected is None:
        return

    entries = get_timetable_entries(selected, review_status="verified")
    if entries:
        st.subheader("Approved timetable")
        st.dataframe(week_grid(entries), width="stretch")
    else:
        st.info("This timetable has no approved records yet.")

    render_teacher_merge()

    with st.expander("Delete this timetable"):
        item = by_id[selected]
        st.warning(
            f"Deletes '{item['original_filename']}' with its "
            f"{item['verified_count']} approved record(s), "
            f"{item['pending_count']} record(s) awaiting review and its "
            "course table. This cannot be undone."
        )
        confirmed = st.checkbox(
            "I understand, delete it",
            key=f"confirm_delete_{selected}",
        )
        if st.button(
            "Delete timetable",
            type="primary",
            disabled=not confirmed,
            key=f"delete_{selected}",
        ):
            delete_timetable(selected)
            if st.session_state.get("current_timetable_id") == selected:
                st.session_state.current_timetable_id = None
                st.session_state.review_records = []
                st.session_state.review_unresolved_items = []
            st.success(f"Deleted {item['original_filename']}.")
            st.rerun()
