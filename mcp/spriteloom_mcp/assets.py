"""Immutable persistent assets for cross-call agent workflows."""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .images import MAX_INPUT_BYTES, check_image, png_bytes, preview

_ID = re.compile(r"^[0-9a-f]{32}$")


class AssetStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, image: Image.Image, metadata: dict) -> dict:
        image = check_image(image)
        asset_id = uuid.uuid4().hex
        folder = self.root / asset_id
        folder.mkdir()
        record = {
            "asset_id": asset_id,
            "width": image.width,
            "height": image.height,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        self._write(folder / "image.png", png_bytes(image))
        self._write(folder / "preview.png", png_bytes(preview(image)))
        self._write(folder / "manifest.json", json.dumps(record, indent=2).encode("utf-8"))
        return record

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        temp = path.with_name(path.name + ".tmp")
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def get(self, asset_id: str) -> tuple[dict, Image.Image]:
        if not _ID.fullmatch(asset_id):
            raise ValueError("invalid asset ID")
        folder = self.root / asset_id
        try:
            record = json.loads((folder / "manifest.json").read_text("utf-8"))
            with Image.open(folder / "image.png") as loaded:
                image = check_image(loaded)
        except (OSError, ValueError) as exc:
            raise ValueError(f"asset {asset_id} was not found or is damaged") from exc
        if record.get("asset_id") != asset_id:
            raise ValueError("asset manifest ID does not match")
        return record, image

    def import_png(self, path: str) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".png":
            raise ValueError("path must name an existing local PNG")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("PNG must be under 32 MB")
        try:
            with Image.open(source) as loaded:
                image = check_image(loaded)
        except (OSError, ValueError) as exc:
            raise ValueError("could not read PNG") from exc
        return self.put(image, {"operation": "import", "source_path": str(source)})

    def image_path(self, asset_id: str) -> Path:
        self.get(asset_id)
        return self.root / asset_id / "image.png"

    def put_animation(self, frames: list[Image.Image], sheet: Image.Image,
                      manifest: dict, fps: int,
                      guides: list[Image.Image | None] | None = None) -> dict:
        if guides is not None and len(guides) != len(frames):
            raise ValueError("guide count must match frame count")
        frame_set_id = uuid.uuid4().hex
        folder = self.root / "animations" / frame_set_id
        folder.mkdir(parents=True)
        for index, frame in enumerate(frames):
            self._write(folder / f"frame_{index:02d}.png", png_bytes(frame))
        if guides is not None:
            for index, guide in enumerate(guides):
                if guide is not None:
                    self._write(folder / f"guide_{index:02d}.png", png_bytes(guide))
        self._write(folder / "sheet.png", png_bytes(sheet))
        self._write(folder / "preview.png", png_bytes(preview(sheet)))
        gif_frames = [preview(frame).convert("RGB") for frame in frames]
        gif_frames[0].save(folder / "preview.gif", save_all=True,
                           append_images=gif_frames[1:],
                           duration=round(1000 / fps), loop=0)
        record = {
            "frame_set_id": frame_set_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **manifest,
        }
        self._write(folder / "manifest.json", json.dumps(record, indent=2).encode("utf-8"))
        return record

    def get_animation(self, frame_set_id: str) -> tuple[dict, Image.Image]:
        if not _ID.fullmatch(frame_set_id):
            raise ValueError("invalid frame set ID")
        folder = self.root / "animations" / frame_set_id
        try:
            record = json.loads((folder / "manifest.json").read_text("utf-8"))
            with Image.open(folder / "sheet.png") as loaded:
                sheet = loaded.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise ValueError(f"frame set {frame_set_id} was not found or is damaged") from exc
        if record.get("frame_set_id") != frame_set_id:
            raise ValueError("frame set manifest ID does not match")
        return record, sheet

    def has_animation(self, frame_set_id: str) -> bool:
        if not _ID.fullmatch(frame_set_id):
            raise ValueError("invalid asset or frame set ID")
        return (self.root / "animations" / frame_set_id / "manifest.json").is_file()


def image_summary(image: Image.Image) -> dict:
    alpha = image.getchannel("A")
    counts = alpha.histogram()
    colors = image.getcolors(maxcolors=257)
    return {
        "transparent_pixels": counts[0],
        "partial_alpha_pixels": sum(counts[1:255]),
        "opaque_pixels": counts[255],
        "color_count": len(colors) if colors is not None else ">256",
    }
