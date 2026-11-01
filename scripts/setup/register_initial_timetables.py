
from app.database.db import (
    initialize_database,
    register_timetable,
    get_timetable,
)
from app.ingestion.timetable_registry import get_available_timetables

initialize_database()

timetables = get_available_timetables()

registered = 0
skipped = 0

for item in timetables:
    timetable_id = item["timetable_id"]

    # Avoid duplicate registrations.
    if get_timetable(timetable_id):
        print(f"Already registered: {timetable_id}")
        skipped += 1
        continue

    register_timetable(
        timetable_id=timetable_id,
        original_filename=item["image"],
        stored_path=str(item["image_path"]),
        semester=item["semester"],
        section=item["section"],
        academic_year="2026",
        term="Odd",
        status="needs_review",
    )

    print(f"Registered: {timetable_id}")
    registered += 1

print("\nRegistration complete!")
print("Newly registered:", registered)
print("Already registered:", skipped)
print("Total available:", len(timetables))
