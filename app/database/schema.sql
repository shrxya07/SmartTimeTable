
CREATE TABLE IF NOT EXISTS timetable_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    teacher_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    class_name TEXT NOT NULL,

    day TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,

    room TEXT NOT NULL,

    source_document TEXT,
    source_page INTEGER,

    review_status TEXT NOT NULL DEFAULT 'verified',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

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
ON timetable_entries (class_name, day);

CREATE INDEX IF NOT EXISTS idx_room_day
ON timetable_entries (room, day);
