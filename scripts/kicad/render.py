#!/usr/bin/env python3
"""Render a KiCad project to PNGs you can look at.

    python3 scripts/kicad/render.py PROJECT_DIR_OR_FILE [--out DIR] [--only sch,top,bottom,3d,layers]

Writes into PROJECT/renders/ by default:
  sch.png            the schematic (every sheet, page 1 first)
  top.png            2D plot of the top side: F.Cu, F.Silkscreen, F.Mask openings, Edge.Cuts
  bottom.png         the same for the bottom side, mirrored as seen from below
  3d-top.png         KiCad's 3D raytraced view from above (needs 3D models for bodies)
  3d-iso.png         3D view from 45 degrees
  layers-<L>.png     one image per copper layer, for checking routing
Open every PNG you produce and look at it before reporting a layout as done.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import kienv


def rasterize(svg: Path, png: Path, width: int = 1800, background: str = "white") -> None:
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run([rsvg, "-w", str(width), "-b", background, str(svg), "-o", str(png)], check=True)
        return
    try:
        import cairosvg
        cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=width, background_color=background)
        return
    except ImportError:
        pass
    node = shutil.which("node")
    capture = kienv.SKILL_ROOT / "scripts" / "figure" / "capture.mjs"
    if node and kienv.chrome():
        subprocess.run([node, str(capture), str(svg), "--out", str(png), "--width", str(width)], check=True)
        return
    sys.exit("No SVG rasterizer: install librsvg (rsvg-convert) or `pip install cairosvg`, or run setup.sh.")


def find(project: Path, suffix: str) -> Path | None:
    if project.is_file():
        candidate = project.with_suffix(suffix)
        return candidate if candidate.exists() else None
    hits = sorted(project.glob(f"*{suffix}"))
    return hits[0] if hits else None


def board_layers(pcb: Path) -> list[str]:
    pcbnew = kienv.pcbnew()
    board = pcbnew.LoadBoard(str(pcb))
    return [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--out")
    ap.add_argument("--only", default="sch,top,bottom,3d,layers")
    ap.add_argument("--width", type=int, default=1800)
    args = ap.parse_args()
    project = Path(args.project).resolve()
    base = project if project.is_dir() else project.parent
    out = Path(args.out).resolve() if args.out else base / "renders"
    out.mkdir(parents=True, exist_ok=True)
    want = set(args.only.split(","))
    written = []
    sch, pcb = find(project, ".kicad_sch"), find(project, ".kicad_pcb")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if "sch" in want and sch:
            kienv.run_cli("sch", "export", "svg", "--exclude-drawing-sheet", "-o", tmp, sch)
            svgs = sorted(tmp.glob("*.svg"), key=lambda p: (p.stem != sch.stem, p.stem))
            for i, svg in enumerate(svgs):
                png = out / ("sch.png" if i == 0 else f"sch-{svg.stem}.png")
                rasterize(svg, png, args.width)
                written.append(png)
                svg.unlink()
        if pcb:
            sides = {"top": ("F.Cu,F.Silkscreen,F.Mask,Edge.Cuts", False),
                     "bottom": ("B.Cu,B.Silkscreen,B.Mask,Edge.Cuts", True)}
            for side, (layers, mirror) in sides.items():
                if side not in want:
                    continue
                svg = tmp / f"{side}.svg"
                cmd = ["pcb", "export", "svg", "--layers", layers, "--mode-single", "--fit-page-to-board",
                       "--exclude-drawing-sheet", "-o", svg, pcb]
                if mirror:
                    cmd.insert(3, "--mirror")
                kienv.run_cli(*cmd)
                rasterize(svg, out / f"{side}.png", args.width)
                written.append(out / f"{side}.png")
            if "layers" in want:
                for layer in board_layers(pcb):
                    svg = tmp / "layer.svg"
                    kienv.run_cli("pcb", "export", "svg", "--layers", f"{layer},Edge.Cuts", "--mode-single",
                                  "--fit-page-to-board", "--exclude-drawing-sheet", "-o", svg, pcb)
                    png = out / f"layers-{layer.replace('.', '_')}.png"
                    rasterize(svg, png, args.width)
                    written.append(png)
            if "3d" in want:
                for name, extra in (("3d-top", ["--side", "top"]), ("3d-iso", ["--side", "top", "--rotate", "-45,0,45"])):
                    png = out / f"{name}.png"
                    proc = kienv.run_cli("pcb", "render", *extra, "--quality", "basic", "--width", "1600", "--height", "1200",
                                         "--background", "opaque", "-o", png, pcb, check=False)
                    if png.exists():
                        written.append(png)
                    else:
                        print(f"3D render skipped ({proc.stderr.strip()[:120] or 'no output'})")
    for path in written:
        print("wrote", path)


if __name__ == "__main__":
    main()
