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
from .actions import PoseFrame, load_guide, validate_poses
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
    data = {
        **record,
        "sheet_path": str(folder / "sheet.png"),
        "preview_gif_path": str(folder / "preview.gif"),
        "frame_paths": [str(folder / f"frame_{i:02d}.png")
                        for i in range(record["frame_count"])],
    }
    if record.get("motion") == "pose_guided":
        data["guide_paths"] = [str(folder / frame["guide_file"])
                               if frame.get("guide_file") else None
                               for frame in record["frames"]]
    return data


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


@mcp.tool()
async def generate_action_sprite(
    ctx: Context,
    source_id: str,
    action: str,
    poses: list[PoseFrame],
    subject_description: str = "",
    seed: int | None = None,
    fps: Annotated[int, Field(ge=1, le=30)] = 8,
    pivot: list[int] | None = None,
) -> CallToolResult:
    """Generate any caller-defined pose sequence; each frame may include a guide PNG."""
    try:
        _validate(seed, None)
        if not action.strip():
            raise ValueError("action must name the sequence")
        validate_poses(poses)
        _, source = store.get(source_id)
        if pivot is not None and (len(pivot) != 2 or any(type(v) is not int for v in pivot)):
            raise ValueError("pivot must be [x, y] in source canvas coordinates")
        sheet_pivot = pivot if pivot is not None else [source.width // 2, source.height // 2]
        guides = [load_guide(pose.guide_path, source.size) if pose.guide_path else None
                  for pose in poses]
        frames = []
        frame_seeds = []
        frame_ids = []
        for index, pose in enumerate(poses):
            if pose.reuse_source:
                frames.append(source.copy())
                frame_seeds.append(None)
                frame_ids.append(source_id)
                continue
            instruction = (
                f"Repose the subject in image 1 as {subject_description or 'the same subject'} "
                f"for {action}, phase {index + 1}/{len(poses)} ({pose.phase}): "
                f"{pose.instruction}. Preserve its distinctive appearance, art style, "
                "view, scale, and canvas placement."
            )
            frame_seed = None if seed is None else (seed + index) % 2**32
            images, seeds = await service.request({
                "mode": "pose", "prompt": instruction,
                "target_size": list(source.size),
                "frames": ([{"image": png_b64(source)},
                            {"image": png_b64(guides[index])}]
                           if guides[index] is not None else [{"image": png_b64(source)}]),
                "variants": 1, "seed": frame_seed, "background": "remove",
            }, _progress(ctx))
            frame = images[0]
            if frame.size != source.size or not frame.getchannel("A").getbbox():
                raise ValueError(f"generated {action} frame {index} is empty or wrong-sized")
            frames.append(frame)
            frame_seeds.append(seeds[0])
            frame_ids.append(store.put(frame, {
                "operation": "pose_frame", "source_id": source_id,
                "action": action, "phase": pose.phase,
                "frame_index": index, "seed": seeds[0],
                "guide_source": pose.guide_path,
                "service_protocol": service.protocol,
            })["asset_id"])
        result = _save_action_sheet(source_id, source, action, poses, frames,
                                    guides, frame_ids, frame_seeds,
                                    fps, subject_description, sheet_pivot,
                                    selection="generated")
        await ctx.report_progress(100, total=100, message="Saved action sheet")
        return result
    except (ValueError, OSError, ServiceError) as exc:
        raise ToolError(str(exc)) from exc


def _save_action_sheet(source_id, source, action, poses, frames, guides,
                       frame_ids, frame_seeds, fps, description, pivot, selection):
    sheet, rects = spritesheet(frames)
    for index, (rect, pose) in enumerate(zip(rects, poses)):
        bbox = frames[index].getchannel("A").getbbox()
        duplicate = next((earlier for earlier in range(index)
                          if frames[earlier].tobytes() == frames[index].tobytes()), None)
        rect.update({
            "file": f"frame_{index:02d}.png",
            "guide_file": f"guide_{index:02d}.png" if guides[index] is not None else None,
            "duration_ms": round(1000 / fps),
            "column": index % min(4, len(frames)),
            "row": index // min(4, len(frames)),
            "phase": pose.phase,
            "instruction": pose.instruction,
            "root_offset": list(pose.root_offset),
            "seed": frame_seeds[index],
            "asset_id": frame_ids[index],
            "visible_bbox": list(bbox),
            "touches_edge": (bbox[0] == 0 or bbox[1] == 0 or
                             bbox[2] == source.width or bbox[3] == source.height),
            "duplicate_of": duplicate,
        })
    record = store.put_animation(frames, sheet, {
        "source_id": source_id, "action": action, "motion": "pose_guided",
        "subject_description": description, "selection": selection,
        "fps": fps, "frame_count": len(frames),
        "frame_width": source.width, "frame_height": source.height,
        "columns": min(4, len(frames)),
        "rows": (len(frames) + min(4, len(frames)) - 1) // min(4, len(frames)),
        "source_pivot": pivot, "pivot": pivot, "frames": rects,
    }, fps, guides=guides)
    data = _public_animation(record)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data)),
                 _image_block(preview(sheet))],
        structured_content=data,
    )


@mcp.tool()
def compose_action_sprite(
    frame_set_id: str,
    frame_ids: list[str],
    fps: Annotated[int | None, Field(ge=1, le=30)] = None,
) -> CallToolResult:
    """Replace frames in a pose sheet, preserving its caller-defined pose metadata."""
    try:
        from pathlib import Path

        original, _ = store.get_animation(frame_set_id)
        if original.get("motion") != "pose_guided":
            raise ValueError("frame_set_id must refer to a pose-guided sheet")
        source_id = original["source_id"]
        action = original["action"]
        _, source = store.get(source_id)
        if len(frame_ids) != original["frame_count"]:
            raise ValueError(f"{action} requires {original['frame_count']} frame_ids")
        poses = [PoseFrame(phase=frame["phase"],
                           instruction=frame.get("instruction", frame["phase"]),
                           root_offset=frame["root_offset"])
                 for frame in original["frames"]]
        frames = []
        seeds = []
        for index, asset_id in enumerate(frame_ids):
            record, frame = store.get(asset_id)
            if frame.size != source.size:
                raise ValueError(f"frame {index} dimensions must match source")
            if not frame.getchannel("A").getbbox():
                raise ValueError(f"frame {index} is empty")
            frames.append(frame)
            seeds.append(record.get("seed"))
        folder = store.root / "animations" / frame_set_id
        guides = []
        for frame in original["frames"]:
            guide_file = frame.get("guide_file")
            if guide_file:
                with Image.open(Path(folder) / guide_file) as loaded:
                    guides.append(loaded.copy())
            else:
                guides.append(None)
        return _save_action_sheet(source_id, source, action, poses, frames,
                                  guides, frame_ids, seeds,
                                  fps or original["fps"],
                                  original.get("subject_description", ""),
                                  original["pivot"],
                                  selection="selected")
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
