
from app.ingestion.timetable_registry import get_available_timetables
from app.ingestion.cell_extractor import extract_cells

for timetable in get_available_timetables():
    try:
        records = extract_cells(timetable["image_path"])
        print(
            f'{timetable["timetable_id"]}: '
            f'{len(records)} records'
        )

        for record in records[:3]:
            print(
                f'  {record["day"]} '
                f'{record["start_time"]}-{record["end_time"]}: '
                f'{record["text"]}'
            )
    except Exception as exc:
        print(f'{timetable["timetable_id"]}: ERROR: {exc}')
