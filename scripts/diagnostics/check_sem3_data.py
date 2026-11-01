
import sqlite3
from pathlib import Path

db_path = Path("data/timetable.db")

with sqlite3.connect(db_path) as conn:
    conn.row_factory = sqlite3.Row

    query = """
        SELECT day, start_time, end_time, subject,
               teacher_name, room, review_status
        FROM timetable_entries
        WHERE timetable_id = ?
        ORDER BY
            CASE day
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
            END,
            start_time
    """

    rows = conn.execute(query, ("sem3_a_2026_odd",)).fetchall()

    print("Semester III-A records:", len(rows))
    print("-" * 70)

    for row in rows:
        print(
            f"{row['day']:10} "
            f"{row['start_time']}-{row['end_time']} | "
            f"{row['subject']} | {row['teacher_name']} | "
            f"{row['room']} | {row['review_status']}"
        )
