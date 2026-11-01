
"""Layout-independent timetable grid geometry detection.

This module detects candidate grid boundaries and creates cell
coordinates without assuming a particular semester or section.
"""
import cv2
import numpy as np
from pathlib import Path
import os
from app.ingestion.grid_extractor import detect_grid_coordinates


def detect_grid_geometry(image_path):
    """Detect candidate grid lines and rectangular cell regions.

    Args:
        image_path: Path to a timetable crop image.

    Returns:
        Dictionary containing image dimensions, grid boundaries,
        and candidate cell rectangles.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(
            f"Timetable image not found: {image_path}"
        )

    result = detect_grid_coordinates(image_path)

    width = result["width"]
    height = result["height"]

    x_lines = sorted(set(result["x_lines"]))
    y_lines = sorted(set(result["y_lines"]))

    if width <= 0 or height <= 0:
        raise ValueError("Invalid image dimensions.")

    # Remove boundaries outside the image.
    x_lines = [x for x in x_lines if 0 <= x < width]
    y_lines = [y for y in y_lines if 0 <= y < height]

    if len(x_lines) < 2 or len(y_lines) < 2:
        raise ValueError(
            "Not enough grid boundaries detected."
        )

    # Generate candidate rectangles between adjacent lines.
    # These are geometric candidates, not yet confirmed timetable cells.
    candidate_cells = []

    for row_index in range(len(y_lines) - 1):
        for col_index in range(len(x_lines) - 1):
            x1 = x_lines[col_index]
            x2 = x_lines[col_index + 1]
            y1 = y_lines[row_index]
            y2 = y_lines[row_index + 1]

            if x2 <= x1 or y2 <= y1:
                continue

            candidate_cells.append({
                "row_index": row_index,
                "column_index": col_index,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": x2 - x1,
                "height": y2 - y1,
            })

    return {
    "image": str(image_path),
    "width": width,
    "height": height,
    "x_lines": x_lines,
    "y_lines": y_lines,
    "candidate_cells": candidate_cells,
    "schedule_top": result.get("schedule_top"),
    "schedule_bottom": result.get("schedule_bottom"),
    "detected_weekdays": result.get("detected_weekdays", []),
    "debug_image": result.get("debug_image"),
}

def inspect_local_boundaries(
    image_path,
    geometry=None,
    band=3,
    threshold=180,
):
    """Measure whether each candidate grid boundary exists locally.

    Returns coverage ratios for vertical and horizontal boundary
    segments. A high ratio suggests a real border; a low ratio
    suggests a possible merged cell.
    """
    import cv2

    image = cv2.imread(str(image_path))

    if image is None:
        raise FileNotFoundError(
            f"Could not read image: {image_path}"
        )

    if geometry is None:
        geometry = detect_grid_geometry(image_path)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = gray < threshold

    x_lines = geometry["x_lines"]
    y_lines = geometry["y_lines"]

    height, width = gray.shape

    def vertical_coverage(x, y1, y2):
        x1 = max(0, x - band)
        x2 = min(width, x + band + 1)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        region = dark[y1:y2, x1:x2]

        if region.size == 0:
            return 0.0

        # Count each image row where dark pixels touch the border.
        touched_rows = region.any(axis=1)
        return float(touched_rows.mean())

    def horizontal_coverage(y, x1, x2):
        y1 = max(0, y - band)
        y2 = min(height, y + band + 1)

        if y2 <= y1 or x2 <= x1:
            return 0.0

        region = dark[y1:y2, x1:x2]

        if region.size == 0:
            return 0.0

        # Count each image column where dark pixels touch the border.
        touched_columns = region.any(axis=0)
        return float(touched_columns.mean())

    vertical = []

    for row_index in range(len(y_lines) - 1):
        y1 = y_lines[row_index]
        y2 = y_lines[row_index + 1]

        for col_index, x in enumerate(x_lines[1:-1], start=1):
            coverage = vertical_coverage(x, y1, y2)

            vertical.append({
                "row_index": row_index,
                "boundary_index": col_index,
                "x": x,
                "y1": y1,
                "y2": y2,
                "coverage": round(coverage, 3),
            })

    horizontal = []

    for boundary_index, y in enumerate(y_lines[1:-1], start=1):
        for col_index in range(len(x_lines) - 1):
            x1 = x_lines[col_index]
            x2 = x_lines[col_index + 1]

            coverage = horizontal_coverage(y, x1, x2)

            horizontal.append({
                "boundary_index": boundary_index,
                "column_index": col_index,
                "y": y,
                "x1": x1,
                "x2": x2,
                "coverage": round(coverage, 3),
            })

    return {
        "vertical": vertical,
        "horizontal": horizontal,
    }

def inspect_morphological_boundaries(image_path, geometry=None):
    """Detect local grid borders while suppressing much of the text.

    Uses directional morphological opening to identify long,
    continuous horizontal and vertical structures.
    """
    import cv2
    import numpy as np

    image = cv2.imread(str(image_path))

    if image is None:
        raise FileNotFoundError(
            f"Could not read image: {image_path}"
        )

    if geometry is None:
        geometry = detect_grid_geometry(image_path)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape

    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    x_lines = geometry["x_lines"]
    y_lines = geometry["y_lines"]

    row_heights = [
        y_lines[i + 1] - y_lines[i]
        for i in range(len(y_lines) - 1)
    ]

    col_widths = [
        x_lines[i + 1] - x_lines[i]
        for i in range(len(x_lines) - 1)
    ]

    median_row_height = int(np.median(row_heights))
    median_col_width = int(np.median(col_widths))

    vertical_length = max(12, median_row_height // 2)
    horizontal_length = max(
    10,
    min(35, median_col_width // 4)
)

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, vertical_length),
    )

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (horizontal_length, 1),
    )

    vertical_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
    )

    horizontal_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
    )

    def vertical_coverage(x, y1, y2):
        x1 = max(0, x - 2)
        x2 = min(width, x + 3)

        region = vertical_mask[y1:y2, x1:x2]

        if region.size == 0:
            return 0.0

        return round(
            float(np.any(region > 0, axis=1).mean()), 3
        )

    def horizontal_coverage(y, x1, x2):
        y1 = max(0, y - 2)
        y2 = min(height, y + 3)

        region = horizontal_mask[y1:y2, x1:x2]

        if region.size == 0:
            return 0.0

        return round(
            float(np.any(region > 0, axis=0).mean()), 3
        )

    vertical = []

    for row_index in range(len(y_lines) - 1):
        y1 = y_lines[row_index]
        y2 = y_lines[row_index + 1]

        for boundary_index, x in enumerate(x_lines[1:-1], start=1):
            vertical.append({
                "row_index": row_index,
                "boundary_index": boundary_index,
                "x": x,
                "coverage": vertical_coverage(x, y1, y2),
            })

    horizontal = []

    for boundary_index, y in enumerate(y_lines[1:-1], start=1):
        for column_index in range(len(x_lines) - 1):
            x1 = x_lines[column_index]
            x2 = x_lines[column_index + 1]

            horizontal.append({
                "boundary_index": boundary_index,
                "column_index": column_index,
                "y": y,
                "coverage": horizontal_coverage(y, x1, x2),
            })

    return {
        "vertical": vertical,
        "horizontal": horizontal,
        "vertical_kernel_length": vertical_length,
        "horizontal_kernel_length": horizontal_length,
    }


def inspect_row_wise_boundaries(image_path, geometry=None, threshold=180):
    """Measure vertical boundary strength separately in each schedule row."""
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    if geometry is None:
        geometry = detect_grid_geometry(image_path)

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Could not read image: {image_path}")

    height, width = gray.shape

    x_lines = sorted(set(geometry["x_lines"]))
    y_lines = sorted(set(geometry["y_lines"]))

    # Ignore image edges; they are not timetable separators.
    x_lines = [
        x for x in x_lines
        if 0 < x < width - 1
    ]

    if len(x_lines) < 2 or len(y_lines) < 2:
        return []

    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    row_heights = [
        y_lines[i + 1] - y_lines[i]
        for i in range(len(y_lines) - 1)
        if y_lines[i + 1] > y_lines[i]
    ]

    if not row_heights:
        return []

    # Shorter kernel preserves evidence in individual timetable rows.
    vertical_length = max(
        8, min(20, int(np.median(row_heights) // 3))
    )

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, vertical_length),
    )

    vertical_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
    )

    results = []

    for row_index in range(len(y_lines) - 1):
        y1 = y_lines[row_index]
        y2 = y_lines[row_index + 1]

        if y1 < 0 or y2 > height or y2 <= y1:
            continue

        row_height = y2 - y1
        inset = max(2, int(row_height * 0.10))

        inner_y1 = y1 + inset
        inner_y2 = y2 - inset

        if inner_y2 <= inner_y1:
            continue

        row_boundaries = []

        for x in x_lines:
            left = max(0, x - 2)
            right = min(width, x + 3)

            region = vertical_mask[
                inner_y1:inner_y2, left:right
            ]

            coverage = (
                float(np.any(region > 0, axis=1).mean())
                if region.size else 0.0
            )

            row_boundaries.append({
                "x": x,
                "coverage": round(coverage, 3),
                "likely_present": coverage >= 0.35,
            })

        results.append({
            "row_index": row_index,
            "y_start": y1,
            "y_end": y2,
            "boundaries": row_boundaries,
        })

    return results

    """
    Measure vertical grid-line strength separately inside each row.

    This is diagnostic only. It does not merge cells automatically.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    if geometry is None:
        geometry = detect_grid_geometry(image_path)

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Could not read image: {image_path}")

    height, width = gray.shape
    x_lines = consolidate_nearby_lines(geometry["x_lines"],min_distance=20,)
    y_lines = sorted(set(geometry["y_lines"]))

    # Detect long vertical structures while suppressing most text.
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    row_heights = [
        y_lines[i + 1] - y_lines[i]
        for i in range(len(y_lines) - 1)
        if y_lines[i + 1] > y_lines[i]
    ]

    if not row_heights:
        return []

    vertical_length = max(10, int(np.median(row_heights) // 2))

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, vertical_length),
    )

    vertical_mask = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
    )

    results = []

    for row_index in range(len(y_lines) - 1):
        y1 = y_lines[row_index]
        y2 = y_lines[row_index + 1]

        if y1 < 0 or y2 > height or y2 <= y1:
            continue

        row_height = y2 - y1

        # Avoid counting intersections with horizontal borders.
        inset = max(2, int(row_height * 0.10))
        inner_y1 = min(y1 + inset, y2)
        inner_y2 = max(y2 - inset, inner_y1)

        row_boundaries = []

        for x in x_lines:
            if x < 0 or x >= width:
                continue

            left = max(0, x - 2)
            right = min(width, x + 3)

            region = vertical_mask[inner_y1:inner_y2, left:right]

            if region.size == 0:
                coverage = 0.0
            else:
                coverage = float(np.count_nonzero(region)) / region.size

            row_boundaries.append({
                "x": x,
                "coverage": round(coverage, 3),
                "likely_present": coverage >= 0.35,
            })

        results.append({
            "row_index": row_index,
            "y_start": y1,
            "y_end": y2,
            "boundaries": row_boundaries,
        })

    return results

