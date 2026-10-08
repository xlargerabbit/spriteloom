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


def test_palette_cycle_ignores_hidden_colors_and_preserves_rgba():
    source = Image.new("RGBA", (3, 1), (0, 255, 0, 0))
    source.putpixel((0, 0), (255, 0, 0, 128))
    source.putpixel((1, 0), (0, 0, 255, 255))
    frames, _ = animate(source, "palette_cycle", count=2, strength=1)
    assert frames[0].getpixel((4, 4)) == (255, 0, 0, 128)
    assert frames[1].getpixel((4, 4)) == (0, 0, 255, 128)
    assert frames[0].getpixel((6, 4)) == (0, 255, 0, 0)


def test_default_strength_cycles_two_color_palette():
    source = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
    source.putpixel((1, 0), (0, 0, 255, 255))
    frames, _ = animate(source, "palette_cycle", count=2, strength=2)
    assert frames[0].getpixel((4, 4)) != frames[1].getpixel((4, 4))


def test_pulse_keeps_custom_pivot_fixed_and_pixels_inside_canvas():
    source = Image.new("RGBA", (100, 20), (240, 20, 10, 255))
    frames, pivot = animate(source, "pulse", count=4, strength=4, pivot=(0, 0))
    assert pivot == (16, 16)
    assert frames[1].getbbox() == (16, 16, 132, 39)


def test_one_frame_sheet_is_static_source_on_transparent_canvas():
    source = Image.new("RGBA", (2, 1), (60, 70, 80, 128))
    frames, _ = animate(source, "bounce", count=1, strength=2)
    sheet, rects = spritesheet(frames)
    assert sheet.tobytes() == frames[0].tobytes()
    assert rects == [{"index": 0, "x": 0, "y": 0, "width": 10, "height": 9}]
    assert frames[0].getpixel((4, 4)) == (60, 70, 80, 128)


def test_animation_rejects_excessive_sheet_size():
    source = Image.new("RGBA", (2000, 2000), "red")
    with pytest.raises(ValueError, match="32 million pixels"):
        animate(source, "idle_bob", count=16, strength=2)
