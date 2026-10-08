# Spriteloom MCP implementation plan

## Goal and scope

Build a local, headless MCP server that lets an agent use Spriteloom's existing model capabilities to generate and refine game sprites. Implement it in a dedicated top-level `mcp/` directory. After that interface works end to end, add deterministic idle and effect animation and export a spritesheet from one selected asset.

This plan does not include controlling Aseprite or editing `.aseprite` documents. Aseprite headless automation is a separate future MCP project. Walk, attack, and pose generation are also outside the initial animation phase; decide whether to pursue them after evaluating the first release.

## Implementation status

The local stdio MCP server, its five tools, persistent asset store, and deterministic idle/effect spritesheet export now exist in `mcp/`. Phase 2 supports fixed-canvas one-frame and motion sheets, custom pivots, row/column frame metadata, lossless RGBA PNG frames, and a GIF preview. The local test suite has exercised generate → inpaint → inspect against a simulated Spriteloom WebSocket service and import → animate → inspect through MCP. A real GPU generation run on supported Windows hardware is still needed before treating Phase 1 as complete. The server remains loopback-only.

## Repository shape

```text
mcp/
  README.md
  pyproject.toml
  spriteloom_mcp/      # package name avoids collision with the MCP SDK's `mcp`
    __main__.py        # stdio MCP entry point
    tools.py           # public tool interface and result shaping
    service.py         # Spriteloom WebSocket client and process ownership
    assets.py          # persistent assets, metadata, previews, IDs
    images.py          # PNG/raw RGBA conversion and mask compositing
    animation.py       # added in phase 2: deterministic frame generation
  tests/               # contract and image-pipeline tests
docs/
  MCP_IMPLEMENTATION_PLAN.md
```

These are planned responsibilities, not a requirement to create every module before it is useful. Add MCP dependencies separately from `server/requirements.txt` so the Aseprite plugin and launcher do not acquire MCP dependencies. Keep inference in the existing `server/` process; `mcp/` is an adapter and workflow layer.

## Architecture and ownership

```text
Agent / MCP client
       | stdio MCP tools, progress and image previews
       v
 mcp/       ---- persistent asset store
       | localhost JSON/WebSocket
       v
 server/main.py -> one GPU worker -> Klein -> postprocess
```

- The MCP process reads the port and relevant paths from `server/config.py`. It attaches to a responsive existing Spriteloom service. If none exists and setup is complete, it starts one using the configured virtual-environment Python and waits for model readiness. It only stops a child process that it started. The launcher and MCP process must not each start a second model on another port merely because one is already warming.
- Use a process identity/readiness check before attaching. The current `ping` response exposes model readiness but no service identity; add a backwards-compatible identity/version field if needed. Treat a busy port that does not answer as Spriteloom as an error.
- Preserve the service's single GPU worker. Concurrent MCP requests may queue, but must not create additional model instances. Relay generation stage/progress to MCP clients and close the WebSocket on cancellation. Report service load failure, missing model, GPU memory failure, and disconnect distinctly.
- The MCP server uses local stdio first. Keep stdout exclusively for MCP protocol messages; send diagnostics to stderr. It should work without Aseprite or the Windows launcher being open.

## Phase 1: existing Spriteloom features

### Public tool interface

Keep the agent-facing interface small. Tool outputs contain structured metadata and a compact image/contact-sheet preview; generated files remain in the asset store rather than being sent as large raw pixel fields.

| Tool | Inputs | Result |
|---|---|---|
| `generate_sprite` | Prompt, width, height; optional variants (1–8), seed, background, pinned palette | New immutable asset IDs, dimensions, seeds, preview contact sheet |
| `import_sprite` | Local PNG path | Asset ID and preview, enabling refinement of an existing sprite |
| `refine_sprite` | Source asset ID, operation (`edit`, `inpaint`, or `instruct`), instruction; optional region/mask, variants, seed, palette | New asset IDs, previews, lineage and seeds |
| `inspect_asset` | Asset ID | PNG preview, dimensions, transparency/palette summary, operation and source metadata |

`generate_sprite` calls the existing `generate` mode. `refine_sprite` maps to `edit`, `inpaint`, or `instruct`; `instruct` uses the same model edit path as `edit` today. Keep prompt text explicit for the first release so the agent can state the requested camera angle or style directly; prompt presets can be shared with the plugin later if they prove useful.

