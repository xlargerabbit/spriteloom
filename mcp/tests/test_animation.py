import pytest
from PIL import Image

from spriteloom_mcp.animation import animate, spritesheet


def test_idle_frames_have_fixed_canvas_and_preserve_alpha():
    source = Image.new("RGBA", (4, 4), (10, 20, 30, 0))
    source.putpixel((1, 2), (10, 20, 30, 128))
    frames, pivot = animate(source, "idle_bob", count=4, strength=1)

    assert len({frame.size for frame in frames}) == 1
    assert pivot == (frames[0].width // 2, 4 + 4)
    assert frames[0].getpixel((4 + 1, 4 + 2)) == (10, 20, 30, 128)
    assert frames[1].getpixel((4 + 1, 4 + 1)) == (10, 20, 30, 128)


def test_sheet_rectangles_match_frames():
    source = Image.new("RGBA", (5, 3), (220, 30, 20, 255))
    frames, _ = animate(source, "pulse", count=6, strength=2)
    sheet, rects = spritesheet(frames)

    assert sheet.size == (4 * frames[0].width, 2 * frames[0].height)
    for frame, rect in zip(frames, rects):
        box = (rect["x"], rect["y"], rect["x"] + rect["width"],
               rect["y"] + rect["height"])
        assert sheet.crop(box).tobytes() == frame.tobytes()


def test_palette_cycle_changes_colors_without_changing_alpha():
    source = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    source.putpixel((0, 0), (255, 0, 0, 255))
    source.putpixel((1, 0), (0, 0, 255, 128))
    frames, _ = animate(source, "palette_cycle", count=2, strength=1)
    colors = [frame.getpixel((4, 4)) for frame in frames]
    assert colors[0] != colors[1]
    assert [color[3] for color in colors] == [255, 255]
    assert [frame.getpixel((5, 4))[3] for frame in frames] == [128, 128]


def test_animation_rejects_excessive_sheet_size():
    source = Image.new("RGBA", (2000, 2000), "red")
    with pytest.raises(ValueError, match="32 million pixels"):
        animate(source, "idle_bob", count=16, strength=2)
