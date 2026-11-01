
from PIL import Image, ImageDraw
from app.ingestion.timetable_registry import get_available_timetables
from app.ingestion.grid_extractor import crop_timetable_area

items = get_available_timetables()

output_dir = "data/uploads/all_timetable_crops"
from pathlib import Path
output_dir = Path(output_dir)
output_dir.mkdir(parents=True, exist_ok=True)

thumb_width = 600
thumb_height = 300
label_height = 35
columns = 2
rows = (len(items) + columns - 1) // columns

sheet = Image.new(
    "RGB",
    (columns * thumb_width, rows * (thumb_height + label_height)),
    "white",
)
draw = ImageDraw.Draw(sheet)

for index, item in enumerate(items):
    crop_path = crop_timetable_area(
        item["image_path"],
        output_path=output_dir / f"{item['timetable_id']}_crop.png",
    )

    with Image.open(crop_path) as source:
        crop = source.convert("RGB")
        original_size = crop.size
        crop.thumbnail((thumb_width - 20, thumb_height - 20))

    x = (index % columns) * thumb_width
    y = (index // columns) * (thumb_height + label_height)

    sheet.paste(
        crop,
        (
            x + (thumb_width - crop.width) // 2,
            y + (thumb_height - crop.height) // 2,
        ),
    )

    draw.text(
        (x + 10, y + thumb_height + 5),
        f"{item['image']} | crop {original_size}",
        fill="black",
    )

sheet.save(output_dir / "crop_contact_sheet.jpg", quality=90)

print("Crops generated:", len(items))
print("Contact sheet:", output_dir / "crop_contact_sheet.jpg")
