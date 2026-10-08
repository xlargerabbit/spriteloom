import asyncio
import base64
import io
from pathlib import Path

from mcp import Client
from PIL import Image

import spriteloom_mcp.tools as tools
from spriteloom_mcp.assets import AssetStore


class FakeService:
    protocol = 1

    def __init__(self):
        self.requests = []

    async def request(self, payload, on_progress=None):
        self.requests.append(payload)
        if on_progress:
            await on_progress(0.5, "Generating")
        size = tuple(payload["target_size"])
        if payload["mode"] == "inpaint":
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            image.putpixel((1, 1), (0, 0, 255, 255))
        else:
            image = Image.new("RGBA", size, (255, 0, 0, 255))
        return [image.copy() for _ in range(payload["variants"])], list(range(100, 100 + payload["variants"]))


def test_generate_inpaint_and_inspect_through_mcp(tmp_path, monkeypatch):
    store = AssetStore(tmp_path / "assets")
    service = FakeService()
    monkeypatch.setattr(tools, "store", store)
    monkeypatch.setattr(tools, "service", service)

    async def go():
        async with Client(tools.mcp) as client:
            generated = await client.call_tool("generate_sprite", {
                "prompt": "red coin", "width": 4, "height": 4,
                "variants": 2, "seed": 100,
            })
            assert not generated.is_error
            assets = generated.structured_content["assets"]
            assert len(assets) == 2
            assert [a["seed"] for a in assets] == [100, 101]
            assert any(block.type == "image" for block in generated.content)

            source_id = assets[0]["asset_id"]
            refined = await client.call_tool("refine_sprite", {
                "source_id": source_id, "instruction": "make one dot blue",
                "operation": "inpaint", "region": [1, 1, 1, 1], "variants": 1,
            })
            assert not refined.is_error
            target_id = refined.structured_content["assets"][0]["asset_id"]
            _, source = store.get(source_id)
            _, target = store.get(target_id)
            assert source.getpixel((1, 1)) == (255, 0, 0, 255)
            assert target.getpixel((0, 0)) == (255, 0, 0, 255)
            assert target.getpixel((1, 1)) == (0, 0, 255, 255)

            mask_bytes = base64.b64decode(service.requests[1]["frames"][0]["mask"])
            with Image.open(io.BytesIO(mask_bytes)) as mask:
                assert mask.convert("L").getpixel((1, 1)) == 255
                assert mask.convert("L").getpixel((0, 0)) == 0

            inspected = await client.call_tool("inspect_asset", {
                "asset_id": target_id, "full_size": True,
            })
            assert inspected.structured_content["color_count"] == 2
            assert inspected.structured_content["opaque_pixels"] == 16
            assert any(block.type == "image" for block in inspected.content)

    asyncio.run(go())


def test_bad_inpaint_region_fails_before_service_call(tmp_path, monkeypatch):
    store = AssetStore(tmp_path / "assets")
    record = store.put(Image.new("RGBA", (4, 4), "red"), {"operation": "import"})
    service = FakeService()
    monkeypatch.setattr(tools, "store", store)
    monkeypatch.setattr(tools, "service", service)

    async def go():
        async with Client(tools.mcp) as client:
            result = await client.call_tool("refine_sprite", {
                "source_id": record["asset_id"], "instruction": "blue dot",
                "operation": "inpaint", "region": [3, 3, 2, 2],
            })
            assert result.is_error
            assert "region must fit" in result.content[0].text
            assert service.requests == []

    asyncio.run(go())


