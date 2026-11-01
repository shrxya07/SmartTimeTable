
from PIL import Image, ImageDraw
from app.ingestion.timetable_registry import get_available_timetables

items = get_available_timetables()

thumb_width = 600
thumb_height = 400
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
    image = Image.open(item["image_path"]).convert("RGB")
    image.thumbnail((thumb_width - 20, thumb_height - 20))

    x = (index % columns) * thumb_width
    y = (index // columns) * (thumb_height + label_height)

    image_x = x + (thumb_width - image.width) // 2
    image_y = y + (thumb_height - image.height) // 2

    sheet.paste(image, (image_x, image_y))

    draw.text(
        (x + 10, y + thumb_height + 5),
        item["image"],
        fill="black",
    )

output_path = "data/uploads/timetable_contact_sheet.jpg"
sheet.save(output_path, quality=90)

print(f"Timetables included: {len(items)}")
print(f"Saved contact sheet to: {output_path}")
