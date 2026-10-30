
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "timetable.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_connection():
    """Create a connection to the timetable database."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate_timetable_metadata():
    """Safely add metadata columns to existing databases."""
    new_columns = {
        "semester": "INTEGER",
        "section": "TEXT",
        "timetable_id": "TEXT",
        "original_room_text": "TEXT",
    }

    with get_connection() as connection:
        existing = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(timetable_entries)"
            ).fetchall()
        }

        for column, column_type in new_columns.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE timetable_entries "
                    f"ADD COLUMN {column} {column_type}"
                )

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_timetable_id
            ON timetable_entries (timetable_id)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_semester_section_day
            ON timetable_entries (semester, section, day)
        """)


def migrate_room_metadata():
    """Keep the original room-metadata migration function available."""
    migrate_timetable_metadata()


def initialize_database():
    """Create tables, then migrate older databases."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with get_connection() as connection:
        connection.executescript(schema)

    migrate_timetable_metadata()
    migrate_optional_timetable_metadata()


def insert_entry(entry, connection=None):
    """Insert a normalized timetable entry and return its ID."""
    required_fields = [
        "teacher_name", "subject", "class_name", "day",
        "start_time", "end_time", "room",
    ]

    for field in required_fields:
        if not entry.get(field):
            raise ValueError(f"Missing required field: {field}")

    allowed_days = {
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    }

    if entry["day"] not in allowed_days:
        raise ValueError("Invalid day name")

    def to_minutes(value):
        try:
            hour_text, minute_text = value.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except (ValueError, AttributeError):
            raise ValueError(
                f"Invalid time: {value}. Use HH:MM."
            ) from None

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(
                f"Invalid time: {value}. Use 24-hour HH:MM."
            )

        return hour * 60 + minute

    if to_minutes(entry["start_time"]) >= to_minutes(entry["end_time"]):
        raise ValueError("start_time must be earlier than end_time")

    semester = entry.get("semester")
    if semester is not None and semester not in range(1, 9):
        raise ValueError("semester must be between 1 and 8")

    columns = [
        "teacher_name", "subject", "class_name", "day",
        "start_time", "end_time", "room",
        "source_document", "source_page", "review_status",
        "semester", "section", "timetable_id", "original_room_text",
    ]

    values = [entry.get(column) for column in columns]

    if connection is not None:
        cursor = connection.execute(
        f"""
        INSERT INTO timetable_entries ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        """,
       values,
    )
        return cursor.lastrowid

    with get_connection() as connection:
        cursor = connection.execute(
          f"""
          INSERT INTO timetable_entries ({", ".join(columns)})
          VALUES ({", ".join("?" for _ in columns)})
          """,
          values,
        )
        return cursor.lastrowid


def get_all_entries():
    """Return all timetable entries as dictionaries."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM timetable_entries ORDER BY day, start_time"
        ).fetchall()

    return [dict(row) for row in rows]

def register_timetable(
    timetable_id,
    original_filename,
    stored_path,
    semester=None,
    section=None,
    academic_year=None,
    term=None,
    status="uploaded",
):
    """Register a newly uploaded timetable document."""
    if not timetable_id or not original_filename or not stored_path:
        raise ValueError("Timetable ID, filename and path are required.")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO timetables (
                timetable_id, original_filename, stored_path,
                semester, section, academic_year, term, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timetable_id, original_filename, str(stored_path),
                semester, section, academic_year, term, status,
            ),
        )

    return timetable_id


def get_all_timetables(status=None):
    """Return registered timetables, optionally filtered by status."""
    with get_connection() as connection:
        if status:
            rows = connection.execute(
                """
                SELECT * FROM timetables
                WHERE status = ?
                ORDER BY uploaded_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM timetables ORDER BY uploaded_at DESC"
            ).fetchall()

    return [dict(row) for row in rows]


def get_timetable(timetable_id):
    """Retrieve one registered timetable."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM timetables WHERE timetable_id = ?",
            (timetable_id,),
        ).fetchone()

    return dict(row) if row else None


def update_timetable_status(timetable_id, status):
    """Update the processing status of a timetable."""
    allowed_statuses = {
        "uploaded", "processing", "needs_review", "ready", "failed"
    }

    if status not in allowed_statuses:
        raise ValueError("Invalid timetable status.")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE timetables
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE timetable_id = ?
            """,
            (status, timetable_id),
        )

        if cursor.rowcount == 0:
            raise ValueError(f"Timetable not found: {timetable_id}")


def delete_timetable(timetable_id):
    """Delete a timetable and its associated entries atomically."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        cursor = connection.execute(
            "DELETE FROM timetable_entries WHERE timetable_id = ?",
            (timetable_id,),
        )

        registry_cursor = connection.execute(
            "DELETE FROM timetables WHERE timetable_id = ?",
            (timetable_id,),
        )

        if registry_cursor.rowcount == 0:
            raise ValueError(f"Timetable not found: {timetable_id}")

def migrate_optional_timetable_metadata():
    """Make timetable metadata optional for standalone schedule entries."""
    with get_connection() as connection:
        columns = connection.execute(
            "PRAGMA table_info(timetable_entries)"
        ).fetchall()

        metadata = {
            row["name"]: row
            for row in columns
        }

        needs_migration = any(
            name in metadata and metadata[name]["notnull"]
            for name in ("semester", "section", "timetable_id")
        )

        if not needs_migration:
            return

        connection.execute("PRAGMA foreign_keys = OFF")

        connection.executescript("""
            CREATE TABLE timetable_entries_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                semester INTEGER,
                section TEXT,
                timetable_id TEXT,
                teacher_name TEXT NOT NULL,
                subject TEXT NOT NULL,
                class_name TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                room TEXT NOT NULL,
                original_room_text TEXT,
                source_document TEXT,
                source_page INTEGER,
                review_status TEXT NOT NULL DEFAULT 'verified',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (semester IS NULL OR semester IN (1,2,3,4,5,6,7,8)),
                CHECK (day IN (
                    'Monday','Tuesday','Wednesday','Thursday',
                    'Friday','Saturday','Sunday'
                )),
                CHECK (start_time < end_time),
                CHECK (review_status IN ('verified','needs_review'))
            );

            INSERT INTO timetable_entries_new
            SELECT
                id, semester, section, timetable_id,
                teacher_name, subject, class_name, day,
                start_time, end_time, room, original_room_text,
                source_document, source_page, review_status, created_at
            FROM timetable_entries;

            DROP TABLE timetable_entries;
            ALTER TABLE timetable_entries_new RENAME TO timetable_entries;

            CREATE INDEX IF NOT EXISTS idx_teacher_day
            ON timetable_entries (teacher_name, day);

            CREATE INDEX IF NOT EXISTS idx_class_day
            ON timetable_entries (semester, section, day);

            CREATE INDEX IF NOT EXISTS idx_room_day
            ON timetable_entries (room, day);

            CREATE INDEX IF NOT EXISTS idx_timetable_id
            ON timetable_entries (timetable_id);
        """)

        connection.execute("PRAGMA foreign_keys = ON")
