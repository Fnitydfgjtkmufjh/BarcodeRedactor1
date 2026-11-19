"""Generate the application icon (PNG + multi-size ICO) – no design assets needed."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "barcode_redactor" / "resources"


def draw(size: int = 256) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size / 256
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s], radius=48 * s, fill=(37, 99, 235, 255))
    d.rounded_rectangle([40 * s, 56 * s, 216 * s, 200 * s], radius=14 * s, fill=(255, 255, 255, 255))
    x = 58
    for w in (6, 3, 9, 3, 6, 12, 3, 6, 3, 9, 6, 3):  # bars on the left half
        if x > 128:
            break
        d.rectangle([x * s, 76 * s, (x + w) * s, 180 * s], fill=(17, 24, 39, 255))
        x += w + 5
    d.rectangle([132 * s, 70 * s, 200 * s, 186 * s], fill=(17, 24, 39, 255))  # redaction block
    return im


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw(256)
    big.save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written to", OUT)
