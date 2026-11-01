from pathlib import Path
import os

import cv2


def _group_positions(positions, tolerance=5):
    """Group adjacent pixel coordinates into representative line positions."""
    groups = []
    for value in positions:
        value = int(value)
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]


def _read_image(image_path):
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    return image_path, image


def _configure_tesseract():
    import pytesseract

    command = os.getenv("TESSERACT_CMD") or (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )
    if Path(command).is_file():
        pytesseract.pytesseract.tesseract_cmd = command
    # If the environment variable points to a PATH command, let pytesseract
    # resolve it normally. Otherwise fail with a useful message.
    elif os.getenv("TESSERACT_CMD"):
        raise FileNotFoundError(f"Tesseract executable not found: {command}")
    return pytesseract


def detect_timetable_grid(image_path, output_path=None):
    """Detect horizontal and vertical grid pixels and save a debug image."""
    image_path, image = _read_image(image_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 10,
    )
    height, width = binary.shape
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(20, width // 35), 1)
        ),
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(20, height // 35))
        ),
    )
    grid = cv2.add(horizontal, vertical)

    if output_path is None:
        output_path = image_path.with_name(image_path.stem + "_grid_debug.png")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), grid):
        raise OSError(f"Could not save debug image: {output_path}")

    return {
        "image": str(image_path),
        "debug_image": str(output_path),
        "width": width,
        "height": height,
        "horizontal_line_pixels": cv2.countNonZero(horizontal),
        "vertical_line_pixels": cv2.countNonZero(vertical),
    }


def crop_timetable_area(image_path, output_path=None, crop_bounds=None):
    """Crop a timetable using optional proportional (left, top, right, bottom) bounds."""
    image_path, image = _read_image(image_path)
    height, width = image.shape[:2]

    if crop_bounds is None:
        crop_bounds = (0.08, 0.18, 0.88, 0.52)

    left, top, right, bottom = crop_bounds
    if not (
        0 <= left < right <= 1 and 0 <= top < bottom <= 1
    ):
        raise ValueError("crop_bounds must be valid image proportions.")

    x1, y1 = int(width * left), int(height * top)
    x2, y2 = int(width * right), int(height * bottom)
    cropped = image[y1:y2, x1:x2]
    if cropped.size == 0:
        raise ValueError(f"Crop is empty for image: {image_path}")

    if output_path is None:
        output_path = image_path.with_name(image_path.stem + "_schedule_crop.png")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), cropped):
        raise OSError(f"Could not save crop: {output_path}")
    return output_path


def extract_crop_text(image_path, output_path=None):
    """Run OCR on the default timetable crop and save the raw text."""
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)
    _configure_tesseract()

    with Image.open(crop_path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)
        text = pytesseract.image_to_string(
            image, lang="eng", config="--psm 6"
        ).strip()

    if output_path is None:
        output_path = image_path.with_name(image_path.stem + "_ocr_preview.txt")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return {"crop": str(crop_path), "text_file": str(output_path), "text": text}


def inspect_grid_coordinates(image_path):
    """Print candidate vertical boundaries in the default timetable crop."""
    import numpy as np

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)
    image = cv2.imread(str(crop_path))
    if image is None:
        raise ValueError(f"Could not read crop: {crop_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 10,
    )
    height, width = binary.shape
    schedule_top = int(height * 0.22)
    schedule_bottom = int(height * 0.92)
    schedule = binary[schedule_top:schedule_bottom, :]
    schedule_height = schedule.shape[0]

    vertical = cv2.morphologyEx(
        schedule, cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(15, schedule_height // 12))
        ),
    )
    projection = np.count_nonzero(vertical, axis=0)
    positions = np.where(projection >= schedule_height * 0.12)[0]
    x_lines = _group_positions(positions)

    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for x in x_lines:
        cv2.line(overlay, (x, schedule_top), (x, schedule_bottom), (255, 0, 0), 2)

    output_path = crop_path.with_name(crop_path.stem + "_schedule_verticals.png")
    cv2.imwrite(str(output_path), overlay)
    print("Crop size:", width, "x", height)
    print("Schedule y-range:", schedule_top, "to", schedule_bottom)
    print("Vertical line candidates:", x_lines)
    print("Overlay saved to:", output_path)
    return x_lines, [], output_path


def inspect_vertical_segments(image_path):
    """Print candidate vertical boundaries separately for each detected row."""
    import numpy as np

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)
    image = cv2.imread(str(crop_path))
    if image is None:
        raise ValueError(f"Could not read crop: {crop_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 10,
    )
    height, _ = binary.shape
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 35)),
    )

    # Diagnostic-only row ranges for the historical crop layout.
    rows = [
        ("Monday", 122, 184),
        ("Tuesday", 184, 240),
        ("Wednesday", 240, 308),
        ("Thursday", 308, 355),
        ("Friday", 355, 432),
        ("Saturday", 432, 474),
    ]
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for day, top, bottom in rows:
        top = max(0, min(top, height))
        bottom = max(top, min(bottom, height))
        if bottom <= top:
            continue
        projection = np.count_nonzero(vertical[top:bottom, :], axis=0)
        xs = np.where(projection >= (bottom - top) * 0.65)[0]
        boundaries = _group_positions(xs, tolerance=4)
        print(f"\n{day} candidate vertical borders: {boundaries}")
        for x in boundaries:
            cv2.line(overlay, (x, top), (x, bottom - 1), (255, 0, 0), 2)

    output_path = crop_path.with_name(crop_path.stem + "_row_verticals.png")
    cv2.imwrite(str(output_path), overlay)
    print("\nOverlay saved to:", output_path)
    return output_path


