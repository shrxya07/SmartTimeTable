"""Manage Timetables page: list uploads, view one as a grid, delete."""

import sqlite3

import pandas as pd
import streamlit as st

from app.database.db import delete_timetable, get_timetable_overview
from app.database.retrieval import get_timetable_entries
from app.ui.components import week_grid


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
