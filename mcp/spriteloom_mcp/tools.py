"""Small agent interface over Spriteloom's existing image tasks."""

import json
from typing import Annotated, Literal

from PIL import Image
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import Field

from .assets import AssetStore, image_summary
from .animation import animate, spritesheet
from .config import asset_dir
from .images import contact_sheet, mask_for, png_b64, png_bytes, preview
from .service import ServiceClient, ServiceError

mcp = MCPServer("Spriteloom")
store = AssetStore(asset_dir())
service = ServiceClient()


def _validate(seed: int | None, palette: list[list[int]] | None) -> None:
    if seed is not None and not 0 <= seed < 2**32:
        raise ValueError("seed must be between 0 and 2^32-1")
    if palette is not None:
        if not 1 <= len(palette) <= 256 or any(
            len(color) != 3 or any(type(channel) is not int or not 0 <= channel <= 255 for channel in color)
            for color in palette
        ):
            raise ValueError("palette must contain 1..256 RGB triplets with values 0..255")


def _public(record: dict) -> dict:
    return {**record, "path": str(store.root / record["asset_id"] / "image.png")}


def _public_animation(record: dict) -> dict:
    folder = store.root / "animations" / record["frame_set_id"]
    return {
        **record,
        "sheet_path": str(folder / "sheet.png"),
        "preview_gif_path": str(folder / "preview.gif"),
        "frame_paths": [str(folder / f"frame_{i:02d}.png")
                        for i in range(record["frame_count"])],
    }


def _image_block(image: Image.Image) -> ImageContent:
    import base64
    return ImageContent(type="image", data=base64.b64encode(png_bytes(image)).decode("ascii"), mime_type="image/png")


def _result(records: list[dict], images: list[Image.Image]) -> CallToolResult:
    assets = [_public(record) for record in records]
    labels = [f"{i + 1}: {record.get('seed', '-')}" for i, record in enumerate(records)]
    sheet = contact_sheet(images, labels)
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps({"assets": assets})),
            _image_block(sheet),
        ],
        structured_content={"assets": assets},
    )


def _progress(ctx: Context):
    last = 0.0

    async def report(value: float, stage: str | None) -> None:
        nonlocal last
        # The GPU service sends value=0 for stage changes. MCP progress must rise.
        if last >= 99.9:
            return
        current = min(99.9, max(last + 0.1, 90.0 * max(0.0, min(1.0, value))))
        last = current
        await ctx.report_progress(current, total=100, message=stage or "Generating")

    return report


