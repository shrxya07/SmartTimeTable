
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.database.db import DB_PATH
from app.ingestion.cell_extractor import extract_cells
from app.ingestion.record_parser import parse_timetable_cells
from app.ingestion.importer import import_timetable_records


TIMETABLE_ID = "sem3_a_2026_odd"


def main():
    db_path = Path(DB_PATH)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    # Back up the database before importing.
    backup_path = db_path.with_name(
        f"timetable_backup_before_import_{datetime.now():%Y%m%d_%H%M%S}.db"
    )
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")

    cells = extract_cells()
    records = parse_timetable_cells(
        cells,
        semester=3,
        section="A",
        timetable_id=TIMETABLE_ID,
        default_room="LHC 301",
        source_document="cse_sem3_a.png",
    )

    print(f"Prepared records: {len(records)}")

    if len(records) != 27:
        raise RuntimeError("Expected 27 records. Import cancelled.")

    # Keep every record flagged for review until manually validated.
    for record in records:
        record["review_status"] = "needs_review"

    results = import_timetable_records(
        records,
        default_room="LHC 301",
        semester=3,
        section="A",
        timetable_id=TIMETABLE_ID,
        source_document="cse_sem3_a.png",
    )

    for result in results:
        print(result)

    imported = sum(r.get("status") == "imported" for r in results)
    needs_review = sum(r.get("status") == "needs_review" for r in results)

    print("\nImport summary:")
    print("Imported:", imported)
    print("Needs review:", needs_review)

    # Verify the final database state.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        timetable_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM timetable_entries WHERE timetable_id = ?",
            (TIMETABLE_ID,),
        ).fetchone()["n"]

        unrelated_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM timetable_entries WHERE timetable_id IS NULL"
        ).fetchone()["n"]

        print("\nDatabase verification:")
        print("Semester III-A rows:", timetable_rows)
        print("Unrelated rows preserved:", unrelated_rows)

        if timetable_rows != 27 or unrelated_rows != 2:
            raise RuntimeError(
                "Verification failed. Check the import results and database backup."
            )

        print("All 27 records are present; unrelated rows are preserved.")


if __name__ == "__main__":
    main()
