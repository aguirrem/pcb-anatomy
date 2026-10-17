#!/usr/bin/env python3
"""Dump a KiCad board to JSON for the isometric figure kit (kit/pcb.mjs).

    python3 scripts/kicad/board_dump.py board.kicad_pcb [-o board.json] [--zones] [--side top|bottom]

Coordinates are millimetres from the board's top-left corner, x right and
y down exactly as KiCad shows them; the kit's floor has the same
handedness, so the figure is never mirrored. Each footprint carries its body
outline (from the fab layer, else the courtyard, else the pads), its pads,
a part kind (chip, gullwing, nolead, header, connector, switch, led, hole,
crystal, electrolytic, inductor, generic) and a body height in mm guessed
from the package name. Correct a height or kind in the JSON, or pass
overrides to boardFigure(), when the guess is wrong.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import kienv

pcbnew = kienv.pcbnew()

HEIGHTS = [  # (regex on the footprint name, kind, body height in mm or None to read it from the name)
    (r"^MountingHole|^Plain_Hole|^Hole_|^NPTH", "hole", 0),
    (r"^Fiducial", "fiducial", 0),
    (r"^TestPoint", "testpoint", 0.1),
    (r"PCB_traces|PCB_Edge|CardEdge|Edge_Connector|_Edge$", "edge", 0),
    (r"USB_C_Receptacle", "connector", 3.2),
    (r"USB_Micro", "connector", 2.6),
    (r"USB_Mini", "connector", 4.0),
    (r"USB_A", "connector", 7.0),
    (r"DSUB|D-Sub|DB9|DE9", "connector", 12.5),
    (r"JST_SH", "connector", 2.9), (r"JST_GH", "connector", 4.25), (r"JST_PH", "connector", 6.0),
    (r"JST_XH", "connector", 7.0), (r"JST_ZH", "connector", 3.5), (r"PicoBlade", "connector", 4.6),
    (r"BarrelJack", "connector", 11.0), (r"RJ45", "connector", 13.5), (r"SD|microSD|Micro_SD", "connector", 1.9),
    (r"PinHeader_\dx\d+_P2\.54mm_Vertical", "header", 8.5),
    (r"PinHeader_\dx\d+_P2\.54mm_Horizontal", "header", 2.5),
    (r"PinHeader_\dx\d+_P1\.27mm", "header", 4.0),
    (r"PinSocket", "socket", 8.5),
    (r"TerminalBlock|AK300|Phoenix|Altech", "connector", 10.0),
    (r"SW_SPST_TL3342|SW_Push_1P1T_NO_CK_KMR2", "switch", 2.0),
    (r"^SW_|Button|Switch", "switch", 3.5),
    (r"_Axial_|DO-35|DO-41|DO-15|DO-201|SOD27|SOD57|SOD64|SOD81", "axial", None),
    (r"C_Disc", "disc", None),
    (r"CP_Radial|C_Radial|CP_Elec", "electrolytic", None),
    (r"TO-220|TO-247|TO-3P", "to220", 4.5),
    (r"TO-92", "to92", 4.5),
    (r"LED_D3\.0mm|LED_D5\.0mm", "led5", None),
    (r"Crystal|Resonator|HC-?49", "crystal", 3.5),
    (r"L_\d+\.?\d*x\d+\.?\d*mm_H(\d+\.?\d*)|Inductor|L_Bourns|L_Taiyo|L_Wurth", "inductor", 3.0),
    (r"SOT-23|SOT-323|SOT-353|SOT-363|SC-70|TSOT", "gullwing", 1.1),
    (r"SOT-223|SOT-89|TO-252|TO-263|DPAK", "gullwing", 1.7),
    (r"SOIC|^SO-|SOP-|SSOP|TSSOP|MSOP|HTSSOP", "gullwing", 1.4),
    (r"LQFP|TQFP|QFP", "gullwing", 1.4),
    (r"QFN|DFN|SON|WSON|VQFN|LGA|UDFN", "nolead", 0.85),
    (r"BGA|WLCSP|CSP", "nolead", 1.2),
    (r"DIP-|PDIP|CERDIP", "dip", 3.3),
    (r"LED_(0402|0603|0805|1206)|LED_\d{4}_\d{4}Metric", "led", 0.6),
    (r"(^|[^0-9.])(0201|0603Metric|0402|1005)([^0-9]|$)", "chip", 0.35),
    (r"(^|[^0-9.])(0603|1608)([^0-9]|$)", "chip", 0.45),
    (r"(^|[^0-9.])(0805|2012)([^0-9]|$)", "chip", 0.5),
    (r"(^|[^0-9.])(1206|3216|1210|3225)([^0-9]|$)", "chip", 0.6),
    (r"(^|[^0-9.])(2010|2512|5025|6332)([^0-9]|$)", "chip", 0.6),
    (r"^LED", "led", 1.0),
]


def _dims(name: str):
    """Length and diameter from names like R_Axial_DIN0207_L6.3mm_D2.5mm or C_Disc_D5.1mm_W3.2mm."""
    get = lambda key: (lambda m: float(m.group(1)) if m else None)(re.search(rf"{key}(\d+\.?\d*)mm", name))
    return get("L"), get("D"), get("W")


AXIAL = {"DO-35": (4.0, 2.0), "SOD27": (4.0, 2.0), "DO-41": (5.2, 2.7), "SOD57": (5.0, 3.0), "DO-15": (7.6, 3.6),
         "DO-201": (9.5, 5.3), "SOD64": (5.5, 4.0), "SOD81": (4.0, 2.0)}


def classify(fid: str) -> tuple[str, float, dict]:
    """(kind, body height mm, extra dimensions) from a Library:Name footprint id."""
    lib, _, name = fid.partition(":")
    for pattern, kind, height in HEIGHTS:
        m = re.search(pattern, name)
        if not m:
            continue
        length, diameter, width = _dims(name)
        if kind == "axial":
            if not (length and diameter):
                length, diameter = next((v for key, v in AXIAL.items() if key in name), (6.3, 2.5))
            return kind, diameter, {"length": length, "diameter": diameter}
        if kind == "disc":
            return kind, (diameter or 5.0) + 1.5, {"diameter": diameter or 5.0, "thickness": width or 2.5}
        if kind == "electrolytic":
            m2 = re.search(r"CP_Elec_(\d+\.?\d*)x(\d+\.?\d*)", name)
            if m2:
                return kind, float(m2.group(2)), {"diameter": float(m2.group(1))}
            d = diameter or 5.0
            return kind, round(max(5.0, d * 1.4), 1), {"diameter": d}
        if kind == "led5":
            return "electrolytic", 8.6, {"diameter": diameter or 5.0, "lens": True}
        if kind == "inductor" and m.groups() and m.group(1):
            return kind, float(m.group(1)), {}
        return kind, height, {}
    if lib.startswith("Connector") or "Conn" in name:
        return "connector", 5.0, {}
    return "generic", 1.0, {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcb")
    ap.add_argument("-o", "--out")
    ap.add_argument("--zones", action="store_true", help="include filled pour outlines (heavier)")
    ap.add_argument("--side", default="top", choices=["top", "bottom"])
    args = ap.parse_args()

    board = pcbnew.LoadBoard(args.pcb)
    edges = board.GetBoardEdgesBoundingBox()
    ox, oy = pcbnew.ToMM(edges.GetLeft()), pcbnew.ToMM(edges.GetTop())
    P = lambda p: [round(pcbnew.ToMM(p.x) - ox, 4), round(pcbnew.ToMM(p.y) - oy, 4)]
    top = args.side == "top"
    cu, mask_layer = (pcbnew.F_Cu, pcbnew.F_Fab) if top else (pcbnew.B_Cu, pcbnew.B_Fab)

    outline = pcbnew.SHAPE_POLY_SET()
    try:
        board.GetBoardPolygonOutlines(outline, True)      # KiCad 10 signature
    except TypeError:
        board.GetBoardPolygonOutlines(outline)
    rings = []
    for i in range(outline.OutlineCount()):
        o = outline.Outline(i)
        rings.append([P(o.CPoint(j)) for j in range(o.PointCount())])
        for h in range(outline.HoleCount(i)):
            hole = outline.Hole(i, h)
            rings.append([P(hole.CPoint(j)) for j in range(hole.PointCount())])

    footprints = []
    for fp in board.GetFootprints():
        if fp.IsFlipped() == top:
            continue
        fid = f"{fp.GetFPID().GetLibNickname()}:{fp.GetFPID().GetLibItemName()}"
        kind, height, dims = classify(fid)
        body = []
        for item in fp.GraphicalItems():
            if item.GetLayer() != mask_layer or item.GetClass() not in ("PCB_SHAPE", "FP_SHAPE"):
                continue
            shape = item.GetShape()
            if shape == pcbnew.SHAPE_T_CIRCLE:
                c, r = item.GetCenter(), item.GetRadius()
                body += [P(pcbnew.VECTOR2I(int(c.x + r * math.cos(a)), int(c.y + r * math.sin(a)))) for a in [k * math.pi / 16 for k in range(32)]]
            else:
                for pt in item.GetCorners() if shape in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_POLY) else [item.GetStart(), item.GetEnd()]:
                    body.append(P(pt))
        if len(body) < 3:
            crt = fp.GetCourtyard(pcbnew.F_CrtYd if top else pcbnew.B_CrtYd)
            if crt.OutlineCount():
                o = crt.Outline(0)
                body = [P(o.CPoint(j)) for j in range(o.PointCount())]
        pads = []
        for pad in fp.Pads():
            if not pad.IsOnLayer(cu) and not pad.HasHole():
                continue
            shape = {pcbnew.PAD_SHAPE_CIRCLE: "circle", pcbnew.PAD_SHAPE_OVAL: "oval", pcbnew.PAD_SHAPE_RECT: "rect",
                     pcbnew.PAD_SHAPE_ROUNDRECT: "roundrect"}.get(pad.GetShape(pcbnew.F_Cu) if hasattr(pad, "GetShape") else pad.GetShape(), "rect")
            size = pad.GetSize(pcbnew.F_Cu) if hasattr(pcbnew, "F_Cu") else pad.GetSize()
            drill = pad.GetDrillSize()
            pads.append({"number": pad.GetNumber(), "net": pad.GetNetname(), "at": P(pad.GetPosition()),
                         "size": [round(pcbnew.ToMM(size.x), 4), round(pcbnew.ToMM(size.y), 4)], "angle": round(pad.GetOrientationDegrees(), 2),
                         "shape": shape, "drill": round(pcbnew.ToMM(drill.x), 4) if pad.HasHole() else 0,
                         "plated": pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH})
        if body and pads:
            # a fab drawing far larger than the copper (a lever, a panel outline) is not the body: use the courtyard
            bx = [p[0] for p in body]; by = [p[1] for p in body]
            px = [p["at"][0] for p in pads]; py = [p["at"][1] for p in pads]
            body_area = (max(bx) - min(bx)) * (max(by) - min(by))
            pad_area = (max(px) - min(px) + 2) * (max(py) - min(py) + 2)
            if kind not in ("hole", "edge") and body_area > 6 * pad_area + 50:
                crt = fp.GetCourtyard(pcbnew.F_CrtYd if top else pcbnew.B_CrtYd)
                if crt.OutlineCount():
                    o = crt.Outline(0)
                    body = [P(o.CPoint(j)) for j in range(o.PointCount())]
        footprints.append({"ref": fp.GetReference(), "value": fp.GetValue(), "footprint": fid, "kind": kind,
                           "height": height, "dims": dims, "at": P(fp.GetPosition()), "angle": round(fp.GetOrientationDegrees(), 2),
                           "body": body, "pads": pads})

    tracks, vias, arcs = [], [], []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append({"at": P(t.GetPosition()), "size": round(pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)), 4),
                         "drill": round(pcbnew.ToMM(t.GetDrillValue()), 4), "net": t.GetNetname()})
        elif t.GetLayer() == cu:
            entry = {"from": P(t.GetStart()), "to": P(t.GetEnd()), "width": round(pcbnew.ToMM(t.GetWidth()), 4), "net": t.GetNetname()}
            if t.Type() == pcbnew.PCB_ARC_T:
                entry["mid"] = P(t.GetMid())
            tracks.append(entry)

    zones = []
    if args.zones:
        for z in board.Zones():
            if not z.IsOnLayer(cu):
                continue
            poly = z.GetFilledPolysList(cu)
            poly.Simplify() if hasattr(poly, "Simplify") else None
            for i in range(poly.OutlineCount()):
                o = poly.Outline(i)
                ring = [P(o.CPoint(j)) for j in range(o.PointCount())]
                holes = [[P(poly.Hole(i, h).CPoint(j)) for j in range(poly.Hole(i, h).PointCount())] for h in range(poly.HoleCount(i))]
                zones.append({"net": z.GetNetname(), "outline": ring, "holes": holes})

    nets = {}
    for t in tracks:
        n = nets.setdefault(t["net"], {"length": 0.0, "pads": 0})
        n["length"] += math.dist(t["from"], t["to"])
    for fp in footprints:
        for p in fp["pads"]:
            if p["net"]:
                nets.setdefault(p["net"], {"length": 0.0, "pads": 0})["pads"] += 1
    for n in nets.values():
        n["length"] = round(n["length"], 2)

    data = {"source": str(Path(args.pcb).name), "side": args.side, "size": [round(pcbnew.ToMM(edges.GetWidth()), 3), round(pcbnew.ToMM(edges.GetHeight()), 3)],
            "thickness": round(pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()), 3), "layers": board.GetCopperLayerCount(),
            "outline": rings, "footprints": footprints, "tracks": tracks, "vias": vias, "zones": zones, "nets": nets}
    out = Path(args.out) if args.out else Path(args.pcb).with_suffix(".board.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {out}: {len(footprints)} footprints, {len(tracks)} tracks, {len(vias)} vias, {len(zones)} pour outlines")


if __name__ == "__main__":
    main()
