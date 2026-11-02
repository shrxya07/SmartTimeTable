from app.ingestion.cell_line_parser import parse_line


def test_parse_lab_fields_when_ocr_word_order_is_scrambled():
    parsed = parse_line("CSE LAB3 (SB) LAB S1 OOP")

    assert parsed == {
        "batch": "S1",
        "course_token": "OOP",
        "kind": "LAB",
        "initials": ["SB"],
        "room": "CSE LAB 3",
    }


def test_parse_room_patterns_and_multiple_initials():
    parsed = parse_line("S2 AEC NOSQL/PE TUT (BG, PN) AB 701 AND AB 702")

    assert parsed["batch"] == "S2"
    assert parsed["course_token"] == "AEC NOSQL/PE"
    assert parsed["kind"] == "TUT"
    assert parsed["initials"] == ["BG", "PN"]
    assert parsed["room"] == "AB 701 & AB 702"


def test_parse_empty_and_unbatched_lines():
    assert parse_line("") == {
        "batch": None,
        "course_token": None,
        "kind": None,
        "initials": [],
        "room": None,
    }
    assert parse_line("SE&M LHC 204")["course_token"] == "SE&M"


def test_ocr_letter_one_in_batch_marker_is_normalized():
    parsed = parse_line("Si OOAD LAB (SB, NPK) CSE LAB4")

    assert parsed["batch"] == "S1"
    assert parsed["course_token"] == "OOAD"
    assert parsed["kind"] == "LAB"
    assert parsed["initials"] == ["SB", "NPK"]
    assert parsed["room"] == "CSE LAB 4"
