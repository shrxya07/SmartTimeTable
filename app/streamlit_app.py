import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from app.genai_assistant import (
    create_chat_session,
    ask_timetable_assistant,
    gemini_extraction_client,
)
from app.ingestion.extraction_pipeline import run_extraction_pipeline
from app.database.db import (
    initialize_database,
    insert_entry,
    register_timetable,
    update_timetable_status,
)
from app.database.retrieval import get_all_verified_entries


# ---------------- CONFIGURATION ----------------

st.set_page_config(
    page_title="SmartTimeTable",
    page_icon="📅",
    layout="wide",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"

initialize_database()


# ---------------- SESSION STATE ----------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "chat_session" not in st.session_state:
    st.session_state.chat_session = create_chat_session()

if "review_records" not in st.session_state:
    st.session_state.review_records = []

if "current_timetable_id" not in st.session_state:
    st.session_state.current_timetable_id = None

if "current_timetable_metadata" not in st.session_state:
    st.session_state.current_timetable_metadata = {}


# ---------------- SIDEBAR ----------------

try:
    verified_count = len(get_all_verified_entries())
except Exception:
    verified_count = 0

with st.sidebar:
    st.title("📅 SmartTimeTable")
    st.caption("AI-powered College Timetable Assistant")

    st.divider()

    st.subheader("Database")
    st.metric("Verified timetable entries", verified_count)
    st.caption("Chat answers use verified database records.")

    st.divider()

    st.subheader("About")
    st.write(
        "Find teacher schedules, classroom assignments, "
        "class timetables and free periods using natural language."
    )

    st.divider()

    if st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.chat_session = create_chat_session()
        st.rerun()


# ---------------- NAVIGATION ----------------

page = st.sidebar.radio(
    "Navigation",
    [
        "Chat Assistant",
        "Upload Timetable",
        "Review Extracted Records",
    ],
)


# ============================================================
# PAGE 1: CHAT ASSISTANT
# ============================================================

if page == "Chat Assistant":

    st.title("Timetable Assistant")
    st.write("Ask questions about your verified college timetable.")

    examples = [
        "Show Dr. Rao's timetable on Monday",
        "Where is Dr. Rao on Monday at 10:30 AM?",
        "When is Dr. Rao free on Monday?",
        "Which class is using LHC 301 on Monday at 11 AM?",
        "Show the timetable for CSE 3A on Monday",
    ]

    st.markdown("### Try asking")

    columns = st.columns(2)

    for index, example in enumerate(examples):
        if columns[index % 2].button(
            example,
            key=f"example_{index}",
            use_container_width=True,
        ):
            st.session_state.pending_question = example

    st.divider()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask your timetable question...")

    if prompt:
        st.session_state.pending_question = prompt

    if "pending_question" in st.session_state:
        question = st.session_state.pop("pending_question")

        st.session_state.messages.append({
            "role": "user",
            "content": question,
        })

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Checking timetable..."):
                try:
                    response = ask_timetable_assistant(
                        question,
                        st.session_state.chat_session,
                    )
                except Exception as error:
                    response = f"Sorry, I encountered an error: {error}"

            st.markdown(response)

        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
        })


# ============================================================
# PAGE 2: UPLOAD TIMETABLE
# ============================================================

