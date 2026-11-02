import json
import sqlite3
import sys
import uuid
from pathlib import Path

# `streamlit run app/streamlit_app.py` only puts app/ on sys.path, so make the
# project root importable before importing the `app` package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from app.genai_assistant import (
    AI_UNAVAILABLE_MESSAGE,
    create_chat_session,
    ask_timetable_assistant,
    gemini_vision_client,
    is_ai_available,
    list_known_timetable_data,
)
from app.ingestion.extraction_pipeline import run_extraction_pipeline
from app.ingestion.timetable_validator import class_name_for
from app.ingestion.vision_extractor import SUPPORTED_SUFFIXES
from app.database.db import (
    get_all_entries,
    get_pending_reviews,
    initialize_database,
    insert_course_offerings,
    insert_entry,
    normalize_missing,
    normalize_time,
    register_timetable,
    save_pending_review,
    update_timetable_metadata,
    update_timetable_status,
)
from app.database.retrieval import get_all_verified_entries


# ---------------- CONFIGURATION ----------------

st.set_page_config(
    page_title="SmartTimeTable",
    page_icon="📅",
    layout="wide",
)

UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"
AI_AVAILABLE = is_ai_available()

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

    if not AI_AVAILABLE:
        st.warning(AI_UNAVAILABLE_MESSAGE)

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

    if not AI_AVAILABLE:
        st.warning(AI_UNAVAILABLE_MESSAGE)

    try:
        known = list_known_timetable_data()
    except sqlite3.Error:
        known = {"teachers": [], "rooms": [], "classes": []}

    if not known["teachers"] and not known["classes"]:
        st.info(
            "No approved timetable data yet. Upload a timetable and approve "
            "its records first."
        )

    # Example questions use names that exist in the approved data.
    example_teacher = (known["teachers"] or ["the teacher"])[0]
    example_room = (known["rooms"] or ["the room"])[0]
    example_class = (known["classes"] or ["3A"])[0]
    examples = [
        f"Show {example_teacher}'s timetable on Monday",
        f"Where is {example_teacher} on Tuesday at 10:00?",
        f"When is {example_teacher} free on Wednesday?",
        f"Which class is using {example_room} on Monday at 11:00?",
        f"When is class {example_class} free on Thursday?",
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
        "Upload a timetable image or PDF. The extracted schedule will "
        "remain unapproved until you explicitly review and save records."
    )

    if not AI_AVAILABLE:
        st.warning(
            "Timetable extraction uses Gemini and is disabled because "
            "GEMINI_API_KEY is not configured."
        )

    uploaded_file = st.file_uploader(
        "Choose a timetable image or PDF",
        type=[suffix.lstrip(".") for suffix in SUPPORTED_SUFFIXES],
    )

    col1, col2 = st.columns(2)

    with col1:
        semester_value = st.selectbox(
            "Semester",
            ["Auto-detect", *range(1, 9)],
            help="Leave on Auto-detect to read it from the timetable.",
        )

    with col2:
        section_value = st.text_input(
            "Section",
            value="",
            placeholder="Auto-detect",
        )

    if uploaded_file is not None:
        if Path(uploaded_file.name).suffix.casefold() == ".pdf":
            st.info(f"PDF selected: {uploaded_file.name}")
        else:
            st.image(
                uploaded_file,
                caption="Uploaded timetable",
                use_container_width=True,
            )

        if st.button(
            "Extract Timetable",
            type="primary",
            disabled=not AI_AVAILABLE,
        ):

            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

            timetable_id = f"upload_{uuid.uuid4().hex[:12]}"

            safe_filename = Path(uploaded_file.name).name
            stored_filename = f"{timetable_id}_{safe_filename}"
            stored_path = UPLOADS_DIR / stored_filename

            semester = (
                None if semester_value == "Auto-detect" else int(semester_value)
            )
            section = section_value.strip().upper() or None

            class_name = class_name_for(semester, section)

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
                    "Reading the timetable with Gemini. This usually takes "
                    "30-90 seconds per page..."
                ):
                    result = run_extraction_pipeline(
                        image_path=stored_path,
                        vision_client=gemini_vision_client,
                        metadata=metadata,
                    )

                header = result.get("header") or {}
                semester = semester or header.get("semester")
                section = section or header.get("section")
                class_name = class_name or header.get("class_name")
                metadata.update({
                    "semester": semester,
                    "section": section,
                    "class_name": class_name,
                })
                update_timetable_metadata(timetable_id, semester, section)
                if result.get("course_offerings"):
                    insert_course_offerings(
                        timetable_id,
                        result["course_offerings"],
                    )

                records = result.get("records", [])

                for record in records:
                    if not isinstance(record, dict):
                        continue
                    if class_name and normalize_missing(
                        record.get("class_name")
                    ) is None:
                        record["class_name"] = class_name
                    if normalize_missing(record.get("semester")) is None:
                        record["semester"] = semester
                    if normalize_missing(record.get("section")) is None:
                        record["section"] = section
                    if normalize_missing(
                        record.get("source_document")
                    ) is None:
                        record["source_document"] = uploaded_file.name

                with st.expander("Detected timetable details"):
                    st.json(header)

                if result.get("legend"):
                    with st.expander(
                        f"Course table ({len(result['legend'])} courses)"
                    ):
                        st.json(result["legend"])

                if result.get("document_uncertainties"):
                    with st.expander("Extraction uncertainties"):
                        st.write(result["document_uncertainties"])

                unresolved_items = result.get("unresolved_items") or []
                if unresolved_items:
                    with st.expander(
                        f"Unresolved extraction items ({len(unresolved_items)})"
                    ):
                        st.json(unresolved_items)

                st.session_state.current_timetable_id = timetable_id
                st.session_state.current_timetable_metadata = metadata
                st.session_state.review_records = records
                st.session_state.review_unresolved_items = unresolved_items
                st.session_state.review_extraction_result = result
                st.session_state.review_save_errors = []
                st.session_state.review_save_notice = ""
                st.session_state.review_editor_revision = (
                    st.session_state.get("review_editor_revision", 0) + 1
                )
                st.session_state.bulk_auto_selected_timetable_id = None

                save_pending_review(timetable_id, {
                    "records": records,
                    "unresolved_items": unresolved_items,
                    "metadata": metadata,
                    "message": result.get("message"),
                })
                update_timetable_status(
                    timetable_id,
                    "needs_review",
                )

                if records:
                    st.success(
                        f"Extraction completed. Found {len(records)} "
                        f"records ({result.get('auto_count', 0)} passed all "
                        "checks); none are approved yet."
                    )
                else:
                    st.warning(
                        "No timetable records were extracted. Review the "
                        "unresolved items and extraction diagnostics."
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

    def load_pending_review(item):
        payload = item["payload"]
        st.session_state.current_timetable_id = item["timetable_id"]
        st.session_state.current_timetable_metadata = (
            payload.get("metadata") or {}
        )
        st.session_state.review_records = payload.get("records") or []
        st.session_state.review_unresolved_items = (
            payload.get("unresolved_items") or []
        )
        st.session_state.review_extraction_result = {
            "message": payload.get("message"),
        }
        st.session_state.review_save_errors = []
        st.session_state.review_save_notice = ""
        st.session_state.review_editor_revision = (
            st.session_state.get("review_editor_revision", 0) + 1
        )
        st.session_state.bulk_auto_selected_timetable_id = None

    # Reviews are stored in the database so they survive a restart.
    try:
        pending_reviews = get_pending_reviews()
    except sqlite3.Error:
        pending_reviews = []
    pending_by_id = {item["timetable_id"]: item for item in pending_reviews}

    if pending_by_id:
        current_id = st.session_state.current_timetable_id
        if current_id is None:
            load_pending_review(pending_reviews[0])
            current_id = st.session_state.current_timetable_id
        options = list(pending_by_id)
        selected_id = st.selectbox(
            "Timetable awaiting review",
            options,
            index=options.index(current_id) if current_id in options else None,
            format_func=lambda timetable_id: (
                f"{pending_by_id[timetable_id]['original_filename'] or timetable_id}"
                f" - {len(pending_by_id[timetable_id]['payload'].get('records') or [])}"
                " records left"
            ),
            placeholder="Choose a timetable",
        )
        if selected_id is not None and selected_id != current_id:
            load_pending_review(pending_by_id[selected_id])
            st.rerun()

    records = st.session_state.review_records
    timetable_id = st.session_state.current_timetable_id
    metadata = st.session_state.current_timetable_metadata
    unresolved_items = st.session_state.get("review_unresolved_items", [])
    extraction_result = st.session_state.get(
        "review_extraction_result",
        {},
    )

    if not timetable_id:
        st.info("Upload and extract a timetable before reviewing records.")

    elif not records and not unresolved_items:
        st.info("There are no extracted records or unresolved items.")

    else:
        if extraction_result.get("message"):
            st.warning(extraction_result["message"])
        if st.session_state.get("review_save_notice"):
            st.success(st.session_state.review_save_notice)
        if st.session_state.get("review_save_errors"):
            st.error("Some records still need attention:")
            for error in st.session_state.review_save_errors:
                st.write(f"- {error}")

        if unresolved_items:
            with st.expander(
                f"Unresolved extraction items ({len(unresolved_items)})",
                expanded=True,
            ):
                st.json(unresolved_items)

        if not records:
            st.info(
                "There are no timetable entries to approve. The unresolved "
                "items above are retained for inspection."
            )
        else:
            st.write(
                "Inspect the OCR evidence and correct entries before "
                "selecting them. `auto` is a deterministic review tier, "
                "not approval; `check` entries require individual review."
            )
            st.warning(
                "Only the separate confirmation action below marks selected "
                "records verified and saves them."
            )

            auto_count = sum(
                record.get("review_tier") == "auto"
                and record.get("review_status") != "verified"
                for record in records
            )
            revision = st.session_state.get("review_editor_revision", 0)
            bulk_selection_id = st.session_state.get(
                "bulk_auto_selected_timetable_id"
            )
            bulk_selected = bulk_selection_id == timetable_id
            bulk_col, clear_col = st.columns(2)
            with bulk_col:
                if st.button(
                    f"Select all auto-tier records ({auto_count})",
                    disabled=auto_count == 0,
                    key=f"select_auto_{timetable_id}_{revision}",
                ):
                    st.session_state.bulk_auto_selected_timetable_id = (
                        timetable_id
                    )
                    st.session_state.review_editor_revision = revision + 1
                    st.rerun()
            with clear_col:
                if st.button(
                    "Clear bulk selection",
                    disabled=not bulk_selected,
                    key=f"clear_auto_{timetable_id}_{revision}",
                ):
                    st.session_state.bulk_auto_selected_timetable_id = None
                    st.session_state.review_editor_revision = revision + 1
                    st.rerun()

            if bulk_selected:
                st.info(
                    "Auto-tier records are selected only. They are not "
                    "approved until you press the separate confirmation button."
                )

            def display_teacher_values(teachers):
                if isinstance(teachers, (str, dict)):
                    teachers = [teachers]
                names = []
                for teacher in teachers or []:
                    if isinstance(teacher, dict):
                        name = (
                            teacher.get("teacher_name")
                            or teacher.get("name")
                            or teacher.get("initials")
                        )
                    else:
                        name = teacher
                    if normalize_missing(name) is not None:
                        names.append(str(name).strip())
                return ", ".join(names)

            def display_json(value):
                if value is None:
                    return ""
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        return value
                return json.dumps(value, ensure_ascii=False, sort_keys=True)

            editable_records = []
            for record in records:
                class_name = normalize_missing(record.get("class_name"))
                if class_name is None:
                    class_name = metadata.get("class_name") or ""
                auto_default = (
                    bulk_selected
                    and record.get("review_tier") == "auto"
                    and record.get("review_status") != "verified"
                )
                editable_records.append({
                    "Approve": auto_default,
                    "review_tier": record.get("review_tier", "check"),
                    "review_status": record.get(
                        "review_status",
                        "needs_review",
                    ),
                    "resolution_method": record.get(
                        "resolution_method",
                        "",
                    ),
                    "ocr_confidence": record.get("ocr_confidence"),
                    "confidence": record.get("confidence"),
                    "entry_type": record.get("entry_type", "class"),
                    "day": record.get("day") or "",
                    "start_time": record.get("start_time") or "",
                    "end_time": record.get("end_time") or "",
                    "course": (
                        record.get("course")
                        or record.get("subject")
                        or ""
                    ),
                    "course_abbrev": record.get("course_abbrev") or "",
                    "teacher_name": record.get("teacher_name") or "",
                    "teachers": display_teacher_values(
                        record.get("teachers")
                    ),
                    "room": record.get("room") or "",
                    "subgroup": record.get("subgroup") or "",
                    "class_name": class_name,
                    "source_document": record.get(
                        "source_document",
                        metadata.get("source_document"),
                    ),
                    "source_page": record.get("source_page"),
                    "source_bbox": display_json(
                        record.get("source_bbox")
                    ),
                    "legend_source_bbox": display_json(
                        record.get("legend_source_bbox")
                    ),
                    "original_text": (
                        record.get("original_text")
                        or record.get("evidence")
                        or ""
                    ),
                    "unresolved_fields": display_json(
                        record.get("unresolved_fields")
                    ),
                    "issues": "; ".join(
                        [
                            str(reason)
                            for reason in (
                                record.get("unresolved_fields") or {}
                            ).values()
                            if isinstance(reason, str)
                        ]
                        + list(record.get("uncertainties") or [])
                    ),
                })

            editor_key = (
                f"review_editor_{timetable_id}_{revision}"
            )
            edited_df = st.data_editor(
                pd.DataFrame(editable_records),
                width="stretch",
                hide_index=True,
                num_rows="fixed",
                key=editor_key,
                disabled=[
                    "review_tier",
                    "review_status",
                    "resolution_method",
                    "entry_type",
                    "course_abbrev",
                    "ocr_confidence",
                    "confidence",
                    "source_document",
                    "source_page",
                    "source_bbox",
                    "legend_source_bbox",
                    "original_text",
                    "unresolved_fields",
                    "issues",
                ],
                column_order=[
                    "Approve", "review_tier", "issues", "entry_type", "day",
                    "start_time", "end_time", "course", "course_abbrev",
                    "teacher_name", "teachers", "room", "subgroup",
                    "class_name", "original_text", "source_document",
                    "source_page", "resolution_method",
                ],
                column_config={
                    "Approve": st.column_config.CheckboxColumn(
                        "Select for approval",
                        help=(
                            "Selection is not approval; use the separate "
                            "confirmation button after inspection."
                        ),
                        default=False,
                    ),
                    "day": st.column_config.SelectboxColumn(
                        "Day",
                        options=[
                            "",
                            "Monday",
                            "Tuesday",
                            "Wednesday",
                            "Thursday",
                            "Friday",
                            "Saturday",
                            "Sunday",
                        ],
                    ),
                    "course": st.column_config.TextColumn("Course"),
                    "teacher_name": st.column_config.TextColumn(
                        "Primary teacher"
                    ),
                    "teachers": st.column_config.TextColumn(
                        "Teachers (comma-separated)"
                    ),
                    "room": st.column_config.TextColumn(
                        "Room (optional for activities/breaks)"
                    ),
                    "subgroup": st.column_config.TextColumn("Subgroup"),
                    "class_name": st.column_config.TextColumn("Class"),
                    "issues": st.column_config.TextColumn(
                        "Issues",
                        help="Why a record needs checking ('check' tier).",
                    ),
                },
            )

            st.caption(
                "Editable: course, primary teacher, teachers, room, "
                "subgroup, class, day and time. Teacher and room may be blank for "
                "activities and breaks. Source evidence and provenance are "
                "read-only."
            )
            selected_count = int(
                edited_df["Approve"].fillna(False).astype(bool).sum()
            )
            if selected_count:
                st.warning(
                    f"{selected_count} record(s) selected. Review them, then "
                    "use the separate confirmation action to approve."
                )

            if st.button(
                "Confirm approval and save selected records",
                type="primary",
                disabled=selected_count == 0,
                key=f"confirm_save_{timetable_id}_{revision}",
            ):
                edited_records = edited_df.to_dict("records")
                existing_entries = [
                    entry for entry in get_all_entries()
                    if entry.get("timetable_id") == timetable_id
                ]

                def padded_time(value):
                    # "9:00" -> "09:00" so duplicate checks match saved rows;
                    # invalid text is left for insert_entry to report.
                    try:
                        return normalize_time(value)
                    except ValueError:
                        return normalize_missing(value)

                def normalized_field(value):
                    value = normalize_missing(value)
                    return str(value).strip().casefold() if value is not None else ""

                def normalized_bbox(value):
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except json.JSONDecodeError:
                            pass
                    if value is None:
                        return ""
                    return json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )

                def duplicate_key(entry):
                    source_text = (
                        entry.get("original_text")
                        or entry.get("evidence")
                    )
                    if source_text is None and entry.get("evidence_json"):
                        try:
                            source_text = json.loads(entry["evidence_json"])
                        except (json.JSONDecodeError, TypeError):
                            source_text = entry.get("evidence_json")
                    return (
                        normalized_field(entry.get("timetable_id")),
                        normalized_field(entry.get("source_page")),
                        normalized_bbox(entry.get("source_bbox")),
                        normalized_field(
                            entry.get("subject") or entry.get("course")
                        ),
                        normalized_field(entry.get("subgroup")),
                        normalized_field(entry.get("parallel_group")),
                        normalized_field(entry.get("day")),
                        normalized_field(entry.get("start_time")),
                        normalized_field(entry.get("end_time")),
                        normalized_field(source_text),
                    )

                def unresolved_matches_entry(item, entry):
                    item_bbox = item.get("source_bbox")
                    item_evidence = item.get("evidence")
                    if item_bbox is None and isinstance(item_evidence, dict):
                        item_bbox = (
                            item_evidence.get("source_bbox")
                            or item_evidence.get("bbox")
                        )
                    if (
                        not normalized_bbox(item_bbox)
                        or normalized_bbox(item_bbox)
                        != normalized_bbox(entry.get("source_bbox"))
                    ):
                        return False
                    item_text = (
                        item.get("line")
                        or item.get("text")
                        or item.get("original_text")
                    )
                    if item_text is None and isinstance(item_evidence, dict):
                        item_text = (
                            item_evidence.get("original_text")
                            or item_evidence.get("text")
                            or item_evidence.get("line")
                        )
                    elif item_text is None:
                        item_text = item_evidence
                    item_text = normalized_field(item_text)
                    record_text = normalized_field(
                        entry.get("original_text") or entry.get("evidence")
                    )
                    return bool(
                        item_text
                        and record_text
                        and (
                            item_text == record_text
                            or item_text in record_text
                            or record_text in item_text
                        )
                    )

                existing_keys = {}
                for existing in existing_entries:
                    key = duplicate_key(existing)
                    previous = existing_keys.get(key)
                    if (
                        previous is None
                        or existing.get("review_status") == "verified"
                    ):
                        existing_keys[key] = existing
                saved_count = 0
                duplicate_count = 0
                already_approved_count = 0
                errors = []
                remaining_records = []
                reviewed_entries = []

                for index, edited in enumerate(edited_records):
                    original = dict(records[index])
                    course = normalize_missing(edited.get("course"))
                    primary_teacher = normalize_missing(
                        edited.get("teacher_name")
                    )
                    teacher_text = normalize_missing(edited.get("teachers"))
                    original_teachers = original.get("teachers") or []
                    original_teacher_text = normalize_missing(
                        display_teacher_values(original_teachers)
                    )
                    teachers_changed = (
                        normalized_field(teacher_text)
                        != normalized_field(original_teacher_text)
                    )

                    if teachers_changed:
                        teachers = [
                            {"teacher_name": name.strip()}
                            for name in str(teacher_text or "").split(",")
                            if normalize_missing(name) is not None
                        ]
                        if (
                            normalized_field(primary_teacher)
                            == normalized_field(
                                original.get("teacher_name")
                            )
                            and teachers
                        ):
                            primary_teacher = normalize_missing(
                                teachers[0]["teacher_name"]
                            )
                    else:
                        teachers = original_teachers
                        if (
                            normalized_field(primary_teacher)
                            != normalized_field(
                                original.get("teacher_name")
                            )
                        ):
                            if isinstance(teachers, (str, dict)):
                                teachers = [teachers]
                            teachers = list(teachers or [])
                            if teachers:
                                first = teachers[0]
                                if isinstance(first, dict):
                                    first = dict(first)
                                    first["teacher_name"] = primary_teacher
                                    teachers[0] = first
                                else:
                                    teachers[0] = primary_teacher
                            elif primary_teacher is not None:
                                teachers = [{
                                    "teacher_name": primary_teacher,
                                }]

                    entry = {
                        **original,
                        "course": course,
                        "subject": course,
                        "course_abbrev": normalize_missing(
                            edited.get("course_abbrev")
                        ),
                        "teacher_name": primary_teacher,
                        "teachers": teachers,
                        "room": normalize_missing(edited.get("room")),
                        "subgroup": normalize_missing(
                            edited.get("subgroup")
                        ),
                        "day": normalize_missing(edited.get("day")),
                        "start_time": padded_time(edited.get("start_time")),
                        "end_time": padded_time(edited.get("end_time")),
                        "class_name": normalize_missing(
                            edited.get("class_name")
                        ),
                        "source_document": (
                            normalize_missing(
                                original.get("source_document")
                            )
                            or metadata.get("source_document")
                        ),
                        "source_page": original.get("source_page"),
                        "source_bbox": original.get("source_bbox"),
                        "original_text": (
                            original.get("original_text")
                            or original.get("evidence")
                        ),
                        "timetable_id": timetable_id,
                    }
                    if normalize_missing(entry.get("class_name")) is None:
                        entry["class_name"] = normalize_missing(
                            metadata.get("class_name")
                        )
                    entry_type = str(
                        normalize_missing(entry.get("entry_type")) or "class"
                    ).casefold()
                    optional_teacher_room = entry_type in {
                        "activity",
                        "break",
                    }
                    missing = [
                        field
                        for field in (
                            "day",
                            "start_time",
                            "end_time",
                            "course",
                            "class_name",
                        )
                        if normalize_missing(entry.get(field)) is None
                    ]
                    if isinstance(teachers, (str, dict)):
                        teacher_values = [teachers]
                    else:
                        teacher_values = teachers or []
                    has_teacher = normalize_missing(
                        entry.get("teacher_name")
                    ) is not None or any(
                        normalize_missing(
                            teacher.get("teacher_name")
                            or teacher.get("name")
                            or teacher.get("initials")
                        )
                        if isinstance(teacher, dict)
                        else normalize_missing(teacher) is not None
                        for teacher in teacher_values
                    )
                    if not optional_teacher_room and not has_teacher:
                        missing.append("teacher")
                    if (
                        not optional_teacher_room
                        and normalize_missing(entry.get("room")) is None
                    ):
                        missing.append("room")

                    pending = dict(original)
                    pending.update({
                        "course": entry.get("course"),
                        "subject": entry.get("subject"),
                        "teacher_name": entry.get("teacher_name"),
                        "teachers": entry.get("teachers"),
                        "room": entry.get("room"),
                        "subgroup": entry.get("subgroup"),
                        "day": entry.get("day"),
                        "start_time": entry.get("start_time"),
                        "end_time": entry.get("end_time"),
                        "class_name": entry.get("class_name"),
                    })
                    if not bool(edited.get("Approve")):
                        remaining_records.append(pending)
                        continue

                    if normalized_field(
                        original.get("review_status")
                    ) == "verified":
                        already_approved_count += 1
                        reviewed_entries.append(entry)
                        continue

                    if missing:
                        errors.append(
                            f"Row {index + 1}: missing required fields: "
                            f"{', '.join(missing)}"
                        )
                        remaining_records.append(pending)
                        continue

                    key = duplicate_key(entry)
                    duplicate = existing_keys.get(key)
                    if duplicate is not None:
                        if duplicate.get("review_status") == "verified":
                            already_approved_count += 1
                            reviewed_entries.append(entry)
                        else:
                            duplicate_count += 1
                            remaining_records.append(pending)
                            errors.append(
                                f"Row {index + 1}: matching entry already "
                                "exists and its approval status was preserved."
                            )
                        continue

                    entry["review_status"] = "verified"
                    try:
                        insert_entry(entry)
                    except (ValueError, TypeError, sqlite3.Error) as error:
                        errors.append(f"Row {index + 1}: {error}")
                        remaining_records.append(pending)
                        continue

                    saved_count += 1
                    existing_keys[key] = entry
                    reviewed_entries.append(entry)

                st.session_state.review_records = remaining_records
                remaining_unresolved_items = [
                    item
                    for item in unresolved_items
                    if not any(
                        unresolved_matches_entry(item, entry)
                        for entry in reviewed_entries
                    )
                ]
                st.session_state.review_unresolved_items = (
                    remaining_unresolved_items
                )
                st.session_state.bulk_auto_selected_timetable_id = None
                st.session_state.review_editor_revision = revision + 1
                save_pending_review(timetable_id, {
                    "records": remaining_records,
                    "unresolved_items": remaining_unresolved_items,
                    "metadata": metadata,
                    "message": extraction_result.get("message"),
                })

                if saved_count:
                    new_status = (
                        "ready"
                        if not remaining_records
                        and not remaining_unresolved_items
                        else "needs_review"
                    )
                    update_timetable_status(timetable_id, new_status)
                notices = []
                if saved_count:
                    notices.append(
                        f"Saved {saved_count} explicitly approved record(s) "
                        "as verified."
                    )
                if already_approved_count:
                    notices.append(
                        f"Skipped {already_approved_count} duplicate record(s) "
                        "that are already verified."
                    )
                if duplicate_count:
                    notices.append(
                        f"Skipped {duplicate_count} duplicate record(s); "
                        "existing database approval status was unchanged."
                    )
                if (
                    not remaining_records
                    and not remaining_unresolved_items
                    and saved_count
                ):
                    notices.append("All extracted records have been reviewed.")

                st.session_state.review_save_notice = " ".join(notices)
                st.session_state.review_save_errors = errors
                st.rerun()
