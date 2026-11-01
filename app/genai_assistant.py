
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.database.db import initialize_database
from app.database.retrieval import get_all_verified_entries

from app.scheduling.engine import (
    find_teacher_schedule,
    find_teacher_at_time,
    find_class_schedule,
    find_room_at_time,
    find_free_slots,
    detect_conflicts,
)


# --------------------------------------------------
# 1. Load Gemini configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

if not API_KEY:
    raise ValueError("GEMINI_API_KEY is missing from .env")

client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(timeout=300000)
)

# Initialize the real SQLite database and its schema.
initialize_database()


# --------------------------------------------------
# 2. Gemini API retry helper
# --------------------------------------------------

def generate_content_with_retry(
    contents,
    config,
    max_attempts=3
):
    """
    Call Gemini and retry temporary API failures.

    Retries HTTP 429, 500, 502, 503 and 504 errors.
    """

    retryable_codes = (429, 500, 502, 503, 504)

    for attempt in range(1, max_attempts + 1):

        try:
            print(
                f"Calling Gemini "
                f"(attempt {attempt}/{max_attempts})..."
            )

            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )

            if not response.text:
                raise ValueError(
                    "Gemini returned an empty response."
                )

            print("Gemini request successful.")
            return response

        except APIError as error:

            if error.code not in retryable_codes:
                raise

            if attempt == max_attempts:
                print("Gemini retry limit reached.")
                raise

            wait_seconds = 2 ** attempt

            print(
                f"Gemini temporarily unavailable "
                f"(HTTP {error.code}). "
                f"Retrying in {wait_seconds} seconds..."
            )

            time.sleep(wait_seconds)


# --------------------------------------------------
# 3. Gemini extraction adapter
# --------------------------------------------------

def gemini_extraction_client(
    system_prompt: str,
    user_prompt: str
):
    """
    Send OCR evidence to Gemini and return structured JSON.

    Used by the timetable document extraction pipeline.
    """

    if not API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")

    response = generate_content_with_retry(
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    return response.text


# --------------------------------------------------
# 4. Helper functions
# --------------------------------------------------

def resolve_teacher_name(teacher_name: str):
    """Resolve a teacher name against verified database records."""

    if not teacher_name or not teacher_name.strip():
        return {"error": "Please provide a teacher name."}

    entries = get_all_verified_entries()

    matches = sorted({
        entry["teacher_name"].strip()
        for entry in entries
        if teacher_name.strip().casefold()
        in entry["teacher_name"].strip().casefold()
    })

    if not matches:
        return {
            "error": (
                f"No verified timetable records found "
                f"for {teacher_name}."
            )
        }

    if len(matches) > 1:
        return {
            "error": "Multiple teachers match your query.",
            "matching_teachers": matches,
            "instruction": "Ask the user to specify the teacher."
        }

    return {"teacher_name": matches[0]}


# --------------------------------------------------
# 5. Real database-backed Gemini tools
# --------------------------------------------------

def get_teacher_schedule(
    teacher_name: str,
    day: str,
    semester: int = None,
    section: str = None
):
    """Get a teacher's verified timetable for a day."""

    resolved = resolve_teacher_name(teacher_name)

    if "error" in resolved:
        return resolved

    return find_teacher_schedule(
        resolved["teacher_name"],
        day,
        semester=semester,
        section=section
    )


def get_teacher_location(
    teacher_name: str,
    day: str,
    time: str
):
    """Find a teacher's scheduled class at a particular time."""

    resolved = resolve_teacher_name(teacher_name)

    if "error" in resolved:
        return resolved

    return find_teacher_at_time(
        resolved["teacher_name"],
        day,
        time
    )


def get_class_timetable(
    class_name: str,
    day: str,
    semester: int = None,
    section: str = None
):
    """Get a class timetable for a particular day."""

    if not class_name or not class_name.strip():
        return {"error": "Please provide a class name."}

    return find_class_schedule(
        class_name,
        day,
        semester=semester,
        section=section
    )


def get_room_schedule(
    room: str,
    day: str,
    time: str
):
    """Find which class is using a room at a particular time."""

    if not room or not room.strip():
        return {"error": "Please provide a room name."}

    return find_room_at_time(room, day, time)


def get_teacher_free_slots(
    teacher_name: str,
    day: str,
    min_duration_minutes: int = 60,
    semester: int = None,
    section: str = None
):
    """Find verified free periods in a teacher's schedule."""

    resolved = resolve_teacher_name(teacher_name)

    if "error" in resolved:
        return resolved

    return find_free_slots(
        resolved["teacher_name"],
        day,
        min_duration_minutes=min_duration_minutes,
        semester=semester,
        section=section
    )


def get_timetable_conflicts(day: str = None):
    """Detect conflicts in verified timetable records."""

    return detect_conflicts(day=day)


# --------------------------------------------------
# 6. Give Gemini access to Python functions
# --------------------------------------------------

TOOLS = [
    get_teacher_schedule,
    get_teacher_location,
    get_class_timetable,
    get_room_schedule,
    get_teacher_free_slots,
    get_timetable_conflicts,
]


# --------------------------------------------------
# 7. Assistant behaviour
# --------------------------------------------------

SYSTEM_INSTRUCTION = """
You are SmartTimeTable Assistant, a college timetable assistant.

You answer questions about:
- Teacher schedules
- Teacher's scheduled classroom at a particular time
- Class timetables
- Room occupancy
- Teacher free periods
- Timetable conflicts

IMPORTANT RULES:

1. Always use the available Python tools to retrieve timetable data.
2. Use only verified timetable records.
3. Never invent teachers, rooms, classes, subjects or timings.
4. Never present records marked needs_review as confirmed.
5. If a tool returns an error or multiple matching teachers,
   ask the user for clarification.
6. If no matching timetable record is found, say so clearly.
7. Do not calculate free slots yourself. Use the scheduling tool.
8. Respect semester and section filters when provided.
9. Explain results in simple, understandable language.
10. Do not claim a teacher is physically present at a location.
    Only describe their scheduled timetable location.
11. If a teacher has no class at a requested time, say that
    no scheduled class was found at that time. Do not assume
    their physical location.
12. If a tool returns no conflicts, say no conflicts were found
    in the verified records searched.
13. Never treat a database error as an empty timetable.

Use clear formatting for schedules, including day, time,
subject, class, teacher and room whenever available.
"""


# --------------------------------------------------
# 8. Persistent Gemini chat session
# --------------------------------------------------

def create_chat_session():
    """Create an independent Gemini chat session."""

    return client.chats.create(
        model=MODEL,
        config={
            "system_instruction": SYSTEM_INSTRUCTION,
            "tools": TOOLS,
            "temperature": 0.2,
        }
    )


def ask_timetable_assistant(
    question: str,
    chat_session
) -> str:
    """Send a question to the persistent Gemini chat session."""

    if not question or not question.strip():
        return "Please enter a timetable question."

    try:
        response = chat_session.send_message(question)

        return response.text or "I could not generate an answer."

    except Exception as error:
        print("Gemini error:", repr(error))

        return (
            "Sorry, I encountered an error while checking "
            "the timetable. Please try again."
        )


# --------------------------------------------------
# 9. Run directly for testing
# --------------------------------------------------

if __name__ == "__main__":

    chat_session = create_chat_session()

    print("SmartTimeTable Gemini Assistant")
    print("Type 'exit' to stop.")

    while True:

        question = input("\nYou: ")

        if question.lower().strip() == "exit":
            break

        answer = ask_timetable_assistant(
            question,
            chat_session
        )

        print("\nAssistant:", answer)
