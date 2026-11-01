
from collections import Counter

from app.ingestion.cell_extractor import extract_cells
from app.ingestion.record_parser import parse_timetable_cells


def main():
    cells = extract_cells()

    records = parse_timetable_cells(
        cells,
        semester=3,
        section="A",
        timetable_id="sem3_a_2026_odd",
        default_room="LHC 301",
        source_document="cse_sem3_a.png",
    )

    print("=== TIMETABLE IMPORT DRY RUN ===")
    print(f"Timetable: Semester III, Section A")
    print(f"Extracted cells: {len(cells)}")
    print(f"Parsed records: {len(records)}")

    statuses = Counter(record["review_status"] for record in records)
    print(f"Review statuses: {dict(statuses)}")

    review_reasons = Counter()

    for record in records:
        issues = []

        if record["teacher_name"] == "UNKNOWN":
            issues.append("teacher unknown")

        if record["subject"] in {
            "INDUSTRY TALK / TRAINING",
            "Partial Delivery",
            "Skill Lab-II",
            "AM31 - Lateral Entry",
        }:
            issues.append("verify subject and instructor")

        if record["room"] == "LHC 301" and any(
            marker in record["original_room_text"].upper()
            for marker in ("LAB", "LHC 305", "AB 701", "AB702")
        ):
            issues.append("check explicit room extraction")

        if issues:
            for issue in issues:
                review_reasons[issue] += 1

        print(
            f'{record["day"]:10} '
            f'{record["start_time"]}-{record["end_time"]} | '
            f'{record["subject"]} | '
            f'{record["teacher_name"]} | '
            f'{record["room"]} | '
            f'{"; ".join(issues) if issues else "review required"}'
        )

    print("\n=== SUMMARY ===")
    print("Records ready to import as verified: 0")
    print(f"Records requiring review: {len(records)}")
    print(f"Review flags: {dict(review_reasons)}")
    print("Database modified: NO")


if __name__ == "__main__":
    main()