def consolidate_nearby_lines(lines, min_distance=20):
    """
    Group nearby candidate grid lines and represent each group
    with its average coordinate.
    """
    lines = sorted(set(int(x) for x in lines))

    if not lines:
        return []

    groups = [[lines[0]]]

    for x in lines[1:]:
        if x - groups[-1][-1] <= min_distance:
            groups[-1].append(x)
        else:
            groups.append([x])

    return [
        round(sum(group) / len(group))
        for group in groups
    ]


def infer_merged_cells(
    row_results,
    min_coverage=0.35,
    min_row_height=35,
    excluded_x_ranges=None,
):
    """
    Group consecutive missing internal boundaries into complete
    candidate merged regions.

    Results remain unverified and require visual review.
    """
    merged_candidates = []
    excluded_x_ranges = excluded_x_ranges or []
    for row in row_results:
        y_start = row["y_start"]
        y_end = row["y_end"]

        # Ignore short header and non-class rows.
        if y_end - y_start < min_row_height:
            continue

        boundaries = row["boundaries"]

        if len(boundaries) < 3:
            continue

        # Find consecutive missing internal boundaries.
        missing = []

        for index in range(1, len(boundaries) - 1):
            if boundaries[index]["coverage"] < min_coverage:
                missing.append(index)

        if not missing:
            continue

        # Group adjacent missing boundaries into one span.
        groups = []
        current_group = [missing[0]]

        for index in missing[1:]:
            if index == current_group[-1] + 1:
                current_group.append(index)
            else:
                groups.append(current_group)
                current_group = [index]

        groups.append(current_group)

        for group in groups:
            first = group[0]
            last = group[-1]
            x_start = boundaries[first - 1]["x"]
            x_end = boundaries[last + 1]["x"]

            # Skip candidate regions that overlap excluded columns.
            if any(
                x_start < excluded_end and x_end > excluded_start
                for excluded_start, excluded_end in excluded_x_ranges
            ):
                continue

            merged_candidates.append({
                "row_index": row["row_index"],
                "y_start": y_start,
                "y_end": y_end,
                "x_start": x_start,
                "x_end": x_end,
                "missing_boundaries": [
                    boundaries[i]["x"] for i in group
                ],
                "review_status": "needs_review",
})

    return merged_candidates