@mcp.tool()
async def generate_sprite(
    ctx: Context,
    prompt: str,
    width: Annotated[int, Field(ge=1, le=4096)],
    height: Annotated[int, Field(ge=1, le=4096)],
    variants: Annotated[int, Field(ge=1, le=8)] = 4,
    seed: int | None = None,
    background: Literal["auto", "remove", "keep"] = "auto",
    palette: list[list[int]] | None = None,
) -> CallToolResult:
    """Generate game sprite variants from a text prompt; return asset IDs and a preview."""
    try:
        if not prompt.strip():
            raise ValueError("prompt must describe the sprite")
        _validate(seed, palette)
        images, seeds = await service.request({
            "mode": "generate", "prompt": prompt, "target_size": [width, height],
            "variants": variants, "seed": seed, "background": background,
            "palette": palette,
        }, _progress(ctx))
        records = [store.put(image, {
            "operation": "generate", "prompt": prompt, "seed": variant_seed,
            "background": background, "palette": palette,
            "service_protocol": service.protocol,
        }) for image, variant_seed in zip(images, seeds)]
        await ctx.report_progress(100, total=100, message="Saved variants")
        return _result(records, images)
    except (ValueError, OSError, ServiceError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def import_sprite(path: str) -> CallToolResult:
    """Import a local PNG into the asset store so it can be refined."""
    try:
        record = store.import_png(path)
        _, image = store.get(record["asset_id"])
        return _result([record], [image])
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def refine_sprite(
    ctx: Context,
    source_id: str,
    instruction: str,
    operation: Literal["edit", "inpaint", "instruct"] = "edit",
    region: list[int] | None = None,
    mask_path: str | None = None,
    variants: Annotated[int, Field(ge=1, le=8)] = 4,
    seed: int | None = None,
    palette: list[list[int]] | None = None,
    symmetry: bool = False,
) -> CallToolResult:
    """Edit, inpaint, or re-view one sprite while preserving the source asset."""
    try:
        if not instruction.strip():
            raise ValueError("instruction must describe the change")
        _validate(seed, palette)
        _, source = store.get(source_id)
        if operation == "inpaint" and symmetry:
            raise ValueError("symmetry cannot be used with inpaint because it moves the mask alignment")
        frame = {"image": png_b64(source)}
        if operation == "inpaint":
            frame["mask"] = png_b64(mask_for(source, region, mask_path))
        elif region is not None or mask_path is not None:
            raise ValueError("region and mask_path are only used for inpaint")
        images, seeds = await service.request({
            "mode": operation, "prompt": instruction,
            "target_size": list(source.size), "frames": [frame],
            "variants": variants, "seed": seed, "palette": palette,
            "symmetry": symmetry,
        }, _progress(ctx))
        if operation == "inpaint":
            images = [Image.alpha_composite(source, patch) for patch in images]
        records = [store.put(image, {
            "operation": operation, "source_id": source_id,
            "instruction": instruction, "seed": variant_seed,
            "palette": palette, "symmetry": symmetry,
            "region": region, "mask_path": mask_path,
            "service_protocol": service.protocol,
        }) for image, variant_seed in zip(images, seeds)]
        await ctx.report_progress(100, total=100, message="Saved variants")
        return _result(records, images)
    except (ValueError, OSError, ServiceError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def inspect_asset(asset_id: str, full_size: bool = False) -> CallToolResult:
    """Inspect a sprite or animation ID; full_size returns its complete PNG or sheet."""
    try:
        if store.has_animation(asset_id):
            record, image = store.get_animation(asset_id)
            data = _public_animation(record)
        else:
            record, image = store.get(asset_id)
            data = {**_public(record), **image_summary(image)}
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(data)),
            _image_block(image if full_size else preview(image)),
        ],
        structured_content=data,
    )


@mcp.tool()
def animate_sprite(
    source_id: str,
    motion: Literal["idle_bob", "bounce", "pulse", "recoil", "palette_cycle"],
    frames: Annotated[int, Field(ge=1, le=16)] = 8,
    fps: Annotated[int, Field(ge=1, le=30)] = 8,
    strength: Annotated[int, Field(ge=1, le=4)] = 2,
    pivot: list[int] | None = None,
) -> CallToolResult:
    """Make a repeatable idle/effect animation and spritesheet from one sprite."""
    try:
        _, source = store.get(source_id)
        if pivot is not None and (len(pivot) != 2 or any(type(v) is not int for v in pivot)):
            raise ValueError("pivot must be [x, y] in source canvas coordinates")
        source_pivot = tuple(pivot) if pivot is not None else (source.width // 2, source.height)
        images, canvas_pivot = animate(source, motion, frames, strength, source_pivot)
        sheet, rects = spritesheet(images)
        for rect in rects:
            rect["file"] = f"frame_{rect['index']:02d}.png"
            rect["duration_ms"] = round(1000 / fps)
            rect["column"] = rect["index"] % min(4, frames)
            rect["row"] = rect["index"] // min(4, frames)
        record = store.put_animation(images, sheet, {
            "source_id": source_id,
            "motion": motion,
            "strength": strength,
            "fps": fps,
            "frame_count": frames,
            "frame_width": images[0].width,
            "frame_height": images[0].height,
            "columns": min(4, frames),
            "rows": (frames + min(4, frames) - 1) // min(4, frames),
            "source_pivot": list(source_pivot),
            "pivot": list(canvas_pivot),
            "frames": rects,
        }, fps)
        data = _public_animation(record)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data)),
                     _image_block(preview(sheet))],
            structured_content=data,
        )
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