def test_import_then_animate_exports_retrievable_sheet(tmp_path, monkeypatch):
    store = AssetStore(tmp_path / "assets")
    monkeypatch.setattr(tools, "store", store)
    source = tmp_path / "source.png"
    Image.new("RGBA", (8, 8), (30, 40, 50, 255)).save(source)

    async def go():
        async with Client(tools.mcp) as client:
            imported = await client.call_tool("import_sprite", {"path": str(source)})
            assert not imported.is_error
            asset_id = imported.structured_content["assets"][0]["asset_id"]
            animated = await client.call_tool("animate_sprite", {
                "source_id": asset_id, "motion": "idle_bob", "frames": 8,
                "fps": 8, "pivot": [2, 7],
            })
            assert not animated.is_error
            data = animated.structured_content
            assert data["frame_count"] == 8
            assert len(data["frames"]) == 8
            assert data["source_pivot"] == [2, 7]
            assert data["pivot"] == [6, 11]
            assert data["columns"] == 4 and data["rows"] == 2
            assert [(frame["row"], frame["column"]) for frame in data["frames"]] == [
                (i // 4, i % 4) for i in range(8)
            ]
            assert all(Path(path).is_file() for path in data["frame_paths"])
            assert Path(data["sheet_path"]).is_file()
            assert Path(data["preview_gif_path"]).is_file()

            inspected = await client.call_tool("inspect_asset", {
                "asset_id": data["frame_set_id"], "full_size": True,
            })
            assert inspected.structured_content["frame_count"] == 8
            assert any(block.type == "image" for block in inspected.content)

    asyncio.run(go())


def test_caller_defined_poses_export_fixed_cells_and_metadata(tmp_path, monkeypatch):
    store = AssetStore(tmp_path / "assets")
    service = FakeService()
    monkeypatch.setattr(tools, "store", store)
    monkeypatch.setattr(tools, "service", service)
    source = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for y in range(4, 29):
        for x in range(9, 24):
            source.putpixel((x, y), (255, 0, 0, 255))
    asset_id = store.put(source, {"operation": "import"})["asset_id"]
    guide_path = tmp_path / "curl.png"
    Image.new("RGB", source.size, "white").save(guide_path)
    poses = [
        {"phase": "rest", "instruction": "coiled dragon at rest", "reuse_source": True},
        {"phase": "curl", "instruction": "curl the tail around the body",
         "guide_path": str(guide_path), "root_offset": [2, -1]},
        {"phase": "stretch", "instruction": "stretch the wings", "root_offset": [4, 0]},
    ]

    async def go():
        async with Client(tools.mcp) as client:
            result = await client.call_tool("generate_action_sprite", {
                "source_id": asset_id, "action": "coil", "poses": poses, "seed": 21,
                "subject_description": "red dragon", "fps": 7, "pivot": [12, 20],
            })
            assert not result.is_error, result.content[0].text
            data = result.structured_content
            assert data["frame_count"] == 3
            assert data["frame_width"] == data["frame_height"] == 32
            assert [f["root_offset"] for f in data["frames"]] == [
                [0, 0], [2, -1], [4, 0]]
            assert [r["seed"] for r in service.requests] == [22, 23]
            assert [len(r["frames"]) for r in service.requests] == [2, 1]
            assert data["frames"][0]["visible_bbox"] == [9, 4, 24, 29]
            assert all(frame["touches_edge"] for frame in data["frames"][1:])
            assert data["guide_paths"][0] is None
            assert Path(data["guide_paths"][1]).is_file()
            assert data["guide_paths"][2] is None
            assert data["fps"] == 7
            assert data["pivot"] == [12, 20]
            assert "sword" not in service.requests[0]["prompt"].lower()
            assert all(store.get(frame["asset_id"])[1].size == source.size
                       for frame in data["frames"])
            _, sheet = store.get_animation(data["frame_set_id"])
            assert sheet.size == (96, 32)

            selected = await client.call_tool("compose_action_sprite", {
                "frame_set_id": data["frame_set_id"],
                "frame_ids": [frame["asset_id"] for frame in data["frames"]],
            })
            assert not selected.is_error
            assert selected.structured_content["selection"] == "selected"
            assert selected.structured_content["frame_count"] == 3
            assert selected.structured_content["pivot"] == [12, 20]
            assert selected.structured_content["frames"][1]["root_offset"] == [2, -1]
            assert Path(selected.structured_content["guide_paths"][1]).is_file()

    asyncio.run(go())
