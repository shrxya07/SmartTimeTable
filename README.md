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
