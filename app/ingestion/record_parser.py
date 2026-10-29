
"""Convert extracted timetable cells into normalized database records."""

import re


SUBJECTS = {
    "MATHS": "Transform Techniques and Linear Algebra",
    "DD&CO": "Digital Design and Computer Organization",
    "DS": "Data Structures",
    "OOP": "Object Oriented Programming",
    "DMS": "Discrete Mathematical Structures",
    "UHV": "Universal Human Value",
    "AEC-UNIX": "Ability Enhancement Course - Unix Shell Programming",
    "DS LAB": "Data Structures Laboratory",
    "OOP LAB": "Object Oriented Programming Laboratory",
    "SKILL LAB-II": "Skill Lab-II",
    "INDUSTRY TALK / TRAINING": "Industry Talk / Training",
    "AM31": "AM31 - Lateral Entry",
    "PARTIAL DELIVERY": "Partial Delivery",
        # Semester V
    "DCN": "Data Communication and Networking",
    "AIML": "Artificial Intelligence and Machine Learning",
    "OOAD/BDA": "Object-Oriented Analysis and Design / Big Data Analytics",
    "RM&IPR": "Research Methodology and Intellectual Property Rights",
    "SE&M": "Software Engineering and Modelling",
    "FAFL": "Formal Languages and Automata Theory",
    "EVS": "Environmental Studies",
    "NOSQL/PE": "NoSQL / PE",
    "FSD": "Full Stack Development",
    "SERM": "Software Engineering and Modelling",

    # Semester VII
    "MAP": "MAP",
    "SAN": "SAN",
    "OE": "Open Elective",
    "CD": "CD",
    "MICROSERVICE LAB": "Microservices Laboratory",
    "SKILL ENHANCEMENT LAB": "Skill Enhancement Laboratory",
    "SKILL ENHANCEMENT TUT": "Skill Enhancement Tutorial",
    "INTERNSHIP/PROJECT": "Internship / Project",
    "PROCTOR-STUDENT MEETING": "Proctor-Student Meeting",
    "COLLEGE/DEPT. ACTIVITIES": "College / Department Activities",
    "DCN LAB": "Data Communication and Networking Laboratory",
"MICROSERVICES LAB": "Microservices Laboratory",
}

TEACHERS = {
    "MATHS": "Dr. Umam",
    "DD&CO": "Dr. Manjula R Chougala",
    "DS": "Akshatha Kamath",
    "OOP": "Pradeep Kumar D",
    "DMS": "Mamatha A",
    "UHV": "Sahil Kumar Jamwal",
    "AEC-UNIX": "Uzma Sulthana",
}

ROOM_PATTERNS = [
    (r"\bCSE\s*LAB\s*1\b", "CSE LAB 1"),
    (r"\bCSE\s*LAB\s*2\b", "CSE LAB 2"),
    (r"\bLHC\s*305\b", "LHC 305"),
    (r"\bAB\s*701\s*(?:&|AND)\s*AB\s*702\b", "AB 701 & AB 702"),
    (r"\bAB\s*702\s*701\b", "AB 701 & AB 702"),
]


def parse_timetable_cell(
    cell,
    *,
    semester=3,
    section="A",
    timetable_id="sem3_a_2026_odd",
    default_room="LHC 301",
    source_document="cse_sem3_a.png",
):
    """Parse one OCR cell. Ambiguous results remain marked for review."""

    raw_text = cell.get("text", "").strip()
    normalized = re.sub(r"\s+", " ", raw_text).strip()
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)
    normalized = re.sub(
    r"\bLHC\s*(\d+)\b", r"LHC \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
    r"\bCSE\s*LAB\s*(\d+)\b", r"CSE LAB \1", normalized, flags=re.IGNORECASE)

    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"\bLHC\s*(\d+)\b", r"LHC \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bCSE\s*LAB\s*(\d+)\b", r"CSE LAB \1", normalized, flags=re.IGNORECASE)
    upper = normalized.upper()

    # Find the most specific subject first.
    subject_key = None
    # Normalize common OCR variations before matching.
    upper = re.sub(r"\s+", " ", normalized.upper()).strip()
    upper = upper.replace("INDUSTRY TALK TRAINING", "INDUSTRY TALK / TRAINING")
    upper = upper.replace("PARTIALDELIVERY", "PARTIAL DELIVERY")
    upper = re.sub(r"\bOO\s*P\b", "OOP", upper)
    upper = re.sub(r"\bO\s*O\s*P\b", "OOP", upper)

    # Prefer specific course names before shorter abbreviations.
    subject_key = None
    for candidate in sorted(SUBJECTS, key=len, reverse=True):
        pattern = rf"(?<![A-Z0-9]){re.escape(candidate)}(?![A-Z0-9])"
        if re.search(pattern, upper):
            subject_key = candidate
            break


    if subject_key is None:
        subject = normalized or "UNKNOWN"
        teacher = "UNKNOWN"
        review_status = "needs_review"
    else:
        subject = SUBJECTS[subject_key]
        teacher = TEACHERS.get(subject_key, "UNKNOWN")
        review_status = "needs_review"
    room = default_room
    room_source = "header_default" if default_room else "unresolved"

    for pattern, room_name in ROOM_PATTERNS:
        if re.search(pattern, upper):
            room = room_name
            room_source = "cell"
            break


    # Teacher names for labs and special sessions cannot be safely
    # inferred from abbreviations alone.
    if subject_key and (
    subject_key.endswith("LAB")
    or subject_key in {
        "AM31",
        "INDUSTRY TALK / TRAINING",
        "PARTIAL DELIVERY",
        "INTERNSHIP/PROJECT",
    }
):
     teacher = "UNKNOWN"

    return {
        "teacher_name": teacher,
        "subject": subject,
        "class_name": f"Semester {semester} Section {section}",
        "day": cell["day"],
        "start_time": cell["start_time"],
        "end_time": cell["end_time"],
        "room": room,
        "room_source": room_source,
        "source_document": source_document,
        "source_page": 1,
        "review_status": review_status,
        "semester": semester,
        "section": section,
        "timetable_id": timetable_id,
        "original_room_text": normalized,
    }


def parse_timetable_cells(cells, **kwargs):
    """Parse multiple cells into database-ready record dictionaries."""
    return [parse_timetable_cell(cell, **kwargs) for cell in cells]
