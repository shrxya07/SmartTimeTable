
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
          INSERT INTO timetable_entries ({", ".join("?" for _ in columns)})
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
