# Pose-guided sprite animation with FLUX.2 Klein

## Goal

Turn one character sprite into usable ARPG idle, run, attack, and dash sheets. A usable sheet needs recognizable character identity, readable action poses, a stable pixel grid and palette, transparent frames, and explicit timing and root-motion metadata.

## Current Spriteloom behavior

`mcp/spriteloom_mcp/animation.py` transforms one flattened image with bob, bounce, pulse, recoil, or palette cycling. This reliably creates small idle and effect loops, but it cannot move limbs, tails, or weapons independently. The existing `animate_sprite` tool exports frames, a sheet, a GIF, and a manifest with fixed cells.

The Klein service supports text-to-image, instruction editing, and inpainting. Its edit route passes one source image. In `server/main.py`, edits are cropped to the detected subject and fitted to the target canvas separately; this is useful for standalone variants but can change character scale and placement between animation frames. The animation route needs one shared canvas, scale, palette, and pivot throughout a clip.

## Options considered

| Method | Strength | Main limitation | Decision |
|---|---|---|---|
| Whole-image transforms | Fast, deterministic, preserves exact pixels | No new body pose | Keep for simple idle/effects |
| Independent Klein edit for every frame | Easy to prototype | Identity and proportion drift | Baseline only |
| Generate an entire sheet in one image | Frames share one model context | Weak pose/timing control and limited pixel budget per cell | Baseline only |
| Pose guides, Klein keyframes, deterministic finishing | Motion is explicit; model fills missing pixels | Needs guides and selection/cleanup | Build first |
| Train a sprite animation model | Could learn consistent action cycles | Needs a large labelled dataset and training work | Revisit after benchmark |

FLUX.2 Klein supports single-image and multi-reference editing. Black Forest Labs documents using one image for identity and another for pose. This makes a pose guide plus character reference the most promising use of the model already resident in Spriteloom. This is an inference from documented image-editing features, not proof of animation quality. A single image cannot specify hidden limbs or unseen costume surfaces, so the tool must expose keyframes for review.

## Recommended pipeline

1. Import a source sprite and choose an action and facing direction.
2. Construct a fixed-canvas sequence of coarse pose guides and explicit timing, pivot, and root offsets. Keep the source image as the identity reference. Start with one facing direction; do not mirror asymmetric weapons without review.
3. Render a small number of keyframes with Klein, using the source plus pose guide as references. For minor movement, reuse source pixels or make a deterministic warp. Generate alternatives for difficult poses.
4. Apply one palette across the clip; keep transparency and pixel edges crisp. Align by the clip pivot, never by per-frame opaque bounding boxes. Detect empty frames, clipped artwork, identity drift, and duplicate poses.
5. Fill only uncomplicated in-betweens deterministically, then export equal-size PNG cells, individual frames, a preview GIF, and a manifest containing durations, action phase, pivot, and root offsets.

Suggested first clips: four-frame idle, six-frame run, five-frame attack, and four-frame dash. These are evaluation targets, not claims that the model can generate all four reliably. Idle may need only a few redrawn details; run needs contact and passing poses; attack needs anticipation, strike, and recovery; dash needs root displacement and an optional separate effect.

## Implementation order and acceptance criteria

1. Add a fixed-canvas postprocess path and a small pose-guide generator. Preserve the existing edit behavior.
2. Add a server request that can use source and guide references with the resident Klein pipeline, then expose it through MCP as an action/keyframe workflow.
3. Export reviewed frames and a sheet through the existing asset-store conventions.
4. Compare the new path against independent per-frame edits and whole-image transforms on the swordcat at native 128x128. Record model latency, manual correction time, character identity, pose readability, frame alignment, palette consistency, transparency, and loop quality.

The proof of concept succeeds if each action has visibly distinct intended poses, consistent character features, transparent fixed-size frames, correct timing/root metadata, and a sheet that can be sliced without per-frame repositioning. Generated art remains subject to visual review; passing structural checks alone does not establish production quality.

## Prototype result, 2026-10-08

The first implementation adds a `pose` service mode and the experimental MCP `generate_action_sprite` tool. It uses two Klein references (the source character and a pose diagram), keeps the postprocess canvas fixed, and exports guide PNGs alongside each sheet. User-supplied same-size guide PNGs can replace the generated diagrams. Each generated frame is also an asset that can be inspected/refined and selected into a new sheet with `compose_action_sprite`. The existing `animate_sprite` behavior remains available.

The swordcat live pilot generated four action sheets in the repository root: `swordcat_pose_idle`, `swordcat_pose_run`, `swordcat_pose_attack`, and `swordcat_pose_dash`. The trial used 4, 6, 5, and 4 frames respectively. All frames were 128x128, used only source-palette colors, had transparent backgrounds, and avoided contact with the canvas edge. Idle had three unique images because its neutral source frame intentionally repeats; every run, attack, and dash frame was distinct. The alpha bounding-box bottoms ranged 113–123 for run, 119–123 for attack, and 111–123 for dash. These numbers include the sword and are a rough alignment diagnostic, not a foot-tracking measurement.

Visual review found readable changes in run stance, a raised-sword windup in attack, and leaning/trailing poses in dash. It also found inconsistent shading and occasional changes to leg or sword shapes. The generated sheets are draft animation references, not production-ready clips. The pilot used a coarse humanoid guide; it did not reconstruct a rig or hidden body parts from the single image. The current interface instead accepts caller-defined pose instructions, optional guide PNGs, root offsets, and timing for any subject. Artist-authored pose guides and per-frame selection are likely to improve action quality. The next useful improvement is caller-defined anchors during keyframe review, followed by deterministic in-between generation only where the two accepted keyframes support it.

## Sources

- Black Forest Labs, FLUX.2 official inference repository: https://github.com/black-forest-labs/flux2
- Black Forest Labs, multi-reference editing guidance: https://github.com/black-forest-labs/skills/blob/master/skills/flux-image-best-practices/rules/multi-reference-editing.md
- Hugging Face Diffusers, FLUX.2 pipelines: https://huggingface.co/docs/diffusers/main/api/pipelines/flux2
- Hugging Face Diffusers, ControlNet guide (its Flux examples do not establish FLUX.2 Klein compatibility): https://huggingface.co/docs/diffusers/using-diffusers/controlnet
