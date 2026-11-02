
import json
import os
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
# SMARTTIMETABLE_DB_PATH lets tests and tooling use an isolated database.
DB_PATH = Path(
    os.getenv("SMARTTIMETABLE_DB_PATH") or DATA_DIR / "timetable.db"
)
_TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2})")
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
EXTRACTION_METADATA_COLUMNS = {
    "entry_type": "TEXT",
    "subgroup": "TEXT",
    "parallel_group": "TEXT",
    "course_abbrev": "TEXT",
    "source_bbox": "TEXT",
    "evidence_json": "TEXT",
    "resolution_method": "TEXT",
    "review_tier": "TEXT",
}


def normalize_missing(value):
    """Normalize common OCR placeholders to SQL NULL."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value.casefold() in {"unknown", "none", "null"}:
            return None
    return value


def normalize_time(value):
    """Return a time as zero-padded 24-hour HH:MM, e.g. 9:00 -> 09:00."""
    value = normalize_missing(value)
    if value is None:
        return None

    match = _TIME_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Invalid time: {value}. Use HH:MM.")

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {value}. Use 24-hour HH:MM.")

    return f"{hour:02d}:{minute:02d}"


def _serialize_metadata(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def get_connection():
    """Create a connection to the timetable database."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
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

    # Older databases may not yet have columns referenced by schema indexes.
    if DB_PATH.exists():
        with get_connection() as connection:
            table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'timetable_entries'
                """
            ).fetchone()
        if table_exists:
            migrate_timetable_metadata()

    with get_connection() as connection:
        connection.executescript(schema)

    migrate_timetable_metadata()
    migrate_optional_timetable_metadata()
    migrate_extraction_metadata()
    migrate_timetable_registry()


def migrate_timetable_registry():
    """Add the file fingerprint and day-layout columns to old databases."""
    with get_connection() as connection:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(timetables)")
        }
        for column in ("file_sha256", "layout_json"):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE timetables ADD COLUMN {column} TEXT"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_timetables_file_sha256
            ON timetables (file_sha256)
            """
        )


def insert_entry(entry, connection=None):
    """Insert a normalized timetable entry and return its ID."""
    entry = dict(entry)
    for field in (
        "teacher_name", "subject", "class_name", "day",
        "start_time", "end_time", "room", "semester", "section",
        "timetable_id", "entry_type", "subgroup", "parallel_group",
        "course_abbrev", "source_bbox", "evidence_json",
        "resolution_method", "review_tier",
    ):
        entry[field] = normalize_missing(entry.get(field))

    # Zero-pad times so the schema's string CHECK (start_time < end_time)
    # agrees with numeric order.
    entry["start_time"] = normalize_time(entry.get("start_time"))
    entry["end_time"] = normalize_time(entry.get("end_time"))

    entry["subject"] =entry.get("subject") or normalize_missing(
        entry.get("course")
    )
    entry["class_name"] = entry.get("class_name") or derive_class_name(
        entry.get("semester"), entry.get("section")
    )
    entry["entry_type"] = entry.get("entry_type") or "class"
    entry["review_status"] = (
        normalize_missing(entry.get("review_status")) or "needs_review"
    )
    entry["course_abbrev"] = (
        entry.get("course_abbrev")
        or normalize_missing(entry.get("abbrev"))
    )
    entry["source_bbox"] = _serialize_metadata(
        entry.get("source_bbox")
    )
    entry["evidence_json"] = _serialize_metadata(
        entry.get("evidence_json")
        if entry.get("evidence_json") is not None
        else entry.get("evidence")
    )

    entry_teachers = entry.get("teachers")
    if isinstance(entry_teachers, (str, dict)):
        entry_teachers = [entry_teachers]
    elif entry_teachers is not None:
        entry_teachers = list(entry_teachers)
    required_fields = [
        "subject", "class_name", "day", "start_time", "end_time",
    ]

    for field in required_fields:
        if not entry.get(field):
            raise ValueError(f"Missing required field: {field}")

    if str(entry["entry_type"]).casefold() not in {"activity", "break"}:
        if not entry.get("teacher_name") and not entry_teachers:
            raise ValueError("Missing required field: teacher_name")
        if not entry.get("room"):
            raise ValueError("Missing required field: room")

    if not entry.get("teacher_name") and entry_teachers:
        first_teacher = next(iter(entry_teachers), None)
        if isinstance(first_teacher, dict):
            entry["teacher_name"] = normalize_missing(
                first_teacher.get("teacher_name")
                or first_teacher.get("name")
                or first_teacher.get("initials")
            )
        else:
            entry["teacher_name"] = normalize_missing(first_teacher)

    if entry.get("teacher_name") and entry_teachers is not None:
        primary = str(entry["teacher_name"]).casefold()
        associated_names = {
            str(
                teacher.get("teacher_name")
                or teacher.get("name")
                or teacher.get("initials")
                or ""
            ).strip().casefold()
            if isinstance(teacher, dict)
            else str(teacher).strip().casefold()
            for teacher in entry_teachers
        }
        if primary not in associated_names:
            entry_teachers.insert(0, entry["teacher_name"])

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
        "entry_type", "subgroup", "parallel_group", "course_abbrev",
        "source_bbox", "evidence_json", "resolution_method", "review_tier",
    ]

    values = [
        entry.get(column)
        for column in columns
    ]

    def insert(connection):
        cursor = connection.execute(
            f"""
            INSERT INTO timetable_entries ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            """,
            values,
        )
        association_values = entry_teachers or (
            [entry["teacher_name"]]
            if entry.get("teacher_name")
            else []
        )
        insert_entry_teachers(
            cursor.lastrowid,
            association_values,
            connection=connection,
        )
        return cursor.lastrowid

    if connection is not None:
        return insert(connection)

    with get_connection() as connection:
        return insert(connection)


