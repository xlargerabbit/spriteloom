import pytest
from PIL import Image

from spriteloom_mcp.assets import AssetStore, image_summary


def test_import_copies_png_and_persists_asset(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGBA", (3, 2), (10, 20, 30, 255))
    image.putpixel((1, 0), (0, 0, 0, 0))
    image.save(source)

    store = AssetStore(tmp_path / "assets")
    record = store.import_png(str(source))
    source.unlink()

    reopened = AssetStore(tmp_path / "assets")
    loaded_record, loaded_image = reopened.get(record["asset_id"])
    assert loaded_record["operation"] == "import"
    assert loaded_image.tobytes() == image.tobytes()
    assert image_summary(loaded_image)["transparent_pixels"] == 1


def test_manifest_is_last_write_and_missing_manifest_is_not_an_asset(tmp_path):
    store = AssetStore(tmp_path / "assets")
    asset_id = "a" * 32
    folder = store.root / asset_id
    folder.mkdir()
    Image.new("RGBA", (2, 2), "red").save(folder / "image.png")

    with pytest.raises(ValueError, match="not found or is damaged"):
        store.get(asset_id)


def test_ids_cannot_escape_asset_root(tmp_path):
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(ValueError, match="invalid asset ID"):
        store.get("../outside")
    with pytest.raises(ValueError, match="invalid asset or frame set ID"):
        store.has_animation("../outside")


def test_animation_manifest_and_sheet_survive_reopen(tmp_path):
    store = AssetStore(tmp_path / "assets")
    frames = [Image.new("RGBA", (2, 2), (i * 80, 0, 0, 255)) for i in range(2)]
    sheet = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        sheet.alpha_composite(frame, (i * 2, 0))
    record = store.put_animation(frames, sheet, {"frame_count": 2}, fps=8)

    reopened = AssetStore(tmp_path / "assets")
    loaded_record, loaded_sheet = reopened.get_animation(record["frame_set_id"])
    assert loaded_record["frame_count"] == 2
    assert loaded_sheet.tobytes() == sheet.tobytes()
    folder = store.root / "animations" / record["frame_set_id"]
    assert (folder / "frame_00.png").is_file()
    assert (folder / "frame_01.png").is_file()
    assert (folder / "preview.gif").is_file()
