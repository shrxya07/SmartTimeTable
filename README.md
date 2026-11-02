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
   Names, initials, rooms and class names are matched loosely ("Dr. Rao",
   "lhc301", "CSE 3A"); free time skips breaks and uses each day's real hours.
5. **Generation** (`app/scheduling/generator.py`): builds a clash-free week
   for a class with a backtracking constraint solver. Hard constraints: no
   class, teacher or room double-booked (including against other classes'
   approved timetables), labs on consecutive periods that never cross a
   break, parallel sections/batches kept together, fixed activities and
   short days (e.g. Saturday) respected. It prefers one session per course
   per day and an even spread, and explains anything it cannot place. A
   generated timetable goes through the same review and approval.

## Pages

| Page | What it does |
|---|---|
| Chat Assistant | Plain-English questions answered from approved records |
| Upload Timetable | Image/PDF upload, extraction with progress, duplicate-file warning |
| Review Extracted Records | Edit, select and approve records; reasons shown for `check` rows |
| Schedules & Conflicts | Weekly grid per teacher/class/room, free periods, clash report |
| Generate Timetable | Constraint-based timetable generation, sent to review |
| Manage Timetables | All uploads with counts; view as a grid; delete |

## Project structure

```text
SmartTimeTable/
├── app/
│   ├── streamlit_app.py        # Web interface
│   ├── genai_assistant.py      # Gemini client, vision adapter, chat tools
│   ├── database/               # SQLite schema, storage, retrieval
│   ├── ingestion/              # Vision extraction, validation, OCR cross-check
│   ├── scheduling/             # Queries, free slots, conflicts, generator
│   └── ui/                     # Schedules, generator and management pages
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
GEMINI_VISION_THINKING=minimal
GEMINI_MODEL=gemini-3.5-flash-lite
```

`GEMINI_VISION_MODEL` reads timetables; `GEMINI_MODEL` answers chat
questions. `GEMINI_VISION_THINKING=minimal` matched full accuracy on the
sample timetables in about half the time; set it empty for the model default.

The free tier allows about 5 requests per minute per model. The app waits as
long as Gemini asks when that limit is hit, and reports a used-up daily
quota clearly instead of failing with a raw error. Without a key the app still starts, shows a warning, and lets you
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

Reading a timetable takes about 20-35 seconds per page.

Use the virtual environment's Python. If `streamlit` on your PATH belongs to
another installation (e.g. Anaconda), the app fails with
`cannot import name 'genai' from 'google'`; run
`.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py` instead.

## Running tests

```powershell
python -m pytest -q
```

Tests use temporary databases and a fake vision model, so they need no API
key and never touch `data/timetable.db`. A live accuracy check against a
hand-verified timetable calls the real API and runs only on request:

```powershell
$env:SMARTTIMETABLE_LIVE_TESTS = "1"; python -m pytest tests/test_live_extraction.py -v
```

## Measured accuracy

Checked slot by slot against the printed timetables (day, time, course,
teachers, room):

| Timetable | Slots correct |
|---|---|
| Ramaiah CSE Sem 3-A | 27/27 |
| Ramaiah CSE Sem 5-A (parallel elective sections, lab batches) | 40/40 |
| Ramaiah CSE Sem 7-C (handwritten room number) | 24/24 |
| Generated layout: days as columns, 12-hour times | 13/13 |
| Generated PDF: abbreviated days, initials legend | 14/14 |

The previous OCR/grid pipeline got about 15/27 on Sem 3-A and nothing on
Sem 7-C.

## Notes

- Extracted records are never approved automatically; `auto` only means
  every check passed.
- No timetable record means "not in the approved data", not "free".
- Uploaded documents and database files are excluded from Git.
