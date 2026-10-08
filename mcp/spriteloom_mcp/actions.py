"""Caller-defined pose frames and optional visual references."""

from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field


class PoseFrame(BaseModel):
    phase: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    guide_path: str | None = None
    root_offset: list[int] = Field(default_factory=lambda: [0, 0])
    reuse_source: bool = False


def validate_poses(poses: list[PoseFrame]) -> None:
    if not 1 <= len(poses) <= 16:
        raise ValueError("poses must contain 1..16 frames")
    for index, pose in enumerate(poses):
        if not pose.phase.strip() or not pose.instruction.strip():
            raise ValueError(f"pose {index} needs a phase and instruction")
        if len(pose.root_offset) != 2 or any(type(v) is not int for v in pose.root_offset):
            raise ValueError(f"pose {index} root_offset must be [x, y]")
        if pose.reuse_source and pose.guide_path:
            raise ValueError(f"pose {index} cannot reuse source and specify a guide")


def load_guide(path: str, size: tuple[int, int]) -> Image.Image:
    guide_path = Path(path).expanduser().resolve()
    if (not guide_path.is_file() or guide_path.suffix.lower() != ".png"
            or guide_path.stat().st_size > 32 * 1024 * 1024):
        raise ValueError("guide must be a local PNG under 32 MB")
    with Image.open(guide_path) as loaded:
        guide = loaded.convert("RGB")
    if guide.size != size:
        raise ValueError("guide dimensions must match source")
    return guide