For `inpaint`, require a rectangle or mask and construct a canvas-sized mask. The service returns a transparent patch intended for a new Aseprite layer. The MCP workflow must composite that patch over the source to produce a complete new asset, while retaining the patch as a diagnostic artifact if useful. Preserve exact source dimensions and alignment. For all refinement operations, give the agent both the original and resulting asset IDs so it can compare or continue from either one.

Use explicit types and bounds at the MCP interface: canvas size, variant limit, seed range, allowed background modes, palette entries, PNG size, and valid mask dimensions. Surface actionable errors before forwarding invalid requests. Never replace source images when refining.

### Asset store

Store MCP assets outside the repository, under a configurable directory such as `%APPDATA%/Spriteloom/mcp-assets/` on Windows. Each asset has a stable opaque ID, a canonical RGBA PNG, a small preview, and a JSON manifest. The manifest records dimensions, operation, prompt/instruction, seed, source ID, service version, palette/background options, and creation time. Variants receive separate IDs. Use atomic writes so an interrupted request cannot expose a partial asset.

Treat IDs and manifests as the cross-call interface. Do not depend on the service's timestamped `output/` history folders or on MCP session memory. The service may keep its current history behavior, but the MCP workflow must be able to reopen assets after the MCP process restarts. Accept input paths only for `import_sprite`; resolve them to local files and copy them into the asset store before further work.

### Implementation steps

1. Add `mcp/` packaging, stdio entry point, dependency declaration, and configuration for asset storage and optional service autostart.
2. Implement the service adapter for `ping`, startup/ownership, request IDs, streamed progress, terminal result/error, timeouts, and cancellation. Decode raw RGBA replies into Pillow images.
3. Implement the persistent asset store and preview/contact-sheet generation.
4. Expose `generate_sprite`, `import_sprite`, `refine_sprite`, and `inspect_asset` with structured results and image previews.
5. Document client configuration and a complete generate → inspect → refine example. Add focused tests for protocol translation, process ownership, artifact persistence, and inpaint composition. Run a real GPU smoke flow on supported hardware before declaring this phase complete.

### Phase 1 completion criteria

- An agent can generate variants, visually inspect them, select one by ID, edit or inpaint it, and retrieve the final PNG without Aseprite.
- Seeds and source lineage survive an MCP process restart; a result can be revisited by ID.
- An existing Spriteloom service is reused; the MCP process cleans up only a service it owns.
- Long operations show progress, cancellation does not leave an orphaned MCP-owned process, and failures state what the user can fix.

## Phase 2: idle and effect animation

Add one tool after Phase 1 is usable:

| Tool | Inputs | Result |
|---|---|---|
| `animate_sprite` | Source asset ID, motion preset, frame count, FPS, optional strength/pivot | Frame set ID, spritesheet PNG, preview GIF, per-frame PNGs, JSON manifest |

Start with deterministic pixel-art presets that can be derived from one flat image: idle bob/hover, bounce, pulse/squash, small recoil, and palette cycling for suitable effects. Do not imply that these produce new poses or independent limb movement. All transforms use transparent RGBA, nearest-neighbor sampling and integer pixel placement where possible. Keep a stable canvas, pivot and baseline across frames; avoid cropping each frame independently. A simple static source should also be exportable as a one-frame sheet.

The spritesheet uses equal-size cells with an explicit row/column order. Its manifest records frame rectangles, frame duration/FPS, pivot, motion preset and parameters, source asset ID, and paths to each frame. Produce a GIF preview for quick review, while PNG frames and the sheet remain the lossless deliverables. Keep the animation code independent of the GPU service so this phase can be developed and verified without model loading.

### Phase 2 completion criteria

- A Phase 1 asset ID alone is sufficient to generate a repeatable idle/effect sheet and preview.
- Every frame keeps the same canvas and pivot; the sheet metadata maps exactly to its cells.
- Transparent edges, palette colors and crisp pixel boundaries survive export.

## Evaluation before further work

Use representative characters, props, and effects to evaluate the Phase 2 output. Decide whether to pursue model-assisted walk/action/turn animation only after observing identity consistency, motion quality, cost, and the amount of manual cleanup needed. Such work would need its own design for pose control and temporal consistency. Keep the headless Aseprite MCP separate from this decision.
