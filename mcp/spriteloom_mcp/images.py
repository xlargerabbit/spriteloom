"""Image conversion at the Spriteloom wire seam."""

import base64
import binascii
import io

from PIL import Image, ImageDraw

MAX_SIDE = 4096
MAX_INPUT_BYTES = 32 * 1024 * 1024


def check_image(image: Image.Image) -> Image.Image:
    if not all(1 <= side <= MAX_SIDE for side in image.size):
        raise ValueError(f"image dimensions must be 1..{MAX_SIDE} per side")
    return image.convert("RGBA")


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def png_b64(image: Image.Image) -> str:
    return base64.b64encode(png_bytes(image)).decode("ascii")


def raw_image(payload: dict) -> Image.Image:
    try:
        width, height = int(payload["w"]), int(payload["h"])
        if not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE:
            raise ValueError("invalid dimensions")
        pixels = base64.b64decode(payload["px"], validate=True)
        if len(pixels) != width * height * 4:
            raise ValueError("incorrect RGBA byte count")
        return Image.frombytes("RGBA", (width, height), pixels)
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError(f"invalid image returned by Spriteloom: {exc}") from exc


def mask_for(image: Image.Image, region: list[int] | None, mask_path: str | None) -> Image.Image:
    if (region is None) == (mask_path is None):
        raise ValueError("inpaint requires exactly one of region or mask_path")
    if region is not None:
        if len(region) != 4 or any(type(v) is not int for v in region):
            raise ValueError("region must be [x, y, width, height]")
        x, y, width, height = region
        if width < 1 or height < 1 or x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ValueError("region must fit inside the source image")
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rectangle((x, y, x + width - 1, y + height - 1), fill=255)
        return mask
    from pathlib import Path
    path = Path(mask_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("mask_path must name a local image under 32 MB")
    with Image.open(path) as loaded:
        if loaded.size != image.size:
            raise ValueError("mask dimensions must match the source image")
        mask = loaded.convert("L")
    if not mask.getbbox():
        raise ValueError("mask is empty")
    return mask


def contact_sheet(images: list[Image.Image], labels: list[str]) -> Image.Image:
    max_side = max(max(i.size) for i in images)
    scale = min(8.0, 160 / max_side)
    cell_w = max(1, round(max(i.width for i in images) * scale)) + 16
    cell_h = max(1, round(max(i.height for i in images) * scale)) + 30
    columns = min(4, len(images))
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#444444")
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(zip(images, labels)):
        left = (index % columns) * cell_w
        top = (index // columns) * cell_h
        draw.rectangle((left + 4, top + 4, left + cell_w - 5, top + cell_h - 19), fill="#dadada")
        enlarged = image.resize((max(1, round(image.width * scale)),
                                 max(1, round(image.height * scale))), Image.Resampling.NEAREST)
        x = left + (cell_w - enlarged.width) // 2
        y = top + 8 + (cell_h - 24 - enlarged.height) // 2
        sheet.paste(enlarged, (x, y), enlarged)
        draw.text((left + 8, top + cell_h - 16), label, fill="white")
    return sheet


def preview(image: Image.Image) -> Image.Image:
    scale = min(8.0, 384 / max(image.size))
    shown = image.resize((max(1, round(image.width * scale)),
                          max(1, round(image.height * scale))), Image.Resampling.NEAREST)
    backdrop = Image.new("RGB", shown.size, "#dadada")
    backdrop.paste(shown, (0, 0), shown)
    return backdrop
