#!/usr/bin/env python3
"""Build the Aurora motion-graphics demo by talking to EffectCraft's MCP server.

Every edit goes through JSON-RPC tools (`execute_command`, `add_keyframe`,
`add_effect`, `set_property`, `render_frame`, `save_project`, …) on a live
`effectcraft-cli mcp` process. The script does not call the engine directly.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"
FRAMES = ROOT / "frames"
PROJECT = ROOT / "aurora.ecproj"
MP4 = ROOT / "aurora.mp4"
GIF = ROOT / "aurora.gif"
CLI = Path(os.environ.get("EFFECTCRAFT_CLI", "target/debug/effectcraft-cli"))

W, H = 960, 540
FPS = 24
DURATION = 4.0


def write_png(path: Path, w: int, h: int, rgba: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgba[y * w * 4 : (y + 1) * w * 4] for y in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def make_spark(path: Path) -> None:
    n = 256
    buf = bytearray(n * n * 4)
    cx = cy = (n - 1) / 2
    for y in range(n):
        for x in range(n):
            dx = (x - cx) / cx
            dy = (y - cy) / cy
            r = (dx * dx + dy * dy) ** 0.5
            # Soft disc with a brighter core, fully transparent at the edge.
            alpha = max(0.0, 1.0 - r) ** 1.6
            core = max(0.0, 1.0 - r * 3.2)
            i = (y * n + x) * 4
            buf[i] = min(255, int(255 * alpha + 40 * core))
            buf[i + 1] = min(255, int(236 * alpha + 80 * core))
            buf[i + 2] = min(255, int(210 * alpha))
            buf[i + 3] = min(255, int(255 * alpha))
    write_png(path, n, n, bytes(buf))


def make_drift(path: Path) -> None:
    """A short looping colour field used as a video layer."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x140818:s=320x180:d=4:r=24",
        "-vf",
        "geq=r='70+50*sin(2*PI*T+X/50)':g='24+40*sin(2*PI*T*0.7+Y/36)':b='110+50*cos(2*PI*(T+X/90))'",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(path),
    ]
    subprocess.run(cmd, check=True)


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
                "clientInfo": {"name": "aurora-demo", "version": "1"},
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
            raise SystemExit(f"{name} {json.dumps(args)[:500]} failed:\n{text}")
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
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run(mcp: Mcp, command: str, params: dict | None = None) -> dict:
    return mcp.tool("execute_command", {"command": command, "params": params or {}})


def keys(mcp: Mcp, layer: str, path: str, pairs: list[tuple[float, object]], interpolation: str = "easyEase") -> None:
    mcp.tool(
        "add_keyframe",
        {
            "layer": layer,
            "path": path,
            "keys": [{"time": t, "value": v} for t, v in pairs],
            "interpolation": interpolation,
        },
    )