elif page == "Upload Timetable":

    st.title("Upload Timetable")
    st.write(
        "Upload a timetable image. OCR and Gemini will extract "
        "the schedule for you to review."
    )

    uploaded_file = st.file_uploader(
        "Choose a timetable image",
        type=["png", "jpg", "jpeg"],
    )

    col1, col2 = st.columns(2)

    with col1:
        # Current project timetable: Semester 7, Section C.
        # Change these defaults when uploading a different class timetable.
        semester_value = st.selectbox(
            "Semester",
            list(range(1, 9)),
            index=6,
        )

    with col2:
        section_value = st.text_input(
            "Section",
            value="C",
            placeholder="Example: A",
        )

    if uploaded_file is not None:
        st.image(
            uploaded_file,
            caption="Uploaded timetable",
            use_container_width=True,
        )

        if st.button("Extract Timetable", type="primary"):

            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

            timetable_id = f"upload_{uuid.uuid4().hex[:12]}"

            safe_filename = Path(uploaded_file.name).name
            stored_filename = f"{timetable_id}_{safe_filename}"
            stored_path = UPLOADS_DIR / stored_filename

            semester = int(semester_value)
            section = section_value.strip().upper() or None

            class_name = (
                f"{semester}{section}"
                if section
                else None
            )

            metadata = {
                "source_document": uploaded_file.name,
                "semester": semester,
                "section": section,
                "class_name": class_name,
            }

            try:
                stored_path.write_bytes(uploaded_file.getvalue())

                register_timetable(
                    timetable_id=timetable_id,
                    original_filename=uploaded_file.name,
                    stored_path=stored_path,
                    semester=semester,
                    section=section,
                    status="processing",
                )

                with st.spinner(
                    "Running OCR and Gemini extraction. Please wait..."
                ):
                    result = run_extraction_pipeline(
                        image_path=stored_path,
                        llm_client=gemini_extraction_client,
                        metadata=metadata,
                    )

                records = result.get("records", [])

                # Semester and section are authoritative upload metadata.
                # Do not rely on Gemini to repeat metadata already supplied
                # by the user. Fill only missing/UNKNOWN class_name values.
                if class_name:
                    for record in records:
                        current_class = str(
                            record.get("class_name", "")
                        ).strip()

                        if not current_class or current_class.upper() == "UNKNOWN":
                            record["class_name"] = class_name

                with st.expander("Debug: OCR text detected"):
                    st.text(
                        result.get("ocr_evidence", {}).get(
                            "text",
                            "No OCR text",
                        )
                    )

                with st.expander("Debug: Extraction metadata"):
                    st.json(metadata)

                if result.get("document_uncertainties"):
                    with st.expander("Extraction uncertainties"):
                        st.write(result["document_uncertainties"])

                st.session_state.current_timetable_id = timetable_id
                st.session_state.current_timetable_metadata = metadata
                st.session_state.review_records = records

                update_timetable_status(
                    timetable_id,
                    "needs_review",
                )

                st.success(
                    f"Extraction completed. Found {len(records)} records."
                )

                if result.get("message"):
                    st.warning(result["message"])

                st.info(
                    "Go to 'Review Extracted Records' to inspect "
                    "and approve the extracted timetable."
                )

            except Exception as error:
                try:
                    update_timetable_status(timetable_id, "failed")
                except Exception:
                    pass

                st.error(f"Timetable processing failed: {error}")


# ============================================================
# PAGE 3: REVIEW EXTRACTED RECORDS
# ============================================================

