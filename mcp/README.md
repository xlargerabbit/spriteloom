# Spriteloom MCP server

This is the local, headless agent interface to Spriteloom's GPU service. It exposes `generate_sprite`, `import_sprite`, `refine_sprite`, `inspect_asset`, and `animate_sprite`. The [implementation plan](../docs/MCP_IMPLEMENTATION_PLAN.md) records the scope and build order.

The MCP server uses stdio. It connects to the Spriteloom WebSocket service on `127.0.0.1` and the port in `%APPDATA%/Spriteloom/config.json` (default `8765`). When the service is offline on Windows, it starts the installed service from the repository's `.venv`. It never needs Aseprite. The inference environment and model must first be installed using the normal Spriteloom setup.

## Install

The Windows release zip includes this `mcp/` package. In the launcher, open
**Setup**, select the optional **MCP tools** row, and press **Install selected**.
The launcher creates `mcp/.venv` and installs the adapter there. The GPU
environment and model are separate Setup items. Configure an stdio MCP client
to run the installed executable (replace the example path):

```json
{
  "command": "C:\\Spriteloom\\mcp\\.venv\\Scripts\\spriteloom-mcp.exe",
  "env": { "SPRITELOOM_ROOT": "C:\\Spriteloom" }
}
```

To install without the launcher, run these commands from the extracted release
root. This creates the same environment as the Setup row:

```bat
py -3.11 -m venv mcp\.venv
mcp\.venv\Scripts\python -m pip install -e mcp
```

You can also use [uv](https://docs.astral.sh/uv/) to run the bundled package
without the launcher installation: `uv tool run --python 3.11 --from
C:\Spriteloom\mcp spriteloom-mcp`. Set `SPRITELOOM_ROOT` in the MCP client
environment because uv keeps its tool environment outside the release folder.

The optional `SPRITELOOM_MCP_ASSETS` environment variable selects where generated assets are saved. By default they go to `%APPDATA%/Spriteloom/mcp-assets/`, one folder per asset ID. Set `SPRITELOOM_MCP_AUTOSTART=0` if you prefer to start the Spriteloom service in the launcher yourself.

## Configure Codex CLI on Windows

This example assumes the release zip was extracted to `C:\Spriteloom` and
Codex CLI runs on that same Windows machine. Change both paths if you extracted
it elsewhere. This guide uses native Windows Codex; WSL needs different path
and networking setup.

1. Run `Spriteloom.exe`, open **Setup**, and select **MCP tools**. Press
   **Install selected** and wait until the row shows the installed version.
   Complete the server and model Setup items too before generating sprites.
   Confirm `codex --version` works in PowerShell.
2. Register the installed stdio server in PowerShell. Codex starts the MCP
   adapter when needed; the adapter connects to the local GPU service.

   ```powershell
   codex mcp add spriteloom --env 'SPRITELOOM_ROOT=C:\Spriteloom' -- 'C:\Spriteloom\mcp\.venv\Scripts\spriteloom-mcp.exe'
   ```

3. Open `$HOME\.codex\config.toml` and add these lines immediately below
   the existing `[mcp_servers.spriteloom]` header. Keep its `command`, `args`,
   and `env` entries. The tool timeout permits a long model load or generation
   request.

   ```toml
   startup_timeout_sec = 120
   tool_timeout_sec = 900
   ```

4. Run `codex mcp get spriteloom` to check the saved command and environment,
   then start a new Codex session. In its terminal UI, run `/mcp` and check
   that Spriteloom is available. Ask Codex to use `import_sprite` on a local
   PNG and then `inspect_asset` on the returned asset ID; these two tools work
   without a GPU request. Try `generate_sprite` after the service is ready.

You can use a config file entry instead of step 2. Add this to
`$HOME\.codex\config.toml` (TOML single-quoted strings keep Windows
backslashes literal):

```toml
[mcp_servers.spriteloom]
command = 'C:\Spriteloom\mcp\.venv\Scripts\spriteloom-mcp.exe'
startup_timeout_sec = 120
tool_timeout_sec = 900

[mcp_servers.spriteloom.env]
SPRITELOOM_ROOT = 'C:\Spriteloom'
```

Codex CLI and its IDE extension share MCP configuration. See the
[official Codex MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
for other clients and configuration options.

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
