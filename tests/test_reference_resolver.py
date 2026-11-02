import unittest

from app.ingestion.reference_resolver import extract_reference_table, resolve_records_with_reference


def word(text, x, y):
    return {
        "text": text,
        "original_bbox": {"x": x, "y": y, "width": max(8, len(text) * 7), "height": 12},
    }


def synthetic_reference_words():
    words = []
    for token, x in [("COURSE", 90), ("CODE", 140), ("COURSE", 430),
                     ("NAME", 500), ("FACULTY", 1000)]:
        words.append(word(token, x, 10))
    rows = [
        (30, [("CSE556", 100), ("OBJECT", 210), ("ORIENTED", 280),
              ("ANALYSIS", 370), ("AND", 450), ("DESIGN", 490),
              ("(OOAD)", 620), ("SECTION", 770), ("1:", 830),
              ("DR.", 855), ("SUSHMA", 885), ("B", 945),
              ("(SB)", 965), ("(LHC", 1010), ("206)", 1050)]),
        (50, [("SECTION", 770), ("2:", 830), ("DR.", 855),
              ("CHANDRIKA", 885), ("P", 960), ("(CP)", 980),
              ("(LHC", 1010), ("207)", 1050)]),
        (70, [("SECTION", 770), ("3:", 830), ("DR.", 855),
              ("PARKAVI", 885), ("A", 950), ("(APK)", 970),
              ("(LHC", 1010), ("208)", 1050)]),
        (90, [("CSE557", 100), ("BIG", 210), ("DATA", 260),
              ("ANALYTICS", 320), ("(BDA)", 620), ("SECTION", 770),
              ("4:", 830), ("UZMA", 855), ("SULTHANA", 900),
              ("(US)", 970), ("(LHC", 1010), ("301)", 1050)]),
    ]
    for y, tokens in rows:
        words.extend(word(t, x, y) for t, x in tokens)
    return words


class ReferenceResolverTests(unittest.TestCase):
    def test_reference_table_extracts_section_assignments(self):
        reference = extract_reference_table(synthetic_reference_words())
        self.assertTrue(reference["found"])
        ooad = next(c for c in reference["courses"] if c["abbreviation"] == "OOAD")
        self.assertEqual([a["section"] for a in ooad["assignments"]], [1, 2, 3])
        self.assertEqual([a["room"] for a in ooad["assignments"]],
                         ["LHC 206", "LHC 207", "LHC 208"])

    def test_combined_elective_expands_using_reference(self):
        reference = extract_reference_table(synthetic_reference_words())
        records = [{"day": "Monday", "start_time": "11:05", "end_time": "12:00",
                    "subject": "OOAD/BDA", "original_text": "OOAD/BDA",
                    "teacher_name": "UNKNOWN", "room": "UNKNOWN"}]
        resolved = resolve_records_with_reference(records, reference)
        self.assertEqual(len(resolved), 4)
        self.assertEqual([r["section_group"] for r in resolved], [1, 2, 3, 4])
        self.assertEqual([r["teacher_name"] for r in resolved],
                         ["DR. SUSHMA B", "DR. CHANDRIKA P", "DR. PARKAVI A", "UZMA SULTHANA"])

    def test_activity_is_not_assigned_a_faculty_member(self):
        reference = extract_reference_table(synthetic_reference_words())
        record = {"subject": "Industry Talk / Training",
                  "original_text": "INDUSTRY TALK / TRAINING",
                  "teacher_name": "UNKNOWN", "room": "UNKNOWN"}
        resolved = resolve_records_with_reference([record], reference)[0]
        self.assertEqual(resolved["record_type"], "activity")
        self.assertEqual(resolved["teacher_name"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
