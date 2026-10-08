# Spriteloom architecture

This document describes the implementation as it exists in the repository. It is intended as a map for future contributors and coding sessions; `README.md` remains the user installation and operation guide.

## Product shape

Spriteloom is a local AI pixel-art assistant controlled from Aseprite. The Lua extension collects task settings and sprite data, a Python service runs FLUX.2 Klein locally, and generated images return to Aseprite as new layers. The launcher is a Windows desktop shell for setup and server lifecycle. Inference is intentionally local: the service binds to loopback and the model is downloaded and stored outside the packaged launcher.

```text
Aseprite 1.3+                         Windows launcher
  Lua extension                            Python + pywebview
  dialogs / prompt / layers                 setup / config / process
          | localhost WebSocket                    |
          +----------------------> Python server <-+
                                    protocol / jobs
                                         |
                                   Klein pipeline
                                         |
                                   postprocessing
                                         |
                              RGBA pixels + seeds + progress
```

The launcher starts and supervises the service; the plugin communicates with the service directly. The launcher is not a request proxy.

An optional headless MCP adapter in `mcp/` lets an agent use the same loopback service without Aseprite. It owns persistent asset IDs, sprite refinement chains, and deterministic idle/effect spritesheet export. The MCP process can attach to the launcher-owned service or start its own local service; it stops only a child it started.

## Runtime components and layers

### Aseprite extension (`plugin/`)

Lua modules are loaded by `plugin/main.lua`, which registers the menu command and keyboard shortcut. `dialogs.lua` owns the main task panel, advanced settings, validation hints, server status polling, and request lifecycle. `prompt.lua` assembles the user-facing prompt text and is deliberately a pure module. `client.lua` implements the WebSocket client and reads the service port from the installed extension's `server.json` (falling back to the default). `sprite.lua` converts Aseprite frames and selections into request images/masks and turns returned raw RGBA bytes into in-memory Aseprite images. `results.lua` and `history.lua` display outputs and past runs; clicking a variant inserts it as a separate layer.

The extension owns editor interaction and never edits existing pixels. Input images and masks are sent to the service; output variants are inserted as new layers. The plugin API and Aseprite UI are runtime dependencies, while most logic can be exercised using the stub API in `plugin/tests/`.

### Launcher (`launcher/`)

`launcher/app.py` is the pywebview host and JavaScript bridge for `launcher/ui/`. It joins setup state, path selection, plugin installation, settings and server controls. `paths.py` resolves the install root, Python environment, Aseprite location and model directory. `setup_checks.py` detects missing prerequisites; `setup_steps.py` runs the selected installation steps and streams logs. `plugin_install.py` copies/updates the extension and writes its `server.json` port configuration.

`server_proc.py` starts the service using the install's virtual-environment Python, probes the WebSocket endpoint, collects logs, and stops the child process. On Windows the child is put in a kill-on-close job object so closing the launcher also releases the service and GPU memory. `build.spec` packages only the launcher and UI: PyTorch, Diffusers, and model weights stay external.

### Service boundary (`server/protocol.py`, `server/main.py`)

The service binds to `127.0.0.1` on the configured port (default `8765`). The wire format is JSON over WebSocket. Requests carry an ID, mode (`generate`, `edit`, `inpaint`, `instruct`), prompt, target size, variant count, seed, palette/background options, and optional image/mask frames. `protocol.py` validates and normalizes requests at the boundary, including dimensions, mode, frame requirements, seed, palette, and variant limits. Image inputs use base64 PNG; results use base64-encoded raw RGBA bytes to avoid temporary files and Aseprite Recent Files pollution.

The service also handles lightweight ping/model readiness and paginated history messages. Generation progress and stage labels are streamed using the request ID; terminal replies contain variants and their seeds or an error. Each connection can issue requests, while a single-worker executor serializes GPU generation. Disconnecting during generation cancels the worker's send path. Keep protocol validation at this boundary rather than letting malformed data reach model or image routines.

### Model and generation (`server/models.py`, `server/instruct.py`)

`models.py` is a small named-pipeline registry and lazy resident-model lifecycle. `main.py` registers the Klein factory and starts loading at service startup. The shipped path is a single FLUX.2 Klein 4B pipeline with an 8-bit text encoder and bf16 transformer. `instruct.py` owns model loading, memory preflight and VRAM mode selection, prompt suffixes, deterministic per-variant seeds, image preparation, text-to-image, instruction editing, and inpainting. The transformer remains bf16 because quantizing it breaks text-to-image output in the tested configuration.

`auto` selects a mode based on available VRAM; `bf16` keeps the pipeline resident on the GPU; `offload` uses sequential CPU/GPU layer offload for lower-VRAM cards and is slower. The pipeline is shared across tasks, avoiding per-task model swaps. Model weights are external artifacts under the configured models directory, not checked into or bundled with this repository.

### Image postprocessing (`server/postprocess.py`)

The service postprocesses each raw result before returning it: background handling, subject crop where applicable, fit to target canvas, palette derivation/quantization, and optional mirror symmetry. Inpainting preserves its canvas/mask alignment and alpha composition, so its background path differs from generation/editing. These functions are largely image transformations independent of the WebSocket/UI and should remain testable without loading the model.