def detect_grid_coordinates(image_path, output_path=None):
    """
    Detect the timetable body using OCR weekday labels plus image grid lines.

    Coordinates are in the original image coordinate system. OCR weekday
    matching tolerates the common Tesseract result 'SATURDA' for 'SATURDAY'.
    """
    import numpy as np
    from pytesseract import Output

    pytesseract = _configure_tesseract()
    image_path, image = _read_image(image_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape

    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 10,
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(30, width // 40), 1)
        ),
    )
    y_projection = np.count_nonzero(horizontal, axis=1)
    all_y_lines = _group_positions(
        np.where(y_projection >= width * 0.25)[0]
    )
    if len(all_y_lines) < 4:
        raise ValueError("Not enough horizontal grid lines detected.")

    ocr = pytesseract.image_to_data(
        gray, config="--oem 3 --psm 11", output_type=Output.DICT
    )
    weekday_aliases = {
        "MONDAY": "MONDAY",
        "TUESDAY": "TUESDAY",
        "WEDNESDAY": "WEDNESDAY",
        "THURSDAY": "THURSDAY",
        "FRIDAY": "FRIDAY",
        "SATURDAY": "SATURDAY",
        "SATURDA": "SATURDAY",
    }
    day_boxes = []
    for i, raw_text in enumerate(ocr["text"]):
        word = raw_text.strip().upper().strip(".,:")
        if word not in weekday_aliases:
            continue
        try:
            confidence = float(ocr["conf"][i])
        except (ValueError, TypeError):
            continue
        if confidence < 25:
            continue
        top = int(ocr["top"][i])
        bottom = top + int(ocr["height"][i])
        day_boxes.append((top, bottom, weekday_aliases[word]))

    found_days = {day for _, _, day in day_boxes}
    if len(found_days) < 4:
        raise ValueError(
            "Could not reliably locate weekday labels. "
            f"Detected: {sorted(found_days)}"
        )

    schedule_tops = []
    for day_top, day_bottom, _ in day_boxes:
        above = [y for y in all_y_lines if y <= day_top]
        below = [y for y in all_y_lines if y >= day_bottom]
        if above and below:
            schedule_tops.append(max(above))

    if not schedule_tops:
        raise ValueError("Could not locate timetable row boundaries.")

    schedule_top = min(schedule_tops)
    last_day_bottom = max(bottom for _, bottom, _ in day_boxes)
    following_lines = [y for y in all_y_lines if y >= last_day_bottom]
    if not following_lines:
        raise ValueError("Could not locate the final weekday row boundary.")
    schedule_bottom = min(following_lines)

    if schedule_bottom <= schedule_top:
        raise ValueError("Invalid timetable schedule region.")

    y_lines = sorted({
        y for y in all_y_lines if schedule_top <= y <= schedule_bottom
    } | {schedule_top, schedule_bottom})

    schedule_binary = binary[schedule_top:schedule_bottom, :]
    schedule_height = schedule_binary.shape[0]
    vertical = cv2.morphologyEx(
        schedule_binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(12, schedule_height // 15))
        ),
    )
    x_projection = np.count_nonzero(vertical, axis=0)
    x_candidates = np.where(x_projection >= schedule_height * 0.08)[0]
    x_lines = _group_positions(x_candidates, tolerance=5)
    if len(x_lines) < 3:
        raise ValueError("Not enough vertical boundaries detected in schedule.")

    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for y in y_lines:
        cv2.line(overlay, (0, y), (width - 1, y), (0, 0, 255), 2)
    for x in x_lines:
        cv2.line(overlay, (x, schedule_top), (x, schedule_bottom), (255, 0, 0), 2)

    if output_path is None:
        output_path = image_path.with_name(image_path.stem + "_grid_coordinates.png")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), overlay):
        raise OSError(f"Could not save grid overlay: {output_path}")

    return {
        "width": width,
        "height": height,
        "x_lines": x_lines,
        "y_lines": y_lines,
        "schedule_top": schedule_top,
        "schedule_bottom": schedule_bottom,
        "detected_weekdays": sorted(found_days),
        "debug_image": str(output_path),
    }


def detect_schedule_region(image_path):
    """Return detected schedule boundaries for manual inspection."""
    result = detect_grid_coordinates(image_path)
    y_lines = result["y_lines"]
    gaps = [
        (y_lines[i + 1] - y_lines[i], i)
        for i in range(len(y_lines) - 1)
    ]
    if len(gaps) < 4:
        raise ValueError("Not enough horizontal lines to identify schedule.")
    return {
        "image": str(image_path),
        "schedule_candidates": y_lines,
        "gaps": gaps,
        "debug_image": result["debug_image"],
        "schedule_top": result["schedule_top"],
        "schedule_bottom": result["schedule_bottom"],
        "detected_weekdays": result["detected_weekdays"],
    }


if __name__ == "__main__":
    inspect_vertical_segments("data/uploads/cse_sem3_a.png")
