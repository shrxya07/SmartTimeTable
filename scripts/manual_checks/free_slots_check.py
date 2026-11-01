
from app.scheduling.engine import find_free_slots

slots = find_free_slots(
    teacher_name="Dr Rao",
    day="Monday",
    min_duration_minutes=60,
    working_start="09:00",
    working_end="17:00",
)

for slot in slots:
    print(slot)
