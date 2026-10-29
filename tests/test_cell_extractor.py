
from app.ingestion.cell_extractor import (
    MERGED_CELLS,
    get_merged_periods,
)


def test_monday_ddco_lab_spans_two_periods():
    merged = next(
        cell for cell in MERGED_CELLS
        if cell["day"] == "Monday"
        and cell["text_contains"] == "DD&CO Lab"
    )

    periods = get_merged_periods(merged)

    assert len(periods) == 2
    assert periods[0][2] == "11:05"
    assert periods[-1][3] == "12:55"


def test_thursday_industry_training_spans_three_periods():
    merged = next(
        cell for cell in MERGED_CELLS
        if cell["day"] == "Thursday"
        and cell["text_contains"] == "INDUSTRY"
    )

    periods = get_merged_periods(merged)

    assert len(periods) == 3
    assert periods[0][2] == "13:45"
    assert periods[-1][3] == "16:30"


def test_all_merged_cells_have_valid_periods():
    for merged in MERGED_CELLS:
        periods = get_merged_periods(merged)

        assert periods, f"No periods found for {merged}"
        assert periods[0][2] == merged["start"]
        assert periods[-1][3] == merged["end"]
