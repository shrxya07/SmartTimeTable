
from app.ingestion.importer import import_timetable_records
from data.uploads.sem3_a_records import records

results = import_timetable_records(
    records,
    default_room="LHC 301",
    semester=3,
    section="A",
    timetable_id="sem3_a_2026_odd",
    source_document="cse_sem3_a.png",
)

for result in results:
    print(result)

print("\nImport summary:")
print("Imported:", sum(r["status"] == "imported" for r in results))
print("Needs review:", sum(r["status"] == "needs_review" for r in results))
