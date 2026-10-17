# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Draw Saydown's icon: a sage tile with the dark dictation pill on it. The same drawing as the Mac build's
install-app.sh, so both apps share one logo. Writes assets/saydown.ico (every size Windows asks for) and
assets/saydown.png (512 px, for the README and the GitHub page).

Run:  uv run make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

SAGE, INK = (219, 235, 222, 255), (20, 33, 26, 255)  # the Mac drawing's colours: (0.86, 0.92, 0.87) and (0.08, 0.13, 0.10)
OUT = Path(__file__).parent / "assets"


def draw(px):
    """The icon at px by px, drawn four times larger and scaled down so its curves are smooth."""
    big = px * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    inset = big * 0.1
    pen.rounded_rectangle((inset, inset, big - inset, big - inset), radius=big * 0.18, fill=SAGE)
    width, height = big * 0.58, big * 0.22
    left, top = (big - width) / 2, (big - height) / 2
    pen.rounded_rectangle((left, top, left + width, top + height), radius=height / 2, fill=INK)
    dot = height * 0.42
    x, y = left + height * 0.3, (big - dot) / 2
    pen.ellipse((x, y, x + dot, y + dot), fill=SAGE)
    return image.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    draw(512).save(OUT / "saydown.png")
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    draw(256).save(OUT / "saydown.ico", sizes=[(s, s) for s in sizes],
                   append_images=[draw(s) for s in sizes[:-1]])
    print("wrote", OUT / "saydown.png", "and", OUT / "saydown.ico")