def derive_class_name(semester, section):
    semester = normalize_missing(semester)
    section = normalize_missing(section)
    if semester is None or section is None:
        return None
    return f"Semester {semester} Section {section}"


def insert_entry_teachers(entry_id, teachers, connection=None):
    """Insert unique teacher associations for a timetable entry."""
    if teachers is None:
        return 0
    if isinstance(teachers, (str, dict)):
        teachers = [teachers]

    associations = []
    seen = set()
    for teacher in teachers:
        if isinstance(teacher, dict):
            name = normalize_missing(
                teacher.get("teacher_name")
                or teacher.get("name")
                or teacher.get("initials")
            )
            initials = normalize_missing(teacher.get("initials"))
            role = normalize_missing(teacher.get("role"))
        else:
            name = normalize_missing(teacher)
            initials = None
            role = None

        if name is None:
            continue

        initials = initials or ""
        role = role or ""
        key = (name.casefold(), initials.casefold(), role.casefold())
        if key in seen:
            continue
        seen.add(key)
        associations.append((entry_id, name, initials, role))

    def insert(connection):
        inserted = 0
        for association in associations:
            existing = connection.execute(
                """
                SELECT 1 FROM entry_teachers
                WHERE entry_id = ?
                  AND LOWER(teacher_name) = LOWER(?)
                  AND LOWER(initials) = LOWER(?)
                  AND LOWER(role) = LOWER(?)
                """,
                association,
            ).fetchone()
            if existing:
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO entry_teachers (
                    entry_id, teacher_name, initials, role
                )
                VALUES (?, ?, ?, ?)
                """,
                association,
            )
            inserted += cursor.rowcount
        return inserted

    if connection is not None:
        return insert(connection)
    with get_connection() as connection:
        return insert(connection)


def insert_course_offerings(timetable_id, offerings, connection=None):
    """Persist course/faculty offerings associated with a timetable."""
    timetable_id = normalize_missing(timetable_id)
    if timetable_id is None:
        raise ValueError("timetable_id is required")
    if isinstance(offerings, dict):
        offerings = [offerings]
    offerings = list(offerings or [])

    columns = (
        "timetable_id", "course_name", "abbrev", "kind", "section_label",
        "faculty_name", "faculty_initials", "room", "source_bbox",
    )

    def insert(connection):
        offering_ids = []
        for offering in offerings:
            values = (
                timetable_id,
                normalize_missing(
                    offering.get("course_name") or offering.get("course")
                ),
                normalize_missing(
                    offering.get("abbrev") or offering.get("course_abbrev")
                ),
                normalize_missing(offering.get("kind")),
                normalize_missing(
                    offering.get("section_label") or offering.get("section")
                ),
                normalize_missing(offering.get("faculty_name")),
                normalize_missing(
                    offering.get("faculty_initials")
                    or offering.get("initials")
                ),
                normalize_missing(offering.get("room")),
                _serialize_metadata(offering.get("source_bbox")),
            )
            cursor = connection.execute(
                f"""
                INSERT INTO timetable_courses ({", ".join(columns)})
                VALUES ({", ".join("?" for _ in columns)})
                """,
                values,
            )
            offering_ids.append(cursor.lastrowid)
        return offering_ids

    if connection is not None:
        return insert(connection)
    with get_connection() as connection:
        return insert(connection)


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
    file_sha256=None,
):
    """Register a newly uploaded timetable document."""
    if not timetable_id or not original_filename or not stored_path:
        raise ValueError("Timetable ID, filename and path are required.")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO timetables (
                timetable_id, original_filename, stored_path,
                semester, section, academic_year, term, status,
                file_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timetable_id, original_filename, str(stored_path),
                semester, section, academic_year, term, status,
                file_sha256,
            ),
        )

    return timetable_id


def find_timetables_by_hash(file_sha256):
    """Earlier uploads of the same file (not failed ones), newest first."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM timetables
            WHERE file_sha256 = ? AND status <> 'failed'
            ORDER BY uploaded_at DESC, id DESC
            """,
            (file_sha256,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_timetable_layout(timetable_id, layout):
    """Store a timetable's day bounds and breaks (from its period header)."""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE timetables
            SET layout_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE timetable_id = ?
            """,
            (json.dumps(layout), timetable_id),
        )


def get_timetable_overview():
    """Every registered timetable with its saved and pending record counts."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                timetable.timetable_id,
                timetable.original_filename,
                timetable.semester,
                timetable.section,
                timetable.status,
                timetable.uploaded_at,
                SUM(entry.review_status = 'verified') AS verified_count,
                SUM(entry.review_status = 'needs_review') AS unreviewed_count,
                pending.payload_json
            FROM timetables AS timetable
            LEFT JOIN timetable_entries AS entry
              ON entry.timetable_id = timetable.timetable_id
            LEFT JOIN pending_reviews AS pending
              ON pending.timetable_id = timetable.timetable_id
            GROUP BY timetable.timetable_id
            ORDER BY timetable.uploaded_at DESC, timetable.id DESC
            """
        ).fetchall()

    overview = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop("payload_json") or "{}")
        item["verified_count"] = item["verified_count"] or 0
        item["unreviewed_count"] = item["unreviewed_count"] or 0
        item["pending_count"] = len(payload.get("records") or [])
        overview.append(item)
    return overview


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


def update_timetable_metadata(timetable_id, semester=None, section=None):
    """Fill in semester/section detected after upload (never overwrites)."""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE timetables
            SET semester = COALESCE(semester, ?),
                section = COALESCE(section, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE timetable_id = ?
            """,
            (semester, section, timetable_id),
        )


