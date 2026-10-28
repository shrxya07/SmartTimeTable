
from pathlib import Path

import cv2


def detect_timetable_grid(image_path, output_path=None):
    """
    Detect horizontal and vertical grid lines in a timetable image.
    Saves a debug image so we can inspect the detected grid.
    """
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Could not read image: {image_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Convert dark printed lines into white pixels on a black background.
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    height, width = binary.shape

    # Detect long horizontal lines.
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(20, width // 35), 1),
    )
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, horizontal_kernel
    )

    # Detect long vertical lines.
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(20, height // 35)),
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, vertical_kernel
    )

    # Combine detected lines for visual inspection.
    grid = cv2.add(horizontal, vertical)

    if output_path is None:
        output_path = image_path.with_name(
            image_path.stem + "_grid_debug.png"
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not cv2.imwrite(str(output_path), grid):
        raise OSError(f"Could not save debug image: {output_path}")

    horizontal_count = cv2.countNonZero(horizontal)
    vertical_count = cv2.countNonZero(vertical)

    return {
        "image": str(image_path),
        "debug_image": str(output_path),
        "width": width,
        "height": height,
        "horizontal_line_pixels": horizontal_count,
        "vertical_line_pixels": vertical_count,
    }


def crop_timetable_area(image_path, output_path=None):
    """Save a crop containing the timetable schedule, excluding the course legend."""
    image_path = Path(image_path)
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Could not read image: {image_path}")

    height, width = image.shape[:2]

    # Approximate schedule area based on the current timetable layout.
    # These proportions should be checked against other timetable templates.
    x1 = int(width * 0.08)
    x2 = int(width * 0.88)
    y1 = int(height * 0.18)
    y2 = int(height * 0.52)

    cropped = image[y1:y2, x1:x2]

    if output_path is None:
        output_path = image_path.with_name(
            image_path.stem + "_schedule_crop.png"
        )

    output_path = Path(output_path)
    if not cv2.imwrite(str(output_path), cropped):
        raise OSError(f"Could not save crop: {output_path}")

    return output_path



def extract_crop_text(image_path, output_path=None):
    """Run OCR on the timetable crop and save the raw text."""
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)

    tesseract_path = Path(
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )
    if tesseract_path.exists():
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    with Image.open(crop_path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(
            image,
            lang="eng",
            config="--psm 6",
        ).strip()

    if output_path is None:
        output_path = image_path.with_name(
            image_path.stem + "_ocr_preview.txt"
        )

    output_path = Path(output_path)
    output_path.write_text(text, encoding="utf-8")

    return {
        "crop": str(crop_path),
        "text_file": str(output_path),
        "text": text,
    }


def inspect_grid_coordinates(image_path):
    """Detect likely vertical grid lines within the timetable schedule."""
    import numpy as np

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)
    image = cv2.imread(str(crop_path))

    if image is None:
        raise ValueError(f"Could not read crop: {crop_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    height, width = binary.shape

    # Restrict detection to the timetable's day rows,
    # excluding the time header and content below the schedule.
    schedule_top = int(height * 0.22)
    schedule_bottom = int(height * 0.92)
    schedule = binary[schedule_top:schedule_bottom, :]

    schedule_height = schedule.shape[0]

    vertical = cv2.morphologyEx(
        schedule,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, max(15, schedule_height // 12)),
        ),
    )

    vertical_projection = np.count_nonzero(vertical, axis=0)

    # Group nearby x-coordinates into candidate vertical lines.
    positions = np.where(
        vertical_projection >= schedule_height * 0.12
    )[0]

    groups = []
    for position in positions:
        position = int(position)
        if not groups or position - groups[-1][-1] > 5:
            groups.append([position])
        else:
            groups[-1].append(position)

    x_lines = [
        round(sum(group) / len(group))
        for group in groups
    ]

    # Draw detected lines only over the schedule body.
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    for x in x_lines:
        cv2.line(
            overlay,
            (x, schedule_top),
            (x, schedule_bottom),
            (255, 0, 0),
            2,
        )

    output_path = crop_path.with_name(
        crop_path.stem + "_schedule_verticals.png"
    )
    cv2.imwrite(str(output_path), overlay)

    print("Crop size:", width, "x", height)
    print("Schedule y-range:", schedule_top, "to", schedule_bottom)
    print("Vertical line candidates:", x_lines)
    print("Overlay saved to:", output_path)

    return x_lines, [], output_path


def inspect_vertical_segments(image_path):
    """Find vertical grid-line segments within timetable rows."""
    import numpy as np

    image_path = Path(image_path)
    crop_path = crop_timetable_area(image_path)
    image = cv2.imread(str(crop_path))

    if image is None:
        raise ValueError(f"Could not read crop: {crop_path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10,
    )

    height, width = binary.shape

    vertical = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, 35)
        ),
    )

    # Weekday row ranges in the existing schedule crop.
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
        row_mask = vertical[top:bottom, :]
        projection = np.count_nonzero(row_mask, axis=0)

        # Candidate x-coordinates with vertical-line evidence.
        xs = np.where(projection >= (bottom - top) * 0.65)[0]

        groups = []
        for x in xs:
            x = int(x)
            if not groups or x - groups[-1][-1] > 4:
                groups.append([x])
            else:
                groups[-1].append(x)

        boundaries = [
            round(sum(group) / len(group))
            for group in groups
        ]

        print(f"\n{day} candidate vertical borders: {boundaries}")

        for x in boundaries:
            cv2.line(
                overlay, (x, top), (x, bottom - 1),
                (255, 0, 0), 2
            )

    output_path = crop_path.with_name(
        crop_path.stem + "_row_verticals.png"
    )
    cv2.imwrite(str(output_path), overlay)
    print("\nOverlay saved to:", output_path)


if __name__ == "__main__":
    inspect_vertical_segments("data/uploads/cse_sem3_a.png")

