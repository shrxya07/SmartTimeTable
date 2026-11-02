# Smart Timetable Assistant

Upload a class timetable (PNG, JPEG, WebP or PDF), review what was read,
then ask questions in plain English: where a teacher is, when a class is
free, which class is in a room, and whether anything clashes.

## How it works

```text
Upload ─► Gemini vision reads each page into structured JSON
       ─► Validator (plain code) checks every field
       ─► You review and approve records
       ─► SQLite database ─► Chat assistant (Gemini + database tools)
```

1. **Extraction** (`app/ingestion/vision_extractor.py`): each page (PDF pages
   are rendered to images) goes to Gemini with a strict JSON schema: every
   timetable cell with day, times, course, teachers, room and batch/section,
   plus the page's course/faculty table.
2. **Validation** (`app/ingestion/timetable_validator.py`): days and times
   are normalized (`1.45` → `13:45`); teachers and courses must appear in
   the page's own course table; initials are expanded only when the table
   defines them; overlapping slots are flagged; and, when Tesseract is
   installed, the cell text must also appear in an OCR reading of the page.
   Records that pass every check are tier `auto`; the rest are `check`
   with the reason shown. Nothing is invented and nothing is approved here.
3. **Review** (Streamlit): records stay pending (saved in the database, so a
   restart does not lose them) until you select and confirm them.
4. **Questions**: the assistant answers only from approved records through
   database tools (`app/scheduling/engine.py`, `app/database/retrieval.py`).

## Project structure

```text
SmartTimeTable/
├── app/
│   ├── streamlit_app.py        # Web interface
│   ├── genai_assistant.py      # Gemini client, vision adapter, chat tools
│   ├── database/               # SQLite schema, storage, retrieval
│   ├── ingestion/              # Vision extraction, validation, OCR cross-check
│   └── scheduling/engine.py    # Schedules, free slots, rooms, conflicts
├── tests/
├── data/                       # timetable.db and uploads (not in Git)
├── requirements.txt
└── README.md
```

## Installation

Requirements: Python 3.10+, Git, and optionally Tesseract OCR (used only to
cross-check extracted text).

```powershell
git clone https://github.com/shrxya07/SmartTimeTable.git
cd SmartTimeTable
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If Tesseract is installed somewhere other than
`C:\Program Files\Tesseract-OCR\tesseract.exe` and is not on PATH:

```powershell
$env:TESSERACT_CMD = "C:\path\to\tesseract.exe"
```

## Configure Gemini

Create a `.env` file in the project root:

```text
GEMINI_API_KEY=your-key-here
# Optional overrides
GEMINI_VISION_MODEL=gemini-3.5-flash
GEMINI_MODEL=gemini-3.5-flash-lite
```

`GEMINI_VISION_MODEL` reads timetables; `GEMINI_MODEL` answers chat
questions. Without a key the app still starts, shows a warning, and lets you
browse and review saved data; uploading and chatting are disabled.

## Running the application

From the project root, with the virtual environment activated, either
command works:

```powershell
streamlit run app/streamlit_app.py
```

```powershell
python -m streamlit run app/streamlit_app.py
```

Then open http://localhost:8501. The database (`data/timetable.db`) is
created on first start.

Reading a timetable takes about 30-90 seconds per page.

## Running tests

```powershell
python -m pytest -q
```

Tests use temporary databases and a fake vision model, so they need no API
key and never touch `data/timetable.db`.

## Notes

- Extracted records are never approved automatically; `auto` only means
  every check passed.
- No timetable record means "not in the approved data", not "free".
- Uploaded documents and database files are excluded from Git.
