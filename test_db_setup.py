
from app.database.db import (
    initialize_database,
    insert_entry,
    get_all_entries,
)

initialize_database()

sample_entry = {
    "teacher_name": "Dr Rao",
    "subject": "DBMS",
    "class_name": "CSE-A",
    "day": "Monday",
    "start_time": "09:00",
    "end_time": "10:00",
    "room": "Room 301",
    "source_document": "sample_timetable.pdf",
    "source_page": 1,
    "review_status": "verified",
}

entry_id = insert_entry(sample_entry)

print("Inserted entry ID:", entry_id)
print("Database entries:")
for entry in get_all_entries():
    print(entry)