def build(mcp: Mcp) -> None:
    state = mcp.tool("get_state")
    print(f"engine commands: {state['app'].get('commands')}  effects: {state['app'].get('effects')}")
    listed = mcp.tool("command_list", {"filter": "layer.new"})
    ids = [c.get("id") for c in listed] if isinstance(listed, list) else []
    print("layer factories:", ", ".join(str(i) for i in ids))
    glow = mcp.tool("list_effects", {"filter": "glow"})
    print("glow effects:", [e.get("name") or e.get("id") for e in glow] if isinstance(glow, list) else glow)
    fonts = mcp.tool("list_fonts", {"query": "Inter"})
    print("fonts matching Inter:", len(fonts) if isinstance(fonts, list) else fonts)

    run(
        mcp,
        "comp.new",
        {
            "name": "Aurora",
            "width": W,
            "height": H,
            "frameRate": FPS,
            "duration": DURATION,
            "background": "#07060f",
        },
    )
    mcp.tool("doc_inspect")

    # Full-frame grade: a solid plus a four-colour gradient.
    run(mcp, "layer.newSolid", {"name": "Sky", "color": "#07060f", "width": W, "height": H})
    mcp.tool(
        "add_effect",
        {
            "layer": "Sky",
            "effect": "4-Color Gradient",
            "values": {
                "positionsColors/point1": [0.08, 0.12],
                "positionsColors/color1": [0.05, 0.06, 0.18],
                "positionsColors/point2": [0.92, 0.08],
                "positionsColors/color2": [0.28, 0.05, 0.24],
                "positionsColors/point3": [0.12, 0.92],
                "positionsColors/color3": [0.02, 0.16, 0.22],
                "positionsColors/point4": [0.84, 0.86],
                "positionsColors/color4": [0.07, 0.04, 0.12],
                "blend": 100,
            },
        },
    )

    spark_item = run(mcp, "file.import", {"paths": [str(MEDIA / "spark.png")], "sequence": False})["items"][0]
    drift_item = run(mcp, "file.import", {"paths": [str(MEDIA / "drift.mp4")], "sequence": False})["items"][0]
    drift_layer = run(mcp, "layer.addItem", {"item": drift_item, "duration": DURATION})["layer"]
    run(mcp, "layer.rename", {"layer": drift_layer, "name": "Drift"})
    run(mcp, "layer.setBlendMode", {"layers": ["Drift"], "mode": "Screen"})
    mcp.tool("set_property", {"layer": "Drift", "path": "transform/opacity", "value": 48})
    keys(mcp, "Drift", "transform/scale", [(0.0, [340, 340]), (4.0, [390, 390])])
    keys(mcp, "Drift", "transform/position", [(0.0, [420, 260]), (4.0, [540, 300])])
    run(mcp, "layer.addMask", {"layer": "Drift", "shape": "ellipse"})
    tree = mcp.tool("get_layer", {"layer": "Drift", "flat": True})
    feather = next((p["path"] for p in tree["properties"] if str(p["path"]).endswith("/feather")), None)
    if feather:
        mcp.tool("set_property", {"layer": "Drift", "path": feather, "value": [90, 90]})

    spark_layer = run(mcp, "layer.addItem", {"item": spark_item, "duration": DURATION})["layer"]
    run(mcp, "layer.rename", {"layer": spark_layer, "name": "Spark"})
    run(mcp, "layer.setBlendMode", {"layers": ["Spark"], "mode": "Add"})
    keys(mcp, "Spark", "transform/position", [(0.2, [250, 170]), (2.2, [700, 150]), (3.8, [760, 210])])
    keys(mcp, "Spark", "transform/scale", [(0.2, [18, 18]), (1.4, [70, 70]), (3.6, [28, 28])])
    keys(mcp, "Spark", "transform/opacity", [(0.2, 0), (1.0, 100), (3.2, 80), (3.9, 0)])

    run(mcp, "layer.newNull", {"name": "Drift Rig"})
    orbs = [
        ("Orb Magenta", "#ff4d8d", [340, 340], [250, 190], [330, 230]),
        ("Orb Cyan", "#3ee0ff", [300, 300], [720, 390], [680, 340]),
        ("Orb Violet", "#8b6bff", [220, 220], [500, 300], [540, 250]),
    ]
    for name, fill, size, start, end in orbs:
        run(
            mcp,
            "layer.newShape",
            {"kind": "ellipse", "name": name, "size": size, "fill": fill, "position": start},
        )
        run(mcp, "layer.setBlendMode", {"layers": [name], "mode": "Screen"})
        mcp.tool("add_effect", {"layer": name, "effect": "Gaussian Blur", "values": {"blurriness": 36}})
        run(mcp, "layer.setParent", {"layers": [name], "parent": "Drift Rig", "compensate": True})
        here = mcp.tool("get_property", {"layer": name, "path": "transform/position", "time": 0})["value"]
        end_pos = [here[0] + (end[0] - start[0]), here[1] + (end[1] - start[1])]
        if isinstance(here, list) and len(here) > 2:
            end_pos.append(here[2])
        keys(mcp, name, "transform/position", [(0.0, here), (4.0, end_pos)])
        keys(mcp, name, "transform/scale", [(0.0, [20, 20]), (1.5, [115, 115]), (4.0, [100, 100])])
        keys(mcp, name, "transform/opacity", [(0.0, 0), (0.8, 100), (4.0, 85)])
    keys(mcp, "Drift Rig", "transform/position", [(0.0, [480, 270]), (4.0, [500, 250])])

    pre = run(
        mcp,
        "layer.precompose",
        {"layers": ["Drift Rig", "Orb Magenta", "Orb Cyan", "Orb Violet"], "name": "Aurora Field", "mode": "move"},
    )
    print("precomp:", pre)
    field = pre.get("layer", "Aurora Field")
    run(mcp, "layer.setBlendMode", {"layers": [field], "mode": "Screen"})
    keys(mcp, field, "transform/scale", [(0.0, [92, 92]), (2.0, [104, 104]), (4.0, [100, 100])])

    run(
        mcp,
        "layer.newShape",
        {
            "kind": "ellipse",
            "name": "Ring",
            "size": [460, 460],
            "fill": "#000000",
            "fillOpacity": 0,
            "stroke": "#e7fbff",
            "strokeWidth": 2,
            "position": [480, 268],
        },
    )
    keys(mcp, "Ring", "transform/rotation", [(0.0, -18), (4.0, 22)])
    keys(mcp, "Ring", "transform/scale", [(0.0, [70, 70]), (1.6, [100, 100]), (4.0, [108, 108])])
    keys(mcp, "Ring", "transform/opacity", [(0.0, 0), (1.2, 80), (4.0, 40)])

    run(
        mcp,
        "layer.newShape",
        {"kind": "star", "name": "Star", "size": [36, 36], "fill": "#fff4cf", "position": [612, 188]},
    )
    run(mcp, "layer.setBlendMode", {"layers": ["Star"], "mode": "Add"})
    keys(mcp, "Star", "transform/rotation", [(0.4, 0), (3.6, 180)])
    keys(mcp, "Star", "transform/scale", [(0.4, [0, 0]), (1.3, [140, 140]), (3.4, [80, 80])])
    keys(mcp, "Star", "transform/opacity", [(0.4, 0), (1.2, 100), (3.5, 0)])

    run(
        mcp,
        "layer.newShape",
        {"kind": "rounded", "name": "Rule", "size": [220, 3], "fill": "#f4f7ff", "position": [480, 348]},
    )
    keys(mcp, "Rule", "transform/scale", [(1.15, [0, 100]), (2.1, [100, 100])])
    keys(mcp, "Rule", "transform/opacity", [(1.15, 0), (2.0, 90), (3.8, 50)])

    run(
        mcp,
        "layer.newText",
        {
            "text": "AURORA",
            "name": "Title",
            "font": "Inter",
            "style": "Bold",
            "size": 118,
            "fill": "#f7f4ff",
            "tracking": 40,
            "position": [480, 250],
        },
    )
    run(mcp, "layer.addTextAnimator", {"layer": "Title", "properties": ["position", "opacity"]})
    mcp.tool("set_property", {"layer": "Title", "path": "text/animators/#1/properties/position", "value": [0, 70, 0]})
    mcp.tool("set_property", {"layer": "Title", "path": "text/animators/#1/properties/opacity", "value": 0})
    keys(
        mcp,
        "Title",
        "text/animators/#1/selectors/#1/offset",
        [(0.35, -15), (1.7, 100)],
    )
    keys(mcp, "Title", "transform/position", [(0.35, [480, 290]), (1.7, [480, 248])])
    mcp.tool(
        "add_effect",
        {
            "layer": "Title",
            "effect": "Glow",
            "values": {
                "threshold": 40,
                "radius": 22,
                "intensity": 1.4,
                "colors": 1,
                "colorA": [1.0, 0.96, 1.0],
                "colorB": [1.0, 0.42, 0.72],
            },
        },
    )

    run(
        mcp,
        "layer.newText",
        {
            "text": "MOTION, COMPOSED",
            "name": "Subtitle",
            "font": "Inter",
            "style": "Medium",
            "size": 22,
            "fill": "#d5e7ff",
            "tracking": 280,
            "position": [480, 348],
        },
    )
    keys(mcp, "Subtitle", "transform/opacity", [(1.35, 0), (2.15, 100), (3.7, 70)])
    keys(mcp, "Subtitle", "transform/position", [(1.35, [480, 372]), (2.2, [480, 348])])

    run(mcp, "layer.newAdjustment", {"name": "Grade"})
    mcp.tool(
        "add_effect",
        {"layer": "Grade", "effect": "Glow", "values": {"threshold": 72, "radius": 18, "intensity": 0.45}},
    )

    run(mcp, "time.set", {"time": 2.2})
    run(mcp, "comp.setPosterTime", {})
    comp = mcp.tool("get_comp")
    print(f"layers ({len(comp['layers'])}):", [layer["name"] for layer in comp["layers"]])
    saved = mcp.tool("save_project", {"path": str(PROJECT)})
    print("saved", saved)
    # The engine stores the import path it was given. Rewrite footage paths so the
    # project opens from the repository root on any machine.
    text = PROJECT.read_text()
    for name in ("spark.png", "drift.mp4"):
        absolute = str((MEDIA / name).resolve())
        text = text.replace(absolute, f"examples/aurora/media/{name}")
    PROJECT.write_text(text)

    FRAMES.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate((0.4, 1.35, 2.2, 3.5), start=1):
        info = mcp.tool(
            "render_frame",
            {"time": t, "max_side": 0, "inline": False, "path": str(FRAMES / f"aurora-{i:02d}.png")},
        )
        print(f"frame {t}s -> {info}")
    preview = mcp.tool("render_preview", {"time": 2.2, "max_side": 480})
    print("preview", {k: preview[k] for k in preview if k != "content"} if isinstance(preview, dict) else type(preview).__name__)

    run(mcp, "renderQueue.add", {"comp": "Aurora", "format": "h264", "output": str(MP4), "audio": "off"})
    run(mcp, "renderQueue.add", {"comp": "Aurora", "format": "gif", "output": str(GIF), "loop": True})
    done = run(mcp, "renderQueue.render", {"wait": True})
    print("render", json.dumps(done)[:2000])
    final = mcp.tool("get_project")
    print("project items:", len(final.get("items") or final.get("project", {}).get("items") or []))


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    make_spark(MEDIA / "spark.png")
    make_drift(MEDIA / "drift.mp4")
    binary = CLI if CLI.is_absolute() else Path.cwd() / CLI
    mcp = Mcp(binary)
    try:
        build(mcp)
    finally:
        print(f"tool calls: {len(mcp.calls)}  unique: {len(set(mcp.calls))}")
        mcp.close()
    if not MP4.is_file() or MP4.stat().st_size < 1000:
        raise SystemExit(f"mp4 missing or tiny: {MP4}")
    if not GIF.is_file() or GIF.stat().st_size < 1000:
        raise SystemExit(f"gif missing or tiny: {GIF}")
    print("OK", MP4, GIF)


if __name__ == "__main__":
    main()
