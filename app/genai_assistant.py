
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.database.db import initialize_database
from app.database.retrieval import get_verified_teacher_identities

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

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

AI_UNAVAILABLE_MESSAGE = (
    "AI features are disabled because GEMINI_API_KEY is not configured. "
    "Add it to the .env file and restart the application."
)

_client = None


def _load_api_key():
    """Return the configured Gemini API key, or None when it is absent."""
    return (os.getenv("GEMINI_API_KEY") or "").strip() or None


def is_ai_available():
    """Return True when a Gemini API key is configured."""
    return _load_api_key() is not None


def get_client():
    """Create the Gemini client on first use."""
    global _client

    if _client is None:
        api_key = _load_api_key()
        if api_key is None:
            raise RuntimeError(AI_UNAVAILABLE_MESSAGE)
        _client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=60000)
        )

    return _client


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

            response = get_client().models.generate_content(
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

    if not is_ai_available():
        raise RuntimeError(AI_UNAVAILABLE_MESSAGE)

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

    query = teacher_name.strip().casefold()

    # Includes co-teachers from entry_teachers; NULL names are excluded.
    matches = sorted({
        identity["teacher_name"]
        for identity in get_verified_teacher_identities()
        if query in identity["teacher_name"].casefold()
        or query == identity["initials"].casefold()
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
    class_name: str = None,
    day: str = None,
    semester: int = None,
    section: str = None,
):
    """Get a verified class timetable using available class identifiers."""

    if not day:
        return {"error": "Please provide a day."}

    if not class_name and semester is None and not section:
        return {
            "error": "Please provide a class name or semester/section."
        }

    return find_class_schedule(
        class_name=class_name,
        day=day,
        semester=semester,
        section=section,
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
14. When the user provides a semester and section, use those
    values directly with get_class_timetable.
15. Do not invent or assume a class name such as "Class 3".
16. If semester and section are known, class_name is optional.
17. If the user asks about a day, retrieve the matching verified
    records using the database tool.
"""


# --------------------------------------------------
# 8. Persistent Gemini chat session
# --------------------------------------------------

def create_chat_session():
    """Create an independent Gemini chat session, or None without a key."""

    if not is_ai_available():
        return None

    return get_client().chats.create(
        model=MODEL,
        config={
            "system_instruction": SYSTEM_INSTRUCTION,
            "tools": TOOLS,
            "temperature": 0.2,
        }
    )


def ask_timetable_assistant(question: str, chat_session) -> str:
    """Send a question to Gemini and measure response time."""

    if not question or not question.strip():
        return "Please enter a timetable question."

    if chat_session is None:
        return AI_UNAVAILABLE_MESSAGE

    start = time.perf_counter()

    try:
        response = chat_session.send_message(question)

        elapsed = time.perf_counter() - start
        print(f"Gemini chat response time: {elapsed:.2f} seconds")

        return response.text or "I could not generate an answer."

    except Exception as error:
        elapsed = time.perf_counter() - start

        print(f"Gemini error after {elapsed:.2f} seconds:", repr(error))

        return (
            "Sorry, I encountered an error while checking "
            "the timetable. Please try again."
        )


# --------------------------------------------------
# 9. Run directly for testing
# --------------------------------------------------

if __name__ == "__main__":

    initialize_database()
    chat_session = create_chat_session()

    if chat_session is None:
        raise SystemExit(AI_UNAVAILABLE_MESSAGE)

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