### Configuration and persisted data

`server/config.py` is shared by launcher and service. It stores settings in `%APPDATA%/Spriteloom/config.json` on Windows (home-directory fallback elsewhere), including port, VRAM mode and resolved paths. Writes merge keys because both components share the file. During plugin installation, the selected port is copied to `plugin/server.json`; this is the plugin's connection configuration. Keep the default host loopback and keep the config merge behavior when adding settings.

Generation history and tuning artifacts are written under `output/`, one timestamped folder per request, containing final images, settings and (for a limited number of recent runs) raw images. The plugin's History view reads this service endpoint. This is local generated data, not source or a model cache.

## Main flows

### Generate or edit

1. The user chooses a mode and options in the Aseprite panel. The plugin assembles the prompt and exports any current frame or selection mask.
2. `client.lua` sends a validated-shape JSON request to the configured loopback WebSocket endpoint.
3. `main.py` parses the request, executes `_run` on its single GPU worker and streams progress/stages to the client.
4. `instruct.py` calls the resident Klein pipeline for text-to-image, instruction edit, or inpaint. `main.py` applies postprocessing and stores run history/debug artifacts.
5. The service returns image dimensions, raw RGBA pixels, and per-variant seeds. The plugin creates in-memory images and presents a variant grid; a click inserts a new layer.

### Setup and launch

The user launches the packaged pywebview application. It detects prerequisites and offers setup steps for the Python environment, packages, plugin, model, optional MCP tools, and shortcut. MCP tools install into `mcp/.venv` from the sidecar `mcp/` package; they do not gate Start. Setup is explicit; Start remains unavailable until required pieces are installed. On Start, the launcher chooses/probes a port, starts the service subprocess, and monitors readiness/log output. It closes the subprocess job when the launcher exits.

## Technology stack

| Area | Technologies |
|---|---|
| Editor integration | Aseprite 1.3+, Lua 5.4 API, JSON/WebSocket client |
| Desktop launcher | Python, pywebview, HTML/CSS/JavaScript, WebView2 on Windows |
| Service | Python 3.11+, `websockets`, asyncio, `ThreadPoolExecutor` |
| Inference | PyTorch CUDA (cu128 install path), Diffusers, Transformers, Accelerate, PEFT, bitsandbytes, safetensors; FLUX.2 Klein 4B |
| Image processing | Pillow, NumPy, SciPy |
| Packaging | PyInstaller; launcher only |
| Headless agent adapter | Python MCP SDK, stdio transport, Pillow; code in `mcp/` |
| Tests | pytest for Python modules; Lua tests and luacheck for plugin modules |

Dependency ranges and install commands live in `server/requirements.txt`, `launcher/requirements.txt`, and `README.md`; consult those files when changing versions or install behavior.

## Design principles visible in the code

- **Local-first and private by architecture:** the service uses loopback networking and model execution is local.
- **One resident model:** all image tasks share Klein, reducing load/swap complexity and making readiness meaningful.
- **Small explicit boundaries:** the WebSocket protocol parses and validates untrusted request data; shared config has one implementation.
- **Preserve user work:** results are additive layers; input pixels are never modified in place.
- **Separate concerns by runtime:** the plugin owns Aseprite UX, the service owns inference and image processing, and the launcher owns installation and process lifecycle.
- **Keep the heavy stack out of the launcher:** installable model and CUDA dependencies are external to the small executable.
- **Make deterministic work reproducible:** seeds are recorded and returned per variant; run metadata is saved with history.
- **Prefer pure/testable logic at seams:** prompt assembly and postprocessing are independently testable; plugin UI tests use a stubbed Aseprite API.

## Extension guidance

- A new task should define its request fields and validation in `protocol.py`, route execution in `main.py`, and implement model behavior in `instruct.py` (or a clearly separated model module). Preserve one GPU worker unless concurrency is intentionally redesigned.
- Keep client/server wire changes coordinated with `plugin/client.lua` and the relevant panel code. Handle progress, errors, and cancellation as part of the flow, not just successful results.
- Add image transformations to `postprocess.py` when they are independent of UI/model execution, with focused tests. Mind mode-specific alpha, crop and mask alignment constraints.
- Add persistent settings through `server/config.py` and merge them; if the plugin needs a value, update installation of `server.json` too.
- Preserve local-only binding, additive layer insertion, and the launcher's kill-on-close process ownership unless the product design explicitly changes.
- Tests are organized under `server/tests/`, `launcher/tests/`, and `plugin/tests/`. Model smoke tests may require GPU, model files and substantial RAM/VRAM; most boundary, UI-stub, setup and postprocess tests do not.

## Repository map

```text
launcher/       Windows setup and lifecycle UI
mcp/            Local MCP tools, asset store and deterministic animation
plugin/         Aseprite Lua extension
server/         WebSocket API, model lifecycle, inference and postprocess
assets/         README artwork and icon
site/           Static project page
build.spec      PyInstaller launcher recipe
```

The public README documents supported installation and user workflows. `TODO.md` records known investigation and platform-support caveats; treat it as project status rather than architecture guarantees.
