#!/usr/bin/env python3
"""Build the Index film by talking to EffectCraft's MCP server.

Swiss editorial kinetic type: flat paper, ink, and one red mark. No gradients,
glows, or blurs. Every edit is a JSON-RPC tool call on `effectcraft-cli mcp`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"
PROJECT = ROOT / "index.ecproj"
MP4 = ROOT / "index.mp4"
GIF = ROOT / "index.gif"
CLI = Path(os.environ.get("EFFECTCRAFT_CLI", REPO / "target/debug/effectcraft-cli"))

W, H = 1920, 1080
FPS = 30
DURATION = 12.0

PAPER = "#F3F0E8"
INK = "#141414"
RED = "#E10600"

# 8px grid.
MARGIN = 96
RULE_W = 1728  # 96 → 1824
RULE_H = 4
MARK = 64
HERO = 272
HERO_Y = 548
LOCK_GAP = 252
LINE = 48
LINE_Y = (708, 780, 852)
# Cap-centre of the hero, where the mark rests on the black field.
MARK_DROP_Y = 448
RULE_TOP = 72
RULE_BOTTOM = 948
CAPTION_Y = 1012
MARK_Y = 128  # center; top of the square sits at 96


def ease(influence: float, speed: float = 0.0) -> dict:
    return {"speed": speed, "influence": influence}


class Mcp:
    def __init__(self, binary: Path) -> None:
        if not binary.is_file():
            raise SystemExit(f"missing CLI binary: {binary}\nbuild it with: cargo build -p effectcraft-cli")
        self.proc = subprocess.Popen(
            [str(binary), "mcp", "--empty"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.seq = 0
        self.calls: list[str] = []
        hello = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "index-demo", "version": "1"},
            },
        )
        if hello.get("error"):
            raise SystemExit(f"initialize failed: {hello}")
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(note) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        msg = {"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}}
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = ""
            if self.proc.stderr is not None:
                err = self.proc.stderr.read()
            raise SystemExit(f"MCP server closed stdout during {method}. stderr:\n{err}")
        return json.loads(line)

    def tool(self, name: str, args: dict | None = None) -> dict:
        self.calls.append(name)
        reply = self.request("tools/call", {"name": name, "arguments": args or {}})
        if reply.get("error"):
            raise SystemExit(f"{name} {args} RPC error:\n{json.dumps(reply['error'], indent=2)}")
        result = reply["result"]
        content = result.get("content") or []
        text = ""
        for block in content:
            if block.get("type") == "text":
                text = block.get("text") or ""
                break
        if result.get("isError"):
            raise SystemExit(f"{name} {json.dumps(args)[:800]} failed:\n{text}")
        if not text:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run(mcp: Mcp, command: str, params: dict | None = None) -> dict:
    return mcp.tool("execute_command", {"command": command, "params": params or {}})


def keys(mcp: Mcp, layer: str, path: str, frames: list[dict]) -> None:
    mcp.tool("add_keyframe", {"layer": layer, "path": path, "timeBase": "comp", "keys": frames})


def span(mcp: Mcp, layers: list[str], inn: float, out: float) -> None:
    run(mcp, "layer.timing", {"layers": layers, "in": inn, "out": out})


def blur(mcp: Mcp, layers: list[str]) -> None:
    run(mcp, "layer.setSwitch", {"layers": layers, "switch": "motionBlur", "value": True})


def position_keys(mcp: Mcp, layer: str, frames: list[dict], hold_at: list[float]) -> None:
    """Position keys on a straight path. `hold_at` times keep their value until the next key.

    Two spatial keys with the same value and auto-bezier handles bow off the line, so a
    still span is a hold and every key on this property is spatially linear.
    """
    keys(mcp, layer, "transform/position", frames)
    times = [f["time"] for f in frames]
    run(
        mcp,
        "keys.select",
        {"timeBase": "comp", "keys": [{"layer": layer, "path": "transform/position", "time": t} for t in times]},
    )
    run(mcp, "keys.interpolation", {"spatial": "linear"})
    if hold_at:
        run(
            mcp,
            "keys.select",
            {"timeBase": "comp", "keys": [{"layer": layer, "path": "transform/position", "time": t} for t in hold_at]},
        )
        run(mcp, "keys.interpolation", {"out": "hold"})


def solid(mcp: Mcp, name: str, color: str, width: int, height: int) -> None:
    run(mcp, "layer.newSolid", {"name": name, "color": color, "width": width, "height": height})


def bar(
    mcp: Mcp,
    name: str,
    color: str,
    x: float,
    y: float,
    width: int,
    height: int,
    t0: float,
    t1: float,
    out_inf: float = 28,
    in_inf: float = 90,
) -> None:
    """A hairline that wipes from its left edge. `y` is the vertical center."""
    solid(mcp, name, color, width, height)
    mcp.tool("set_property", {"layer": name, "path": "transform/anchor", "value": [0, height / 2, 0]})
    mcp.tool("set_property", {"layer": name, "path": "transform/position", "value": [x, y, 0]})
    keys(
        mcp,
        name,
        "transform/scale",
        [
            {"time": t0, "value": [0, 100], "outEase": ease(out_inf)},
            {"time": t1, "value": [100, 100], "inEase": ease(in_inf)},
        ],
    )
    blur(mcp, [name])


def text(
    mcp: Mcp,
    name: str,
    body: str,
    *,
    size: float,
    fill: str,
    x: float,
    y: float,
    style: str = "Bold",
    tracking: float = 0,
    justify: str = "left",
) -> None:
    run(
        mcp,
        "layer.newText",
        {
            "name": name,
            "text": body,
            "font": "Inter",
            "style": style,
            "size": size,
            "fill": fill,
            "tracking": tracking,
            "justify": justify,
            "position": [x, y],
            "applyStroke": False,
        },
    )


def stagger(mcp: Mcp, name: str, t0: float, t1: float, rise: float = 48) -> None:
    """Characters rise into place, left to right, on a short ramp."""
    run(mcp, "layer.select", {"layers": [name]})
    run(mcp, "layer.addTextAnimator", {"layer": name, "properties": ["opacity", "position"], "name": "Rise"})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/properties/opacity", "value": 0})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/properties/position", "value": [0, rise, 0]})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/selectors/#1/end", "value": 16})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/selectors/#1/advanced/shape", "value": 1})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/selectors/#1/advanced/easeHigh", "value": 55})
    mcp.tool("set_property", {"layer": name, "path": "text/animators/#1/selectors/#1/advanced/easeLow", "value": 20})
    keys(
        mcp,
        name,
        "text/animators/#1/selectors/#1/offset",
        [
            {"time": t0, "value": -16, "outEase": ease(42)},
            {"time": t1, "value": 100, "inEase": ease(82)},
        ],
    )


def matte_wipe(mcp: Mcp, layer: str, matte: str, x: float, y: float, width: int, height: int, t0: float, t1: float) -> None:
    """Reveal `layer` with a solid track matte that scales from its left edge."""
    solid(mcp, matte, INK, width, height)
    mcp.tool("set_property", {"layer": matte, "path": "transform/anchor", "value": [0, height / 2, 0]})
    mcp.tool("set_property", {"layer": matte, "path": "transform/position", "value": [x, y, 0]})
    keys(
        mcp,
        matte,
        "transform/scale",
        [
            {"time": t0, "value": [0, 100], "outEase": ease(32)},
            {"time": t1, "value": [100, 100], "inEase": ease(88)},
        ],
    )
    blur(mcp, [matte])
    run(mcp, "layer.setTrackMatte", {"layer": layer, "matte": matte, "kind": "alpha"})


def build(mcp: Mcp) -> None:
    state = mcp.tool("get_state")
    print(f"engine commands: {state['app'].get('commands')}  effects: {state['app'].get('effects')}")
    fonts = mcp.tool("list_fonts", {"query": "Inter"})
    print("fonts:", fonts.get("families") if isinstance(fonts, dict) else fonts)

    run(
        mcp,
        "comp.new",
        {
            "name": "Index",
            "width": W,
            "height": H,
            "frameRate": FPS,
            "duration": DURATION,
            "background": PAPER,
            "shutterAngle": 120,
            "shutterPhase": -60,
            "motionBlurSamples": 8,
        },
    )
    run(mcp, "comp.setSwitch", {"switch": "motionBlur", "value": True})

    # --- Scene 1–2, ink on paper (covered once the black field arrives) ---
    bar(mcp, "Rule Top", INK, MARGIN, RULE_TOP, RULE_W, RULE_H, 0.04, 0.52)
    bar(mcp, "Rule Bottom", INK, MARGIN, RULE_BOTTOM, RULE_W, RULE_H, 1.62, 2.18)

    text(mcp, "Index 01", "01", size=26, fill=INK, x=176, y=MARK_Y + 8, style="Medium", tracking=60)
    text(mcp, "Index 02", "02", size=26, fill=INK, x=176, y=MARK_Y + 8, style="Medium", tracking=60)
    text(mcp, "Meta", "1920  ×  1080", size=18, fill=INK, x=1824, y=MARK_Y + 6, style="Medium", tracking=180, justify="right")

    text(mcp, "Structure", "STRUCTURE", size=HERO, fill=INK, x=MARGIN, y=HERO_Y, tracking=-28)
    stagger(mcp, "Structure", 0.16, 1.42, rise=72)
    text(mcp, "Type", "TYPE", size=HERO, fill=INK, x=MARGIN, y=HERO_Y, tracking=-28)

    lines = ("WEIGHT", "MEASURE", "INTERVAL")
    for i, (word, y) in enumerate(zip(lines, LINE_Y), start=1):
        name = f"Line {i}"
        text(mcp, name, word, size=LINE, fill=INK, x=MARGIN, y=y, style="Medium", tracking=80)
        matte_wipe(mcp, name, f"Line Matte {i}", MARGIN, y - 16, 1400, 72, 3.08 + (i - 1) * 0.16, 3.62 + (i - 1) * 0.16)

    text(mcp, "Caption", "KINETIC TYPE", size=20, fill=INK, x=MARGIN, y=CAPTION_Y, style="Medium", tracking=360)
    matte_wipe(mcp, "Caption", "Caption Matte", MARGIN, CAPTION_Y - 8, 900, 40, 1.92, 2.46)

    span(mcp, ["Index 01", "Structure"], 0.0, 3.0)
    span(mcp, ["Index 02", "Type"], 3.0, 6.75)
    span(mcp, ["Line 1", "Line 2", "Line 3", "Line Matte 1", "Line Matte 2", "Line Matte 3"], 3.0, 6.75)
    span(mcp, ["Rule Top", "Rule Bottom", "Meta", "Caption", "Caption Matte"], 0.0, 6.75)

    # --- Scene 4 lockup, waiting under the field ---
    text(mcp, "Index 04", "04", size=26, fill=INK, x=176, y=MARK_Y + 8, style="Medium", tracking=60)
    text(mcp, "Effect", "EFFECT", size=HERO, fill=INK, x=MARGIN - 6, y=HERO_Y, tracking=-28)
    text(mcp, "Craft", "CRAFT", size=HERO, fill=INK, x=MARGIN, y=HERO_Y + LOCK_GAP, tracking=-28)
    text(mcp, "Footer", "EFFECTCRAFT", size=20, fill=INK, x=MARGIN, y=CAPTION_Y, style="Medium", tracking=360)
    text(mcp, "Footer Right", "30 FPS", size=20, fill=INK, x=1824, y=CAPTION_Y, style="Medium", tracking=280, justify="right")
    # Static rules: the exiting field uncovers them. A second wipe would fight that edge.
    for name, y in (("Rule Lock Top", RULE_TOP), ("Rule Lock Bottom", RULE_BOTTOM)):
        solid(mcp, name, INK, RULE_W, RULE_H)
        mcp.tool("set_property", {"layer": name, "path": "transform/anchor", "value": [0, RULE_H / 2, 0]})
        mcp.tool("set_property", {"layer": name, "path": "transform/position", "value": [MARGIN, y, 0]})
    span(
        mcp,
        ["Index 04", "Effect", "Craft", "Footer", "Footer Right", "Rule Lock Top", "Rule Lock Bottom"],
        9.0,
        DURATION,
    )

    # The field and a hidden twin. Paper type is track-matted to the twin, so it exists only
    # where the field is — the wipe edge is one line, and the lockup underneath is not covered
    # once that edge has passed.
    field_keys = [
        {"time": 6.0, "value": [1824, H / 2, 0], "outEase": ease(24)},
        {"time": 6.58, "value": [0, H / 2, 0], "inEase": ease(90)},
        {"time": 9.0, "value": [0, H / 2, 0], "outEase": ease(24)},
        {"time": 9.58, "value": [W, H / 2, 0], "inEase": ease(90)},
    ]
    for name in ("Field", "Field Matte"):
        solid(mcp, name, INK, W, H)
        mcp.tool("set_property", {"layer": name, "path": "transform/anchor", "value": [0, H / 2, 0]})
        mcp.tool("set_property", {"layer": name, "path": "transform/position", "value": [W, H / 2, 0]})
        position_keys(mcp, name, field_keys, hold_at=[6.58])
        blur(mcp, [name])
    span(mcp, ["Field", "Field Matte"], 6.0, 9.7)

    text(mcp, "Index 03", "03", size=26, fill=PAPER, x=176, y=MARK_Y + 8, style="Medium", tracking=60)
    text(mcp, "Meta Paper", "1920  ×  1080", size=18, fill=PAPER, x=1824, y=MARK_Y + 6, style="Medium", tracking=180, justify="right")
    # M and E sit a few pixels to the right of S; nudge so the ink edges match on the cut.
    text(mcp, "Motion", "MOTION", size=HERO, fill=PAPER, x=MARGIN - 6, y=HERO_Y, tracking=-28)
    text(mcp, "Caption Paper", "KINETIC TYPE", size=20, fill=PAPER, x=MARGIN, y=CAPTION_Y, style="Medium", tracking=360)
    solid(mcp, "Rule Top Paper", PAPER, RULE_W, RULE_H)
    mcp.tool("set_property", {"layer": "Rule Top Paper", "path": "transform/anchor", "value": [0, RULE_H / 2, 0]})
    mcp.tool("set_property", {"layer": "Rule Top Paper", "path": "transform/position", "value": [MARGIN, RULE_TOP, 0]})
    solid(mcp, "Rule Bottom Paper", PAPER, RULE_W, RULE_H)
    mcp.tool("set_property", {"layer": "Rule Bottom Paper", "path": "transform/anchor", "value": [0, RULE_H / 2, 0]})
    mcp.tool("set_property", {"layer": "Rule Bottom Paper", "path": "transform/position", "value": [MARGIN, RULE_BOTTOM, 0]})
    paper = ["Index 03", "Meta Paper", "Motion", "Caption Paper", "Rule Top Paper", "Rule Bottom Paper"]
    for layer in paper:
        run(mcp, "layer.setTrackMatte", {"layer": layer, "matte": "Field Matte", "kind": "alpha"})
    span(mcp, paper, 6.0, 9.7)

    # --- The mark. On top of every field. Right edge at x=1824 when the wipe starts. ---
    solid(mcp, "Mark", RED, MARK, MARK)
    mcp.tool("set_property", {"layer": "Mark", "path": "transform/position", "value": [128, MARK_Y, 0]})
    keys(
        mcp,
        "Mark",
        "transform/scale",
        [
            {"time": 0.08, "value": [0, 0, 100], "outEase": ease(22)},
            {"time": 0.40, "value": [114, 114, 100], "inEase": ease(58), "outEase": ease(36)},
            {"time": 0.68, "value": [100, 100, 100], "inEase": ease(78)},
        ],
    )
    # Rest x=1792 puts the mark's right edge on the rule end (1824), which is where the field enters.
    position_keys(
        mcp,
        "Mark",
        [
            {"time": 3.0, "value": [128, MARK_Y, 0], "outEase": ease(36)},
            {"time": 3.92, "value": [1832, MARK_Y, 0], "inEase": ease(62), "outEase": ease(40)},
            {"time": 4.28, "value": [1792, MARK_Y, 0], "inEase": ease(80)},
            {"time": 7.05, "value": [1792, MARK_Y, 0], "outEase": ease(34)},
            {"time": 7.62, "value": [1792, MARK_DROP_Y, 0], "inEase": ease(78)},
            {"time": 9.55, "value": [1792, MARK_DROP_Y, 0], "outEase": ease(30)},
            {"time": 10.12, "value": [1024, HERO_Y + LOCK_GAP - 20, 0], "inEase": ease(55), "outEase": ease(34)},
            {"time": 10.46, "value": [985, HERO_Y + LOCK_GAP - 32, 0], "inEase": ease(82)},
        ],
        hold_at=[4.28, 7.62],
    )
    keys(
        mcp,
        "Mark",
        "transform/scale",
        [
            {"time": 10.02, "value": [100, 100, 100], "outEase": ease(30)},
            {"time": 10.28, "value": [112, 112, 100], "inEase": ease(50), "outEase": ease(34)},
            {"time": 10.54, "value": [100, 100, 100], "inEase": ease(80)},
        ],
    )
    blur(mcp, ["Mark"])

    run(mcp, "time.set", {"time": 11.8})
    run(mcp, "comp.setPosterTime", {})
    comp = mcp.tool("get_comp")
    print(f"layers ({len(comp['layers'])}):", [layer["name"] for layer in comp["layers"]])
    saved = mcp.tool("save_project", {"path": str(PROJECT)})
    print("saved", saved.get("path") or saved)


def render_frames(mcp: Mcp, times: list[tuple[str, float]]) -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for name, t in times:
        info = mcp.tool(
            "render_frame",
            {"time": t, "max_side": 0, "inline": False, "path": str(FRAMES / f"{name}.png")},
        )
        print(f"frame {t:.2f}s -> {info.get('path')} {info.get('width')}x{info.get('height')}")


def render_film(mcp: Mcp) -> None:
    run(
        mcp,
        "renderQueue.add",
        {
            "comp": "Index",
            "format": "h264",
            "output": str(MP4),
            "audio": "off",
            "bitrate": 18000,
            "motionBlur": "onForChecked",
            "rateControl": "bitrate",
        },
    )
    done = run(mcp, "renderQueue.render", {"wait": True})
    print("render", json.dumps(done)[:2000])


def gif_from_mp4() -> None:
    """Flat fields: a generated palette and no dither, so the preview stays clean."""
    palette = ROOT / "frames" / "palette.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(MP4),
            "-vf",
            "fps=15,scale=1280:-1:flags=lanczos,palettegen=stats_mode=diff:max_colors=32",
            str(palette),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(MP4),
            "-i",
            str(palette),
            "-lavfi",
            "fps=15,scale=1280:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=none",
            "-loop",
            "0",
            str(GIF),
        ],
        check=True,
    )
    palette.unlink(missing_ok=True)


PROOF = [
    ("01-open", 0.45),
    ("02-structure", 2.70),
    ("03-cut", 3.04),
    ("04-lines", 4.70),
    ("05-wipe", 6.32),
    ("06-motion", 8.20),
    ("07-reveal", 9.36),
    ("08-lockup", 11.80),
]


def main() -> None:
    render = "--render" in sys.argv
    binary = CLI if CLI.is_absolute() else Path.cwd() / CLI
    mcp = Mcp(binary)
    try:
        build(mcp)
        render_frames(mcp, PROOF)
        if render:
            render_film(mcp)
    finally:
        print(f"tool calls: {len(mcp.calls)}  unique: {len(set(mcp.calls))}")
        mcp.close()
    if render:
        if not MP4.is_file() or MP4.stat().st_size < 100_000:
            raise SystemExit(f"mp4 missing or tiny: {MP4}")
        gif_from_mp4()
        if not GIF.is_file() or GIF.stat().st_size < 10_000:
            raise SystemExit(f"gif missing or tiny: {GIF}")
        print("OK", MP4, GIF)


if __name__ == "__main__":
    main()
