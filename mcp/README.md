# Spriteloom MCP server

This is the local, headless agent interface to Spriteloom's GPU service. It exposes `generate_sprite`, `import_sprite`, `refine_sprite`, `inspect_asset`, and `animate_sprite`. The [implementation plan](../docs/MCP_IMPLEMENTATION_PLAN.md) records the scope and build order.

The MCP server uses stdio. It connects to the Spriteloom WebSocket service on `127.0.0.1` and the port in `%APPDATA%/Spriteloom/config.json` (default `8765`). When the service is offline on Windows, it starts the installed service from the repository's `.venv`. It never needs Aseprite. The inference environment and model must first be installed using the normal Spriteloom setup.

## Install

From the repository root on Windows:

```bat
py -3.11 -m venv mcp\.venv
mcp\.venv\Scripts\python -m pip install -e mcp
```

Configure an MCP client to run `mcp\.venv\Scripts\spriteloom-mcp.exe` using an absolute path. If the client launches it from another working directory, set `SPRITELOOM_ROOT` to the Spriteloom installation directory.

The optional `SPRITELOOM_MCP_ASSETS` environment variable selects where generated assets are saved. By default they go to `%APPDATA%/Spriteloom/mcp-assets/`, one folder per asset ID. Set `SPRITELOOM_MCP_AUTOSTART=0` if you prefer to start the Spriteloom service in the launcher yourself.

## Agent workflow

1. Call `generate_sprite` with a prompt and target canvas size. It returns variant IDs, seeds, local PNG paths, and a contact sheet preview.
2. Call `inspect_asset` to view a chosen variant. Set `full_size=true` to return its full PNG through MCP.
3. Call `refine_sprite` with the chosen ID and an instruction. Use `operation="edit"` for a whole-sprite change, `operation="inpaint"` with `[x,y,width,height]` or a mask path for a local change, or `operation="instruct"` for a new view.
4. Each result has a new ID; the source asset remains available. Use `import_sprite` to begin from an existing local PNG.
5. Call `animate_sprite` on a chosen asset for `idle_bob`, `bounce`, `pulse`, `recoil`, or `palette_cycle`. It saves individual PNG frames, a spritesheet, a GIF preview, and frame metadata. Call `inspect_asset` with the returned frame set ID to view the sheet.

For inpaint, the MCP server composites the service's transparent patch over the source and saves a complete new PNG. Paths passed to `import_sprite` and `mask_path` are on the same machine as this MCP process.

Animation presets transform pixels from one flattened image. They do not generate new poses or independently moving limbs. The spritesheet uses equal-size transparent cells and records each frame rectangle, duration, and pivot in its manifest.

## Tests

Install the optional test dependency, then run the MCP suite from the repository root:

```bat
mcp\.venv\Scripts\python -m pip install -e "mcp[test]"
mcp\.venv\Scripts\python -m pytest mcp\tests
```

These tests use a simulated WebSocket service and temporary assets; they do not load the GPU model.