elif page == "Review Extracted Records":

    st.title("Review Extracted Records")

    records = st.session_state.review_records
    timetable_id = st.session_state.current_timetable_id
    metadata = st.session_state.current_timetable_metadata

    if not timetable_id:
        st.info("Upload and extract a timetable before reviewing records.")

    elif not records:
        st.info("There are no extracted records waiting for review.")

    else:
        st.write(
            "Check the extracted information carefully. Correct errors "
            "and select Approve only for records you have verified."
        )

        st.warning(
            "Only approved records with all required fields will be "
            "saved to the database."
        )

        editable_records = []

        for record in records:
            editable = {
                "Approve": False,
                "day": record.get("day", ""),
                "start_time": record.get("start_time", ""),
                "end_time": record.get("end_time", ""),
                "subject": record.get("subject", ""),
                "teacher_name": record.get("teacher_name", ""),
                "class_name": record.get(
                    "class_name",
                    metadata.get("class_name", "UNKNOWN"),
                ),
                "room": record.get("room", ""),
                "original_room_text": record.get(
                    "original_room_text",
                    record.get("room", ""),
                ),
                "source_page": record.get("source_page"),
            }

            # Also cover records whose class_name was explicitly UNKNOWN.
            if (
                str(editable["class_name"]).strip().upper() == "UNKNOWN"
                and metadata.get("class_name")
            ):
                editable["class_name"] = metadata["class_name"]

            editable_records.append(editable)

        edited_df = st.data_editor(
            pd.DataFrame(editable_records),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key=f"review_editor_{timetable_id}",
            column_config={
                "Approve": st.column_config.CheckboxColumn(
                    "Approve",
                    help="Select only after checking this record.",
                    default=False,
                ),
                "day": st.column_config.SelectboxColumn(
                    "Day",
                    options=[
                        "Monday",
                        "Tuesday",
                        "Wednesday",
                        "Thursday",
                        "Friday",
                        "Saturday",
                        "Sunday",
                    ],
                ),
            },
        )

        st.caption(
            "Required: day, start_time, end_time, subject, "
            "teacher_name, class_name and room."
        )

        if st.button("Save Approved Records", type="primary"):

            approved_records = edited_df.to_dict("records")

            selected = [
                record for record in approved_records
                if record.get("Approve") is True
            ]

            if not selected:
                st.warning("Select at least one record to approve.")

            else:
                required_fields = [
                    "day",
                    "start_time",
                    "end_time",
                    "subject",
                    "teacher_name",
                    "class_name",
                    "room",
                ]

                saved_count = 0
                errors = []
                remaining_records = []

                for index, record in enumerate(approved_records):

                    # Preserve edited values for rows that remain pending.
                    if not record.get("Approve"):
                        pending = dict(records[index])
                        for field in required_fields:
                            value = record.get(field)
                            if pd.isna(value) if value is not None else True:
                                value = ""
                            pending[field] = str(value).strip()
                        pending["original_room_text"] = str(
                            record.get("original_room_text")
                            or pending.get("room", "")
                        )
                        remaining_records.append(pending)
                        continue

                    cleaned = {}

                    for field in required_fields:
                        value = record.get(field)

                        if pd.isna(value) if value is not None else True:
                            value = ""

                        cleaned[field] = str(value).strip()

                    missing = [
                        field for field in required_fields
                        if not cleaned[field]
                        or cleaned[field].upper() == "UNKNOWN"
                    ]

                    if missing:
                        errors.append(
                            f"Row {index + 1}: missing or unresolved "
                            f"fields: {', '.join(missing)}"
                        )
                        pending = dict(records[index])
                        pending.update(cleaned)
                        remaining_records.append(pending)
                        continue

                    try:
                        entry = {
                            **cleaned,
                            "review_status": "verified",
                            "source_document": metadata.get(
                                "source_document"
                            ),
                            "source_page": record.get("source_page"),
                            "semester": metadata.get("semester"),
                            "section": metadata.get("section"),
                            "timetable_id": timetable_id,
                            "original_room_text": str(
                                record.get("original_room_text")
                                or cleaned["room"]
                            ),
                        }

                        insert_entry(entry)
                        saved_count += 1

                    except Exception as error:
                        errors.append(
                            f"Row {index + 1}: {error}"
                        )
                        pending = dict(records[index])
                        pending.update(cleaned)
                        remaining_records.append(pending)

                st.session_state.review_records = remaining_records

                if saved_count:
                    new_status = (
                        "ready" if not remaining_records
                        else "needs_review"
                    )

                    update_timetable_status(
                        timetable_id,
                        new_status,
                    )

                    st.success(
                        f"Successfully saved {saved_count} verified records."
                    )

                if errors:
                    st.error(
                        "Some selected records could not be saved:"
                    )
                    for error in errors:
                        st.write(f"- {error}")

                if not remaining_records:
                    st.success(
                        "All extracted records have been reviewed."
                    )

                st.rerun()
