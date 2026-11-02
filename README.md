# Smart Timetable Assistant

A timetable management and intelligent scheduling system that uses OCR, GenAI-based document understanding, structured database storage, and deterministic scheduling.

## Project Structure

```text
SmartTimeTable/
├── app/
│   ├── chat/                  # Conversational interface integration
│   ├── database/              # SQLite database and retrieval
│   ├── ingestion/             # OCR, extraction, parsing and importing
│   └── scheduling/            # Timetable queries and scheduling engine
├── tests/                     # Automated tests
├── scripts/
│   ├── diagnostics/           # Development and extraction diagnostics
│   ├── manual_checks/         # Manual verification utilities
│   └── setup/                 # Timetable setup and import utilities
├── data/
│   └── uploads/               # Uploaded timetable documents
├── requirements.txt
└── README.md
```

## Features

### 1. Timetable Document Ingestion
- Image preprocessing and OCR.
- Word-level OCR evidence and coordinates.
- Timetable document interpretation using a provider-independent LLM interface.
- Structured timetable record extraction.
- Source document and confidence tracking.
- Review status for uncertain records.

### 2. Database Management
- SQLite-based timetable storage.
- Multiple timetable registration.
- Timetable retrieval and filtering.
- Teacher and class schedule retrieval.
- Source and review-status tracking.

### 3. Safe Timetable Importing
- Required-field validation.
- Explicit approval before database import.
- Protection against empty extraction replacing existing timetable data.
- Import result reporting.

### 4. Scheduling
- Teacher schedule retrieval.
- Class schedule retrieval.
- Room availability checks.
- Free-slot detection.
- Scheduling conflict detection.

## Installation

### Requirements
- Python 3.10 or later.
- Tesseract OCR.
- Git.

### Clone the repository

```powershell
git clone https://github.com/shrxya07/SmartTimeTable.git
cd SmartTimeTable
```

### Create a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Install Tesseract OCR

Install Tesseract OCR for Windows.

If it is not available on PATH, configure its executable using the `TESSERACT_CMD` environment variable.

Example:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## Database Initialization

Initialize the SQLite database using:

```powershell
python -c "from app.database.db import initialize_database; initialize_database()"
```

The application also initializes the database on startup.

## Configure Gemini (optional)

Create a `.env` file in the project root:

```text
GEMINI_API_KEY=your-key-here
# Optional: override the default model
GEMINI_MODEL=gemini-3.5-flash-lite
```

Without `GEMINI_API_KEY` the application still starts. The chat assistant
and the Gemini extraction fallback are disabled and the UI says so;
deterministic OCR extraction, review and saving keep working.

## Running the Application

From the project root, with the virtual environment activated, either
command works:

```powershell
streamlit run app/streamlit_app.py
```

```powershell
python -m streamlit run app/streamlit_app.py
```

Then open http://localhost:8501.

## Running Tests

Run the complete automated test suite:

```powershell
python -m pytest -q
```

## Backend Module Interfaces

### Document Processing

Module:

`app.ingestion.document_processor`

Main function:

```python
process_timetable_image(image_path)
```

Produces OCR text, word-level evidence, coordinates, and OCR confidence.

### GenAI Document Understanding

Module:

`app.ingestion.document_intelligence`

Main function:

```python
interpret_timetable_document(
    ocr_evidence,
    llm_client,
    metadata=None
)
```

The LLM client is injected by the caller. The module does not require a particular model provider.

Expected client interface:

```python
def llm_client(system_prompt, user_prompt):
    # Return a JSON string or dictionary
    ...
```

### Extraction Pipeline

Module:

`app.ingestion.extraction_pipeline`

Main function:

```python
run_extraction_pipeline(
    image_path,
    llm_client,
    metadata=None
)
```

Connects OCR processing with GenAI interpretation and preserves provenance.

### Import Pipeline

Module:

`app.ingestion.import_pipeline`

