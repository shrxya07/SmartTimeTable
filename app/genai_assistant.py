
import os
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
import httpx

from app.database.db import get_teacher_aliases, initialize_database, person_key
from app.database.retrieval import (
    get_day_layout,
    get_faculty_directory,
    get_verified_classes,
    get_verified_rooms,
    get_verified_teacher_identities,
)
from app.ingestion.timetable_validator import (
    DAY_NAMES,
    normalize_clock,
    normalize_day,
)

from app.scheduling.engine import (
    find_teacher_schedule,
    find_teacher_at_time,
    find_class_schedule,
    find_room_at_time,
    find_free_slots,
    find_room_bookings,
    find_room_free_slots,
    find_section_free_slots,
    detect_conflicts,
)


# --------------------------------------------------
# 1. Load Gemini configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Extraction needs the stronger model: flash-lite mis-read merged cells.
VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.5-flash")
# "minimal" thinking matched full accuracy on the sample timetables in
# about half the time. Set GEMINI_VISION_THINKING= (empty) for the default.
VISION_THINKING_LEVEL = os.getenv("GEMINI_VISION_THINKING", "minimal") or None

AI_UNAVAILABLE_MESSAGE = (
    "AI features are disabled because GEMINI_API_KEY is not configured. "
    "Add it to the .env file and restart the application."
)

_client = None
# Streamlit sessions and PDF pages run on threads; build exactly one client.
_client_lock = threading.Lock()


def _load_api_key():
    """Return the configured Gemini API key, or None when it is absent."""
    return (os.getenv("GEMINI_API_KEY") or "").strip() or None


def is_ai_available():
    """Return True when a Gemini API key is configured."""
    return _load_api_key() is not None


def get_client():
    """Create the Gemini client on first use."""
    global _client

    with _client_lock:
        if _client is None:
            api_key = _load_api_key()
            if api_key is None:
                raise RuntimeError(AI_UNAVAILABLE_MESSAGE)
            # Vision extraction of a dense page can take ~90 s.
            _client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=180000)
            )

    return _client


# --------------------------------------------------
# 2. Gemini API retry helper
# --------------------------------------------------

MAX_RETRY_WAIT_SECONDS = 65


class GeminiQuotaError(RuntimeError):
    """The Gemini quota is used up and waiting a minute will not help."""


def _retry_wait_seconds(error, attempt):
    """Use the delay Gemini asks for (e.g. 'retry in 31s'), else back off."""
    match = re.search(
        r"retry(?:Delay['\"]?:\s*['\"]| in )(\d+(?:\.\d+)?)s",
        str(error),
    )
    if match:
        return min(float(match.group(1)) + 1, MAX_RETRY_WAIT_SECONDS)
    return min(2 ** attempt, MAX_RETRY_WAIT_SECONDS)


def _is_daily_quota(error):
    return error.code == 429 and "PerDay" in str(error)


# A dropped connection or failed DNS lookup is worth retrying.
NETWORK_ERRORS = (httpx.TransportError, ConnectionError)


def describe_gemini_error(error):
    """A short message for the UI instead of a raw API error dump."""
    if isinstance(error, GeminiQuotaError):
        return str(error)
    if isinstance(error, APIError):
        if error.code == 429:
            return (
                "Gemini's rate limit was reached (the free tier allows a few "
                "requests per minute). Wait a minute and try again."
            )
        if error.code in (500, 502, 503, 504):
            return "Gemini is temporarily unavailable. Try again shortly."
        if error.code in (401, 403):
            return "Gemini rejected the API key. Check GEMINI_API_KEY in .env."
        return f"Gemini request failed (HTTP {error.code})."
    if isinstance(error, NETWORK_ERRORS):
        return (
            "Couldn't reach Gemini. Check the internet connection and "
            "try again."
        )
    return str(error)