def save_pending_review(timetable_id, payload):
    """Store records awaiting review; an empty payload removes the entry."""
    with get_connection() as connection:
        if not payload or (
            not payload.get("records") and not payload.get("unresolved_items")
        ):
            connection.execute(
                "DELETE FROM pending_reviews WHERE timetable_id = ?",
                (timetable_id,),
            )
            return
        connection.execute(
            """
            INSERT INTO pending_reviews (timetable_id, payload_json)
            VALUES (?, ?)
            ON CONFLICT (timetable_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (timetable_id, json.dumps(payload, ensure_ascii=False, default=str)),
        )


def get_pending_reviews():
    """Return pending reviews, newest first, with their timetable details."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT pending.timetable_id, pending.payload_json,
                   pending.updated_at, timetable.original_filename
            FROM pending_reviews AS pending
            LEFT JOIN timetables AS timetable
              ON timetable.timetable_id = pending.timetable_id
            ORDER BY pending.updated_at DESC, pending.rowid DESC
            """
        ).fetchall()

    return [
        {
            "timetable_id": row["timetable_id"],
            "original_filename": row["original_filename"],
            "updated_at": row["updated_at"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def delete_timetable(timetable_id):
    """Delete a timetable and its associated entries atomically."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        cursor = connection.execute(
            "DELETE FROM timetable_entries WHERE timetable_id = ?",
            (timetable_id,),
        )

        connection.execute(
            "DELETE FROM timetable_courses WHERE timetable_id = ?",
            (timetable_id,),
        )
        connection.execute(
            "DELETE FROM pending_reviews WHERE timetable_id = ?",
            (timetable_id,),
        )
        registry_cursor = connection.execute(
            "DELETE FROM timetables WHERE timetable_id = ?",
            (timetable_id,),
        )

        if registry_cursor.rowcount == 0:
            raise ValueError(f"Timetable not found: {timetable_id}")

def migrate_optional_timetable_metadata():
    """Make metadata, teacher, and room columns nullable on old databases."""
    with get_connection() as connection:
        columns = connection.execute(
            "PRAGMA table_info(timetable_entries)"
        ).fetchall()

        not_null_columns = {
            row["name"]
            for row in columns
            if row["notnull"]
        }
        nullable_columns = {
            "semester", "section", "timetable_id",
            "teacher_name", "room",
        } & not_null_columns

        if not nullable_columns:
            return

        _rebuild_timetable_entries(connection, nullable_columns)


