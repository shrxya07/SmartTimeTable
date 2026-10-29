
from pathlib import Path

UPLOADS_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"

TIMETABLES = [
    {
        "timetable_id": "sem3_a_2026_odd",
        "image": "cse_sem3_a.png",
        "semester": 3,
        "section": "A",
        "default_room": "LHC 301",
    },
    {
        "timetable_id": "sem3_b_2026_odd",
        "image": "cse_sem3_b.png",
        "semester": 3,
        "section": "B",
        "default_room": None,
    },
    {
        "timetable_id": "sem3_c_2026_odd",
        "image": "cse_sem3_c.png",
        "semester": 3,
        "section": "C",
        "default_room": None,
    },
    {
        "timetable_id": "sem3_d_2026_odd",
        "image": "cse_sem3_d.png",
        "semester": 3,
        "section": "D",
        "default_room": None,
    },
    {
        "timetable_id": "sem5_a_2026_odd",
        "image": "cse_sem5_a.png",
        "semester": 5,
        "section": "A",
        "default_room": None,
    },
    {
        "timetable_id": "sem5_b_2026_odd",
        "image": "cse_sem5_b.png",
        "semester": 5,
        "section": "B",
        "default_room": None,
    },
    {
        "timetable_id": "sem5_c_2026_odd",
        "image": "cse_sem5_c.png",
        "semester": 5,
        "section": "C",
        "default_room": None,
    },
    {
        "timetable_id": "sem5_d_2026_odd",
        "image": "cse_sem5_d.png",
        "semester": 5,
        "section": "D",
        "default_room": None,
    },
    {
        "timetable_id": "sem7_a_2026_odd",
        "image": "cse_sem7_a.png",
        "semester": 7,
        "section": "A",
        "default_room": None,
    },
    {
        "timetable_id": "sem7_b_2026_odd",
        "image": "cse_sem7_b.png",
        "semester": 7,
        "section": "B",
        "default_room": None,
    },
    {
        "timetable_id": "sem7_c_2026_odd",
        "image": "cse_sem7_c.jpeg",
        "semester": 7,
        "section": "C",
        "default_room": None,
    },
]


def get_available_timetables():
    """Return registered timetable images that exist on disk."""
    return [
        {**item, "image_path": UPLOADS_DIR / item["image"]}
        for item in TIMETABLES
        if (UPLOADS_DIR / item["image"]).is_file()
    ]