def generate_content_with_retry(
    contents,
    config,
    max_attempts=5,
    model=None,
):
    """
    Call Gemini and retry temporary API failures.

    Retries HTTP 429, 500, 502, 503 and 504 errors, waiting as long as a
    rate-limit response asks (the free tier allows ~5 requests a minute),
    and brief network drops. A used-up daily quota is raised at once as
    GeminiQuotaError.
    """

    retryable_codes = (429, 500, 502, 503, 504)

    for attempt in range(1, max_attempts + 1):

        try:
            print(
                f"Calling Gemini "
                f"(attempt {attempt}/{max_attempts})..."
            )

            response = get_client().models.generate_content(
                model=model or MODEL,
                contents=contents,
                config=config,
            )

            if not response.text:
                raise ValueError(
                    "Gemini returned an empty response."
                )

            print("Gemini request successful.")
            return response

        except NETWORK_ERRORS as error:

            if attempt == max_attempts:
                print("Gemini unreachable; retry limit reached.")
                raise

            wait_seconds = min(2 ** attempt, MAX_RETRY_WAIT_SECONDS)
            print(
                f"Network error reaching Gemini ({error!r}). "
                f"Retrying in {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)

        except APIError as error:

            if _is_daily_quota(error):
                raise GeminiQuotaError(
                    "Gemini's daily free quota for this API key is used up. "
                    "Try again tomorrow or use a key with billing enabled."
                ) from error

            if error.code not in retryable_codes:
                raise

            if attempt == max_attempts:
                print("Gemini retry limit reached.")
                raise

            wait_seconds = _retry_wait_seconds(error, attempt)

            print(
                f"Gemini temporarily unavailable "
                f"(HTTP {error.code}). "
                f"Retrying in {wait_seconds} seconds..."
            )

            time.sleep(wait_seconds)


# --------------------------------------------------
# 3. Gemini vision extraction adapter
# --------------------------------------------------

def gemini_vision_client(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    response_schema,
):
    """
    Send one timetable page image to Gemini and return structured JSON.

    Used by the timetable extraction pipeline.
    """

    if not is_ai_available():
        raise RuntimeError(AI_UNAVAILABLE_MESSAGE)

    response = generate_content_with_retry(
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=response_schema,
            thinking_config=(
                types.ThinkingConfig(thinking_level=VISION_THINKING_LEVEL)
                if VISION_THINKING_LEVEL
                else None
            ),
        ),
        model=VISION_MODEL,
    )

    return response.text


# --------------------------------------------------
# 4. Helper functions
# --------------------------------------------------

_TITLES = re.compile(r"\b(dr|prof|professor|mr|mrs|ms|miss|sir|madam)\b\.?", re.I)


def _name_key(value):
    """Compare names ignoring titles, case, spaces and punctuation."""
    without_titles = _TITLES.sub(" ", str(value or ""))
    return re.sub(r"[^a-z0-9]", "", without_titles.casefold())


