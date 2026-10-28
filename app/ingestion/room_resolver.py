
def resolve_room(
    cell_room=None,
    timetable_default_room=None,
):
    """
    Prefer a room explicitly stated in the timetable cell.
    Otherwise, use the default room from that same timetable.
    """
    if cell_room and cell_room.strip():
        return cell_room.strip()

    if timetable_default_room and timetable_default_room.strip():
        return timetable_default_room.strip()

    return None