Main function:

```python
run_import_pipeline(
    image_path,
    llm_client,
    timetable_id,
    approved_record_indices=None,
    metadata=None
)
```

Only explicitly approved, valid records are passed to the database importer.

Unapproved records remain pending review.

### Database and Retrieval

Modules:

- `app.database.db`
- `app.database.retrieval`

These provide database initialization, timetable registration, record storage, and retrieval operations.

### Scheduling

Module:

`app.scheduling.engine`

Provides schedule retrieval, free-slot detection, room queries, and conflict detection.

## Integration Notes for the Chat/LLM Layer

The conversational layer should call the backend modules through Python interfaces.

Responsibilities of the conversational layer:
- Interpret user questions.
- Select the appropriate backend operation.
- Call retrieval and scheduling functions.
- Convert structured results into natural-language responses.
- Maintain conversational context.

The scheduling engine and database remain the source of truth for timetable availability and conflicts.

The backend document intelligence interface is independent of the LLM provider.

## Important Notes

- Extracted records should be reviewed before being approved for import.
- Model confidence alone does not establish that a timetable record is correct.
- Missing timetable coverage must not be interpreted as confirmed free time.
- Uploaded documents and local database files are excluded from Git.
- Tesseract OCR must be installed separately from Python dependencies.

## Development

Run tests before committing changes:

```powershell
python -m pytest -q
```

Use feature branches for development and integration.
# SmartTimeTable: reference-table matching patch

## What this patch changes

The timetable image contains two separate data regions: the weekly grid and a lower course/faculty reference table. The existing pipeline OCRs the full page, but its semantic interpretation does not explicitly structure and join the lower table. This patch adds that missing stage.

- `app/ingestion/reference_resolver.py` reads OCR words and bounding boxes, locates the `COURSE CODE / COURSE NAME / FACULTY` table, and parses course abbreviations, faculty aliases, section-specific assignments and rooms.
- `app/ingestion/extraction_pipeline.py` parses the reference table from full-page OCR and resolves extracted records against it after Gemini interpretation.
- `app/ingestion/document_intelligence.py` clarifies the LLM's responsibility: distinguish grid cells from reference rows, preserve subgroup identifiers, and avoid inventing missing assignments.
- `tests/test_reference_resolver.py` tests section-specific OOAD/BDA expansion and activity classification.

## Install

Copy the files in this patch into the matching locations in the project, replacing the two existing ingestion files with the patched versions. Keep the existing `app/ingestion/__init__.py` and other project files unchanged.

Run:

```bash
python -m unittest discover -s tests -p "test_reference_resolver.py" -v
```

Then run the existing test suite. No database schema migration is included; `section_group`, `teacher_alias`, `reference_matches`, `record_type` and `uncertainties` are added to the extracted record dictionaries for the review/import layer to persist if its schema supports them.

## Expected Semester 5 behavior

- `OOAD/BDA` expands to the section-specific rows supported by the reference table: OOAD sections 1–3 and BDA section 4, with their listed faculty and LHC rooms.
- `AEC NOSQL/PE` expands to PE sections 1–3 and NoSQL section 4 where the reference table supports those assignments.
- Explicit initials in lab text can be resolved when the same reference table contains a full-name/initial pair. Unknown initials remain unresolved.
- Industry Talk, Proctor-Student Meeting, College/Department Activities, Partial Delivery and Internship/Project are typed as activities rather than being treated as missing course rows.
- The resolver never marks a record verified. All output remains `needs_review` until independently approved.

## Important limitation

This is a conservative OCR-driven parser, not a universal layout model. Course codes and some labels in the supplied Semester 5 image are noisy, so the parser uses the table's spatial columns and visible row text rather than exact course-code matching. Any table that lacks the expected three-column header, or whose OCR does not preserve enough row structure, is returned as `found: false` or left unresolved. Inspect the returned `reference_table` in the extraction result during the first run before importing records.