def _compact(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def resolve_teacher_name(teacher_name: str):
    """Resolve a teacher name or initials against verified database records."""

    if not teacher_name or not teacher_name.strip():
        return {"error": "Please provide a teacher name."}

    query = _name_key(teacher_name)
    if not query:
        return {"error": "Please provide a teacher name."}

    # Includes co-teachers from entry_teachers; NULL names are excluded.
    identities = get_verified_teacher_identities()
    directory = get_faculty_directory()
    aliases_map = get_teacher_aliases()

    def person_of(name):
        # Confirmed same-teacher spellings map to one canonical name.
        return _name_key(aliases_map.get(person_key(name), name))

    # One person per name key: "DR.SUSHMA B" and "Dr Sushma B" are the same
    # teacher written differently in two timetables.
    initials_by_person = {}
    for identity in identities:
        if identity["initials"]:
            initials_by_person.setdefault(
                person_of(identity["teacher_name"]), set()
            ).add(_compact(identity["initials"]))
    for initials, entry in directory.items():
        if len(entry["names"]) == 1:
            initials_by_person.setdefault(
                person_of(entry["names"][0]), set()
            ).add(_compact(initials))

    exact = {
        person_of(identity["teacher_name"])
        for identity in identities
        if query == person_of(identity["teacher_name"])
        or query == _compact(identity["initials"])
    } | {
        person for person, initials in initials_by_person.items()
        if query in initials
    }
    people = exact or {
        person_of(identity["teacher_name"])
        for identity in identities
        if query in person_of(identity["teacher_name"])
    }
    # A name that is itself someone's initials ("SB") is that person.
    people = {
        next(
            (person for person, initials in initials_by_person.items()
             if key in initials and person != key),
            key,
        )
        for key in people
    }

    spellings = {
        person: sorted({
            identity["teacher_name"] for identity in identities
            if person_of(identity["teacher_name"]) == person
        })
        for person in people
    }
    spellings = {person: names for person, names in spellings.items() if names}

    if not spellings:
        return {
            "error": (
                f"No verified timetable records found "
                f"for {teacher_name}."
            )
        }

    if len(spellings) > 1:
        return {
            "error": "Multiple teachers match your query.",
            "matching_teachers": sorted(names[0] for names in spellings.values()),
            "instruction": "Ask the user to specify the teacher."
        }

    person, names = next(iter(spellings.items()))
    initials = initials_by_person.get(person, set())
    aliases = set(names) | {
        identity["teacher_name"] for identity in identities
        if _compact(identity["teacher_name"]) in initials
    } | {value.upper() for value in initials}
    canonical = aliases_map.get(person_key(names[0]))
    return {
        "teacher_name": canonical or max(names, key=len),
        "aliases": sorted(aliases),
    }


def resolve_room(room: str):
    """Match 'lhc301' or 'LHC-301' to the stored room name 'LHC 301'."""
    if not room or not room.strip():
        return {"error": "Please provide a room name."}
    query = _compact(room)
    # One room per key: "CSE LAB1" and "CSE LAB 1" are the same lab.
    spellings = {}
    for name in get_verified_rooms():
        spellings.setdefault(_compact(name), set()).add(name)
    keys = [key for key in spellings if key == query] or [
        key for key in spellings if query in key
    ]
    if not keys:
        return {"error": f"No verified timetable records use room {room}."}
    if len(keys) > 1:
        return {
            "error": "Multiple rooms match your query.",
            "matching_rooms": sorted(max(spellings[key], key=len) for key in keys),
            "instruction": "Ask the user to specify the room.",
        }
    names = sorted(spellings[keys[0]])
    return {"room": max(names, key=len), "aliases": names}


def resolve_class(class_name=None, semester=None, section=None):
    """Turn 'CSE 3A', '3A', 'Semester 3 Section A' into a stored class name."""
    classes = get_verified_classes()
    if class_name:
        query = _compact(class_name)
        for item in classes:
            if _compact(item["class_name"]) == query:
                return {"class_name": item["class_name"]}
        match = re.search(
            r"(\d)\s*(?:sec(?:tion)?)?\s*([a-z])\s*$",
            class_name.strip(),
            re.I,
        )
        if match:
            semester = int(match.group(1))
            section = match.group(2).upper()
    if semester is not None and section:
        for item in classes:
            if item["semester"] == int(semester) and str(
                item["section"] or ""
            ).upper() == str(section).upper():
                return {"class_name": item["class_name"]}
    if class_name or (semester is not None and section):
        return {
            "error": "No verified timetable found for that class.",
            "known_classes": [item["class_name"] for item in classes],
        }
    return {"error": "Please provide a class name or semester and section."}


def _check_day_time(day, time_value=None):
    """Normalize 'mon'/'MONDAY' and '2:40 PM'/'14.40'; report unreadable input."""
    normalized_day = normalize_day(day)
    if normalized_day is None:
        return None, None, {"error": f"Could not understand the day '{day}'."}
    if time_value is None:
        return normalized_day, None, None
    normalized_time, _ = normalize_clock(time_value)
    if normalized_time is None:
        return normalized_day, None, {
            "error": f"Could not understand the time '{time_value}'. Use e.g. 10:30."
        }
    return normalized_day, normalized_time, None


def _free_time_layout(day, class_name=None):
    """Working hours and breaks for free-slot tools, or a note when the
    approved data has nothing on that day (unknown is not "free")."""
    layout = get_day_layout(day, class_name)
    if layout is None:
        return {
            "note": (
                f"No approved timetable records on {day}"
                + (f" for class {class_name}" if class_name else "")
                + ", so free time cannot be determined."
            )
        }
    return layout


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
    day, _, error = _check_day_time(day)
    if error:
        return error

    return find_teacher_schedule(
        resolved["aliases"],
        day,
        semester=semester,
        section=section
    )


def get_teacher_location(
    teacher_name: str,
    day: str,
    time: str
):
    """Find a teacher's scheduled class at a particular time (e.g. 10:30)."""

    resolved = resolve_teacher_name(teacher_name)
    if "error" in resolved:
        return resolved
    day, time, error = _check_day_time(day, time)
    if error:
        return error

    return find_teacher_at_time(
        resolved["aliases"],
        day,
        time
    )


def get_class_timetable(
    class_name: str = None,
    day: str = None,
    semester: int = None,
    section: str = None,
):
    """Get a verified class timetable for a day, using a class name such as
    '3A' or a semester and section."""

    if not day:
        return {"error": "Please provide a day."}
    day, _, error = _check_day_time(day)
    if error:
        return error
    resolved = resolve_class(class_name, semester, section)
    if "error" in resolved:
        return resolved

    return find_class_schedule(class_name=resolved["class_name"], day=day)


def get_class_free_slots(
    day: str,
    class_name: str = None,
    semester: int = None,
    section: str = None,
    min_duration_minutes: int = 30,
):
    """Find periods when a class has nothing scheduled in verified records."""

    day, _, error = _check_day_time(day)
    if error:
        return error
    resolved = resolve_class(class_name, semester, section)
    if "error" in resolved:
        return resolved

    layout = _free_time_layout(day, resolved["class_name"])
    if "note" in layout:
        return layout
    lateral = [
        entry for entry in find_class_schedule(
            class_name=resolved["class_name"], day=day,
        )
        if entry.get("entry_type") == "lateral_entry"
    ]
    result = {
        "class_name": resolved["class_name"],
        "day": day,
        "free_periods": find_section_free_slots(
            resolved["class_name"],
            day,
            min_duration_minutes=min_duration_minutes,
            **layout,
        ),
    }
    if lateral:
        result["lateral_entry_sessions"] = [
            {
                "start_time": entry["start_time"],
                "end_time": entry["end_time"],
                "subject": entry.get("subject"),
                "room": entry.get("room"),
            }
            for entry in lateral
        ]
        result["note"] = (
            "Lateral-entry sessions are only for diploma (lateral-entry) "
            "students, so the rest of the class is free then."
        )
    return result


def get_room_schedule(
    room: str,
    day: str,
    time: str
):
    """Find which class is using a room at a particular time (e.g. 11:00)."""

    resolved = resolve_room(room)
    if "error" in resolved:
        return resolved
    day, time, error = _check_day_time(day, time)
    if error:
        return error

    return find_room_at_time(resolved["aliases"], day, time)


def get_room_free_slots(
    room: str,
    day: str,
    min_duration_minutes: int = 30,
):
    """Find when a room or lab is free on a day, checked against every
    class that uses it in all approved timetables."""

    resolved = resolve_room(room)
    if "error" in resolved:
        return resolved
    day, _, error = _check_day_time(day)
    if error:
        return error
    layout = _free_time_layout(day)
    if "note" in layout:
        return layout

    aliases = resolved["aliases"]
    busy = find_room_bookings(aliases, day)
    week_classes = sorted({
        entry["class_name"]
        for other_day in DAY_NAMES
        for entry in find_room_bookings(aliases, other_day)
        if entry.get("class_name")
    })
    return {
        "room": resolved["room"],
        "day": day,
        "free_periods": find_room_free_slots(
            aliases,
            day,
            min_duration_minutes=min_duration_minutes,
            **layout,
        ),
        "busy_periods": [
            {
                "start_time": entry["start_time"],
                "end_time": entry["end_time"],
                "class_name": entry.get("class_name"),
                "subject": entry.get("subject"),
                "teacher": entry.get("teacher_name"),
                "group": entry.get("subgroup"),
            }
            for entry in busy
        ],
        "classes_checked": week_classes,
        "note": (
            "Checked against every approved timetable that uses this room. "
            "Classes whose timetables are not uploaded or approved yet are "
            "not included."
        ),
    }


def get_teacher_free_slots(
    teacher_name: str,
    day: str,
    min_duration_minutes: int = 30,
):
    """Find when a teacher is free on a day, checked against every class
    they teach in all approved timetables (not just one class)."""

    resolved = resolve_teacher_name(teacher_name)
    if "error" in resolved:
        return resolved
    day, _, error = _check_day_time(day)
    if error:
        return error
    layout = _free_time_layout(day)
    if "note" in layout:
        return layout

    aliases = resolved["aliases"]
    busy = find_teacher_schedule(aliases, day)
    week_classes = sorted({
        entry["class_name"]
        for other_day in DAY_NAMES
        for entry in find_teacher_schedule(aliases, other_day)
        if entry.get("class_name")
    })
    return {
        "teacher": resolved["teacher_name"],
        "day": day,
        "free_periods": find_free_slots(
            aliases,
            day,
            min_duration_minutes=min_duration_minutes,
            **layout,
        ),
        "busy_periods": [
            {
                "start_time": entry["start_time"],
                "end_time": entry["end_time"],
                "class_name": entry.get("class_name"),
                "subject": entry.get("subject"),
                "room": entry.get("room"),
            }
            for entry in sorted(busy, key=lambda item: item["start_time"])
        ],
        "classes_checked": week_classes,
        "note": (
            "Checked against every approved timetable this teacher appears "
            "in. Classes whose timetables are not uploaded or approved yet "
            "are not included."
        ),
    }


def get_timetable_conflicts(day: str = None):
    """Detect conflicts in verified timetable records."""

    if day:
        day, _, error = _check_day_time(day)
        if error:
            return error
    return detect_conflicts(day=day)


def list_known_timetable_data():
    """List the teachers, rooms and classes that have verified records."""

    # One entry per person, even when timetables spell the name differently.
    aliases_map = get_teacher_aliases()
    people = {}
    for identity in get_verified_teacher_identities():
        name = aliases_map.get(
            person_key(identity["teacher_name"]), identity["teacher_name"]
        )
        key = _name_key(name)
        if len(name) > len(people.get(key, "")):
            people[key] = name
    return {
        "teachers": sorted(people.values()),
        "rooms": sorted({
            _compact(name): name
            for name in sorted(get_verified_rooms(), key=len)
        }.values()),
        "classes": [item["class_name"] for item in get_verified_classes()],
    }


# --------------------------------------------------
# 6. Give Gemini access to Python functions
# --------------------------------------------------

TOOLS = [
    get_teacher_schedule,
    get_teacher_location,
    get_class_timetable,
    get_class_free_slots,
    get_room_schedule,
    get_room_free_slots,
    get_teacher_free_slots,
    get_timetable_conflicts,
    list_known_timetable_data,
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
- Room occupancy and free rooms
- Teacher and class free periods
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
   A teacher's or room's free time is checked across all classes that
   use it; mention which classes were checked (classes_checked) and what
   it is busy with (busy_periods) when you answer.
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
18. Pass names, rooms and classes as the user wrote them; the tools
    match "Dr. Rao", "rao" or initials, "lhc301", and "CSE 3A".
19. If a name, room or class is not found, call
    list_known_timetable_data and suggest the closest known values.
20. If the user gives no day for a day-based question, ask which day.
21. Entries with a subgroup (e.g. "Section 2", "S1") apply only to
    that group of students; say so when you list them.
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
            "Sorry, I couldn't check the timetable: "
            f"{describe_gemini_error(error)}"
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
