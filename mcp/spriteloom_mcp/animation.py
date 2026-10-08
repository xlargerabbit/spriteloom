"""Deterministic pixel animation from one flattened sprite."""

import math

from PIL import Image

PRESETS = ("idle_bob", "bounce", "pulse", "recoil", "palette_cycle")


def _colors(image: Image.Image) -> list[tuple[int, int, int]]:
    pixels = image.load()
    colors = {pixels[x, y][:3] for y in range(image.height) for x in range(image.width)
              if pixels[x, y][3]}
    if len(colors) > 4096:
        raise ValueError("palette_cycle supports sprites with at most 4096 visible colors")
    return sorted(colors)


def _cycle(image: Image.Image, colors: list[tuple[int, int, int]], step: int) -> Image.Image:
    if len(colors) < 2:
        return image.copy()
    pixels = image.load()
    output = image.copy()
    target = output.load()
    index = {color: pos for pos, color in enumerate(colors)}
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a:
                nr, ng, nb = colors[(index[(r, g, b)] + step) % len(colors)]
                target[x, y] = (nr, ng, nb, a)
    return output


def animate(image: Image.Image, preset: str, count: int, strength: int,
            pivot: tuple[int, int] | None = None) -> tuple[list[Image.Image], tuple[int, int]]:
    if preset not in PRESETS:
        raise ValueError(f"motion must be one of {', '.join(PRESETS)}")
    if not 1 <= count <= 16 or not 1 <= strength <= 4:
        raise ValueError("frames must be 1..16 and strength must be 1..4")
    if pivot is None:
        pivot = (image.width // 2, image.height)
    if not 0 <= pivot[0] <= image.width or not 0 <= pivot[1] <= image.height:
        raise ValueError("pivot must be within the source canvas")
    source_pivot = pivot
    pad = max(4, strength * 2,
              math.ceil(max(image.size) * 0.04 * strength) if preset == "pulse" else 0)
    canvas_size = (image.width + pad * 2, image.height + pad * 2)
    if canvas_size[0] * canvas_size[1] * count > 32_000_000:
        raise ValueError("animation would exceed 32 million pixels; use a smaller sprite or fewer frames")
    pivot = (pad + pivot[0], pad + pivot[1])
    colors = _colors(image) if preset == "palette_cycle" else []
    frames = []
    for index in range(count):
        phase = 2 * math.pi * index / count
        frame = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        sprite = image
        x, y = pad, pad
        if preset == "idle_bob":
            y -= round(strength * math.sin(phase))
        elif preset == "bounce":
            y -= round(strength * abs(math.sin(phase)))
        elif preset == "pulse":
            factor = 1 + 0.04 * strength * math.sin(phase)
            size = (max(1, round(image.width * factor)),
                    max(1, round(image.height * factor)))
            sprite = image.resize(size, Image.Resampling.NEAREST)
            x = pivot[0] - round(source_pivot[0] * sprite.width / image.width)
            y = pivot[1] - round(source_pivot[1] * sprite.height / image.height)
        elif preset == "recoil":
            x += round(strength * math.exp(-3 * index / max(1, count - 1)) * math.sin(phase))
        elif preset == "palette_cycle":
            step = strength if len(colors) < 2 or strength % len(colors) else 1
            sprite = _cycle(image, colors, index * step)
        frame.paste(sprite, (x, y))
        frames.append(frame)
    return frames, pivot


def spritesheet(frames: list[Image.Image]) -> tuple[Image.Image, list[dict]]:
    columns = min(4, len(frames))
    rows = (len(frames) + columns - 1) // columns
    width, height = frames[0].size
    sheet = Image.new("RGBA", (columns * width, rows * height), (0, 0, 0, 0))
    rects = []
    for index, frame in enumerate(frames):
        x = (index % columns) * width
        y = (index // columns) * height
        sheet.paste(frame, (x, y))
        rects.append({"index": index, "x": x, "y": y, "width": width, "height": height})
    return sheet, rects
