# EffectCraft MCP

An AI-controllable fork of [storytold/effectcraft](https://github.com/storytold/effectcraft) — an open-source After Effects-style compositor written in pure Rust. This repository keeps the upstream engine, licenses, and attribution, and drives it with a Model Context Protocol server: stdio for desktop clients, and loopback HTTP for local agents.

![Index, a 12-second editorial title](examples/index/index.gif)

**Index** is a 1920×1080, 30 fps, 12-second title built only through the MCP tools: flat paper `#F3F0E8`, ink `#141414`, and one red mark. No gradients, glows, or blurs.

| | |
|---|---|
| Film | [`examples/index/index.mp4`](examples/index/index.mp4) |
| Project | [`examples/index/index.ecproj`](examples/index/index.ecproj) |
| Stills | [`examples/index/frames/`](examples/index/frames/) |
| Builder | [`examples/index/build_index.py`](examples/index/build_index.py) |

## Setup

Rust **1.95+** (this tree uses edition 2024). The first build fetches the FilmCraft codec crates from GitHub.

```sh
cargo build --release -p effectcraft-cli
./target/release/effectcraft-cli --version
```

Headless frames render on the CPU. `--gpu` attaches the GPU compositor when an adapter is available.

The desktop app, the CLI, and the MCP server share one command registry, so a tool call is the same operation a menu would run. Upstream's product README is preserved at [docs/UPSTREAM.md](docs/UPSTREAM.md). Deeper agent notes live in [docs/agents.md](docs/agents.md) and [docs/mcp.md](docs/mcp.md).

## MCP client config

Stdio is the transport Cursor, Claude Code, and Claude Desktop expect. From this repo, after a release build:

```json
{
  "mcpServers": {
    "effectcraft": {
      "command": "/absolute/path/to/effectcraft-mcp/target/release/effectcraft-cli",
      "args": ["mcp", "--empty"]
    }
  }
}
```

[`/.mcp.json`](.mcp.json) registers that server, plus `effectcraft-demo`, which opens the Index project. Add `--demo` to boot the upstream sample project, or `--project path/to/file.ecproj` to open a file. `--autosave` checkpoints a dirty headless project before each tool reply.

Loopback HTTP speaks the same JSON-RPC messages, one request at a time. It binds only `127.0.0.1` or `localhost`; any other address is rejected.

```sh
./target/release/effectcraft-cli mcp --empty --http 127.0.0.1:8765
```

```sh
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

`POST /shutdown` stops the HTTP server. Progress notifications for a long render stay on the stdio transport.

A running desktop app can be driven instead of the headless session:

```sh
effectcraft --control 9877
effectcraft-cli mcp --bridge 9877
```

Bridge mode adds `screenshot` and the `ui_*` tools.

## Tools

36 tools. Headless sessions expose 27; the nine UI tools appear only in bridge mode. Each tool's JSON schema is returned by `tools/list`. Unknown argument keys are rejected with the accepted list.

| Tool | What it does |
|---|---|
| `list_commands` | Engine command catalogue: id, label, menu, shortcut, parameter doc, whether it can run. `filter`, `enabled_only`, `schemas`. |
| `describe_command` | One command in full, including a JSON Schema of its parameters. |
| `execute_command` | Run any engine command by id. Edits are undoable. Unknown parameter keys are rejected. |
| `command_list` | Same catalogue as `list_commands`. |
| `command_run` | Run one command (`id`, `params`). |
| `command_batch` | Run commands in order. Each edit has its own undo step. Stops on the first error unless `stop_on_error` is false. |
| `batch` | Several commands as **one** undo step. `"$N.key"` substitutes an earlier result. A failure rolls the batch back. |
| `run_script` | JavaScript with an After Effects-style object model (`app.project`, layers, `setValueAtTime`, effects). |
| `get_state` | Active comp, time, selection, tool, plus build capabilities (command count, effects, formats). |
| `get_project` | Items, bit depth, active comp, dirty flag, undo stack. |
| `get_comp` | Comp settings and the layer stack (id, name, type, in/out, blend, parent, switches). |
| `get_layer` | A layer's property tree. Every leaf has a `path`. `flat: true` returns a compact list. |
| `get_property` | One property at a comp time: value, keyframes, interpolation, expression. |
| `set_property` | Static value, a keyframe (`time`), or an expression. `timeBase` is `layer` or `comp`. |
| `add_keyframe` | One key or `keys: [{time, value, inEase, outEase}]`. `interpolation`: `linear`, `bezier`, `hold`, `easyEase`, `easyEaseIn`, `easyEaseOut`. `inEase` / `outEase` are `{speed, influence}` with influence in percent, applied after the shared interpolation. |
| `list_effects` | Effect registry: id, name, category, GPU and 32-bpc flags, parameters. |
| `add_effect` | Apply an effect and set parameters in one undo step. Returns `effects/#n` and each parameter path. |
| `list_fonts` | Families the text engine can use (bundled and installed), with styles. |
| `render_frame` | PNG of a frame. `path` writes it, `max_side` scales it, `transparent` keeps alpha. |
| `render_preview` | Same frame as a PNG reply, without a default file path. |
| `open_project` | Open an `.ecproj` (`path`), the built-in demo (`demo: true`), or an empty project (`new: true`). |
| `save_project` | Save, or Save As when `path` is set. |
| `undo` / `redo` | Step through the undo stack. |
| `history` | Branching history. `goto` jumps to any state without dropping the others. |
| `doc_inspect` | Project overview plus the active composition. Also published as the `effectcraft://document` resource. |
| `script_ui` | Drive ScriptUI windows a script opened: list, get, click, set, close. |
| `screenshot` | **Bridge.** PNG of the window, a panel, or an element. |
| `ui_inspect` | **Bridge.** Window, workspace, viewer, timeline, playback. |
| `ui_elements` | **Bridge.** Hit targets from the last frame (`tools.Selection`, `timeline.cti`, …). |
| `ui_click` / `ui_drag` / `ui_key` / `ui_type` / `ui_set` | **Bridge.** Click, drag, press a shortcut, type, or set viewer and timeline state. |
| `control` | **Bridge.** Any control-channel method (`ui.menu.invoke`, `ui.playback`, …). |

`effectcraft://commands` is the command catalogue as a resource.

### Engine commands the tools drive

`execute_command` / `command_run` reach the whole registry (hundreds of ids — `list_commands` is the live list). The groups used to author and render a composition:

| Area | Commands |
|---|---|
| Project | `file.newProject`, `file.open`, `file.save`, `file.saveAs`, `file.import` |
| Composition | `comp.new`, `comp.settings`, `comp.open`, `comp.workArea`, `comp.setPosterTime` |
| Layers | `layer.newSolid`, `layer.newText`, `layer.newShape`, `layer.newNull`, `layer.newAdjustment`, `layer.addItem` (image, video, precomp) |
| Transform & timing | `prop.set`, `prop.addKey`, `keys.set`, `layer.timing`, `layer.setParent` |
| Keyframe easing | `add_keyframe` with `inEase` / `outEase` (`speed`, `influence` percent), or `easyEase` / `easyEaseIn` / `easyEaseOut` / `bezier` / `hold` / `linear`. Motion blur: `comp.new` `shutterAngle`, `comp.setSwitch`, `layer.setSwitch`. |
| Effects | `effect.apply`, `effect.list`, `add_effect`, then `set_property` on `effects/#n/…` |
| Masks | `layer.addMask`, `mask.new`, `layer.setMask`, `mask.setVertex` |
| Blending | `layer.setBlendMode` (`Screen`, `Add`, `Multiply`, …), `layer.setTrackMatte` |
| Precomps | `layer.precompose` |
| Time | `time.set`, `time.nextFrame`, `time.start`, `time.end`, `time.workAreaStart` |
| Render | `renderQueue.add`, `renderQueue.render`, `render_frame`, `render_preview` |
| Query | `get_state`, `get_project`, `get_comp`, `get_layer`, `get_property`, `doc_inspect` |

Shapes accept `kind`: `rect`, `rounded`, `ellipse`, `star`, `polygon`. Text accepts font, style, size, fill, tracking, and per-character animators (`layer.addTextAnimator`). Export formats include H.264, HEVC, AV1, ProRes, WebM, PNG, JPEG, TIFF, EXR, GIF, WAV, and AIFF.

### A short session

```json
{"name":"execute_command","arguments":{"command":"comp.new","params":{"name":"Intro","width":1920,"height":1080,"frameRate":30,"duration":4,"background":"#F3F0E8"}}}
{"name":"execute_command","arguments":{"command":"layer.newText","params":{"text":"INDEX","name":"Title","font":"Inter","style":"Bold","size":200,"fill":"#141414","justify":"left","position":[96,540]}}}
{"name":"add_keyframe","arguments":{"layer":"Title","path":"transform/opacity","keys":[{"time":0,"value":0,"outEase":{"speed":0,"influence":40}},{"time":0.6,"value":100,"inEase":{"speed":0,"influence":80}}],"timeBase":"comp"}}
{"name":"render_frame","arguments":{"time":1.0,"max_side":960}}
{"name":"save_project","arguments":{"path":"intro.ecproj"}}
```

Times on `add_keyframe` and `set_property` are layer seconds unless `timeBase` is `"comp"`. `render_frame` and `get_property` take comp seconds.

## Index demo

1920×1080, 30 fps, 12 seconds. The builder (`examples/index/build_index.py`) starts `effectcraft-cli mcp --empty` and then:

1. Creates **Index** on an 8px grid: shutter 120°, eight motion-blur samples, paper background.
2. Sets Inter Bold headlines and Inter Medium labels. **STRUCTURE** rises per character (`layer.addTextAnimator`). **TYPE** hard-cuts on the same origin.
3. Wipes **WEIGHT / MEASURE / INTERVAL** with track mattes, and draws the rules with custom bezier scale.
4. Slides a flat black field in from the red mark's right edge. **MOTION** is paper type, matted to that field, so the edge is one match cut.
5. The field leaves and uncovers **EFFECT / CRAFT**. The mark travels on a straight path, holds still between moves, and lands with a short scale overshoot.
6. Saves the project, writes eight PNG frames, and renders H.264 through the queue with an 18 Mbps VBR target and motion blur on the moving layers. Flat fields encode well below that cap. The GIF in this README is a 1280-wide, 15 fps palette pass of that master (`dither=none`, so the fields stay clean).

`--frames` is the default (project and stills only). `--render` also writes the master and the GIF.

```sh
EFFECTCRAFT_CLI=target/release/effectcraft-cli python3 examples/index/build_index.py --render
```

## Tests

Protocol tests live in `crates/automation` (stdio, resources, confinement, and the loopback HTTP round trip). QA scenarios under `crates/automation/tests/qa/` build real projects through the same tools.

```sh
cargo test -p effectcraft-automation --lib
```

## По-русски

**EffectCraft MCP** — форк [storytold/effectcraft](https://github.com/storytold/effectcraft) (лицензия MIT или Apache-2.0). Это композитор в духе After Effects на чистом Rust: композиции, слои, ключевые кадры, эффекты, маски и очередь рендера. ИИ управляет им через MCP.

- Сервер: `effectcraft-cli mcp` (stdio) или `effectcraft-cli mcp --http 127.0.0.1:8765` (только loopback).
- Сборка: Rust 1.95+, `cargo build --release -p effectcraft-cli`.
- 36 инструментов: проект, слои (solid, text, shape, image, video, null, adjustment), трансформы, ключи с ease, эффекты, маски, режимы наложения, прекомпы, время, рендер, захват кадра и чтение состояния.
- Демо «Index»: `examples/index/index.ecproj`, `index.mp4`, `index.gif` (превью в начале README), кадры в `examples/index/frames/`. 1920×1080, 30 к/с, 12 с. Плоские бумага, тушь и один красный знак.

Авторские права на движок остаются у ArtCraft Team и участников EffectCraft. См. `LICENSE-APACHE`, `LICENSE-MIT`, `NOTICE`, `ATTRIBUTION.md`.

## License and credits

EffectCraft is licensed under the MIT License or the Apache License 2.0, at your option. This fork redistributes that work unchanged except for the MCP HTTP transport, per-key ease on `add_keyframe`, the Index demo, and these docs.

- Upstream: [github.com/storytold/effectcraft](https://github.com/storytold/effectcraft)
- Copyright: ArtCraft Team and the EffectCraft contributors (`NOTICE`)
- Third-party assets and fonts: `ATTRIBUTION.md`
- Trademarks in `docs/brand/` are not covered by the code license (`docs/brand/LICENSE-brand.txt`)

## Limitations

- The HTTP transport is loopback-only and serves one request at a time. Render progress and cancellation are implemented on stdio.
- Headless rendering uses the CPU unless the process is started with `--gpu`.
- `screenshot` and the `ui_*` tools need a desktop app started with `--control`.
- Index is a 12-second 1080p30 film. Motion blur is limited to the mark, the rules, the mattes, and the field; type stays sharp. A full-frame blur on every layer would be much slower on CPU.
- This fork imports a snapshot of upstream. It is not an automatically updated mirror.
