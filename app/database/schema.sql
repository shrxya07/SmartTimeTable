
CREATE TABLE IF NOT EXISTS timetable_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    semester INTEGER ,
    section TEXT ,
    timetable_id TEXT ,

    teacher_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    class_name TEXT NOT NULL,

    day TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,

    room TEXT NOT NULL,
    original_room_text TEXT,

    source_document TEXT,
    source_page INTEGER,

    review_status TEXT NOT NULL DEFAULT 'verified',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (semester IN (1, 2, 3, 4, 5, 6, 7, 8)),
    CHECK (day IN (
        'Monday', 'Tuesday', 'Wednesday', 'Thursday',
        'Friday', 'Saturday', 'Sunday'
    )),
    CHECK (start_time < end_time),
    CHECK (review_status IN ('verified', 'needs_review'))
);

CREATE INDEX IF NOT EXISTS idx_teacher_day
ON timetable_entries (teacher_name, day);

CREATE INDEX IF NOT EXISTS idx_class_day
ON timetable_entries (semester, section, day);

CREATE INDEX IF NOT EXISTS idx_room_day
ON timetable_entries (room, day);

CREATE INDEX IF NOT EXISTS idx_timetable_id
ON timetable_entries (timetable_id);

CREATE TABLE IF NOT EXISTS timetables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    timetable_id TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,

    semester INTEGER,
    section TEXT,

    academic_year TEXT,
    term TEXT,

    status TEXT NOT NULL DEFAULT 'uploaded',

    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        semester IS NULL OR semester BETWEEN 1 AND 8
    ),

    CHECK (
        status IN (
            'uploaded',
            'processing',
            'needs_review',
            'ready',
            'failed'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_timetables_semester_section
ON timetables (semester, section);

CREATE INDEX IF NOT EXISTS idx_timetables_status
ON timetables (status);

CREATE TABLE IF NOT EXISTS timetable_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timetable_id TEXT NOT NULL,
    course_name TEXT,
    abbrev TEXT,
    kind TEXT,
    section_label TEXT,
    faculty_name TEXT,
    faculty_initials TEXT,
    room TEXT,
    source_bbox TEXT
);

CREATE TABLE IF NOT EXISTS entry_teachers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    teacher_name TEXT NOT NULL,
    initials TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (entry_id) REFERENCES timetable_entries(id) ON DELETE CASCADE,
    UNIQUE (entry_id, teacher_name, initials, role)
);
