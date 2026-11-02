"""Read timetable pages with a vision model into structured JSON.

The vision client is injected so the module does not depend on a provider:

    vision_client(image_bytes, mime_type, prompt, response_schema)
        -> JSON string or dict matching ``TimetablePage``
"""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SUPPORTED_SUFFIXES = tuple(IMAGE_MIME_TYPES) + (".pdf",)
PDF_RENDER_ZOOM = 2


class Teacher(BaseModel):
    name: Optional[str]
    initials: Optional[str]


class Slot(BaseModel):
    day: str
    start_time: str
    end_time: str
    entry_type: str
    cell_text: str
    course_code: Optional[str]
    course_abbrev: Optional[str]
    course_name: Optional[str]
    teachers: list[Teacher]
    room: Optional[str]
    group: Optional[str]
    notes: Optional[str]


class SectionAssignment(BaseModel):
    section: Optional[str]
    teacher_name: Optional[str]
    initials: Optional[str]
    room: Optional[str]


class LegendRow(BaseModel):
    course_code: Optional[str]
    course_abbrev: Optional[str]
    course_name: str
    faculty: list[Teacher]
    section_assignments: list[SectionAssignment]


class Period(BaseModel):
    start_time: str
    end_time: str
    is_break: bool


class TimetablePage(BaseModel):
    institution: Optional[str]
    semester: Optional[str]
    section: Optional[str]
    default_room: Optional[str]
    academic_year: Optional[str]
    periods: list[Period] = []
    legend: list[LegendRow]
    slots: list[Slot]
    uncertainties: list[str]


EXTRACTION_PROMPT = """You are extracting a class timetable from the attached image.
Return every scheduled cell as one slot. Rules:
- Times are 24-hour HH:MM. Afternoon times written like 1.45 mean 13:45.
- A cell spanning several period columns is ONE slot from the first period's start to the last period's end.
- Use the period header times; never invent times. Break/lunch columns are not slots unless a cell spans them.
- Days may be rows or columns; read whichever the layout uses.
- periods: every period column/row of the time header in order, with is_break true for breaks and lunch (give a break its time span from the neighbouring periods if it has no times printed).
- entry_type: lecture, lab, tutorial, elective, activity (talks, meetings, partial delivery, projects), or other.
- cell_text: the exact text printed in the cell.
- Use the course/faculty table on the page, if there is one, to fill course_name, course_code and teachers. Map initials printed in a cell (e.g. AKK) to full names ONLY when the table shows that mapping; otherwise keep the initials with name null.
- room: room printed in the cell, else the timetable's default room for lectures; null for activities.
- If the legend splits a course by section (e.g. "SECTION 1 : X (LHC 206)"), record each in section_assignments.
- If a cell names several parallel courses (e.g. OOAD/BDA, NOSQL/PE), emit one slot PER LEGEND SECTION ASSIGNMENT of those courses, with group "Section N", that section's teacher, and that section's room.
- If a cell lists batches (S1, S2...), emit one slot per batch with group "S1" etc., using only that batch's line.
- Never guess. Put anything unclear in uncertainties."""


def render_pages(document_path):
    """Return ``[(page_number, image_bytes, mime_type)]`` for an image or PDF."""
    path = Path(document_path)
    if not path.is_file():
        raise FileNotFoundError(f"Timetable file not found: {path}")

    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type '{path.suffix}'. "
            f"Upload one of: {', '.join(SUPPORTED_SUFFIXES)}."
        )

    if suffix == ".pdf":
        return _render_pdf(path)

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError(f"Could not read image '{path.name}': {error}") from None

    return [(1, path.read_bytes(), IMAGE_MIME_TYPES[suffix])]


def _render_pdf(path):
    import pymupdf

    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise ValueError(f"Could not open PDF '{path.name}': {error}") from None

    with document:
        if document.page_count == 0:
            raise ValueError(f"The PDF '{path.name}' contains no pages.")
        pages = []
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(PDF_RENDER_ZOOM, PDF_RENDER_ZOOM),
                alpha=False,
            )
            pages.append((page_number, pixmap.tobytes("png"), "image/png"))
    return pages


def parse_response(response):
    """Parse and schema-check a vision response into a plain dict."""
    if isinstance(response, str):
        text = response.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        try:
            response = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"Vision model returned invalid JSON: {error}") from None

    if not isinstance(response, dict):
        raise ValueError("Vision model response must be a JSON object.")

    return TimetablePage.model_validate(response).model_dump()


def extract_page(image_bytes, mime_type, vision_client):
    """Run the vision model on one page and return the validated page dict."""
    if not callable(vision_client):
        raise TypeError("vision_client must be callable")
    response = vision_client(
        image_bytes,
        mime_type,
        EXTRACTION_PROMPT,
        TimetablePage,
    )
    return parse_response(response)