def _split_table_definitions(definitions):
    """Split a CREATE TABLE body on commas outside nested SQL expressions."""
    parts = []
    start = 0
    depth = 0
    quote = None
    index = 0

    while index < len(definitions):
        char = definitions[index]
        if quote:
            if char == quote:
                if index + 1 < len(definitions) and definitions[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "[":
            quote = "]"
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(definitions[start:index].strip())
            start = index + 1
        index += 1

    final_part = definitions[start:].strip()
    if final_part:
        parts.append(final_part)
    return parts


def _rebuild_timetable_entries(connection, make_nullable):
    """Rebuild the table while retaining its SQL-defined schema and objects."""
    create_row = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'timetable_entries'
        """
    ).fetchone()
    if not create_row or not create_row["sql"]:
        raise RuntimeError(
            "Cannot safely rebuild timetable_entries without its CREATE SQL."
        )

    original_sql = create_row["sql"]
    create_match = re.search(
        r"(?is)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:main\s*\.\s*)?"
        r'(?:"timetable_entries"|`timetable_entries`|'
        r"\[timetable_entries\]|timetable_entries)(?=\s|\()",
        original_sql,
    )
    if not create_match:
        raise RuntimeError(
            "Cannot safely identify the timetable_entries table definition."
        )

    open_paren = original_sql.find("(", create_match.end())
    if open_paren < 0:
        raise RuntimeError("Invalid timetable_entries CREATE statement.")

    depth = 0
    quote = None
    close_paren = None
    index = open_paren
    while index < len(original_sql):
        char = original_sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(original_sql) and original_sql[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "[":
            quote = "]"
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_paren = index
                break
        index += 1

    if close_paren is None:
        raise RuntimeError("Unbalanced timetable_entries CREATE statement.")

    definitions = _split_table_definitions(
        original_sql[open_paren + 1:close_paren]
    )
    updated_definitions = []
    for definition in definitions:
        identifier = re.match(
            r'\s*(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_]\w*))',
            definition,
        )
        if identifier:
            column_name = next(
                item for item in identifier.groups() if item is not None
            )
            if column_name in make_nullable:
                definition = re.sub(
                    r"\s+NOT\s+NULL\b",
                    "",
                    definition,
                    count=1,
                    flags=re.IGNORECASE,
                )
        updated_definitions.append(definition)

    suffix = original_sql[close_paren + 1:]
    new_prefix = re.sub(
        r"(?is)(\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:main\s*\.\s*)?)"
        r'(?:"timetable_entries"|`timetable_entries`|'
        r"\[timetable_entries\]|timetable_entries)(?=\s|\()",
        r"\1timetable_entries_new",
        original_sql[:open_paren],
        count=1,
    )
    new_sql = (
        new_prefix
        + "("
        + ",\n".join(updated_definitions)
        + ")"
        + suffix
    )

    existing_temp = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'timetable_entries_new'
        """
    ).fetchone()
    if existing_temp:
        raise RuntimeError(
            "A stale timetable_entries_new table exists; refusing to overwrite it."
        )

    preserved_objects = connection.execute(
        """
        SELECT type, sql FROM sqlite_master
        WHERE tbl_name = 'timetable_entries'
          AND type IN ('index', 'trigger')
          AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    old_columns = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_xinfo(timetable_entries)"
        ).fetchall()
        if row["hidden"] == 0
    ]

    previous_foreign_keys = connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]
    previous_legacy_alter = connection.execute(
        "PRAGMA legacy_alter_table"
    ).fetchone()[0]
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(new_sql)
        quoted_columns = ", ".join(
            '"' + column.replace('"', '""') + '"'
            for column in old_columns
        )
        connection.execute(
            f"""
            INSERT INTO timetable_entries_new ({quoted_columns})
            SELECT {quoted_columns} FROM timetable_entries
            """
        )
        connection.execute("DROP TABLE timetable_entries")
        connection.execute(
            "ALTER TABLE timetable_entries_new RENAME TO timetable_entries"
        )
        for item in preserved_objects:
            connection.execute(item["sql"])

        foreign_key_issues = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_issues:
            raise sqlite3.IntegrityError(
                "Foreign-key validation failed while rebuilding "
                "timetable_entries."
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(
            f"PRAGMA legacy_alter_table = {int(previous_legacy_alter)}"
        )
        connection.execute(
            f"PRAGMA foreign_keys = {int(previous_foreign_keys)}"
        )


def migrate_extraction_metadata():
    """Add extraction provenance columns and indexes to timetable entries."""
    with get_connection() as connection:
        existing = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(timetable_entries)"
            ).fetchall()
        }
        if not existing:
            return

        for column, column_type in EXTRACTION_METADATA_COLUMNS.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE timetable_entries "
                    f"ADD COLUMN {column} {column_type}"
                )

        for column in (
            "entry_type", "subgroup", "parallel_group",
            "course_abbrev", "review_tier",
        ):
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_timetable_entries_{column}
                ON timetable_entries ({column})
                """
            )
