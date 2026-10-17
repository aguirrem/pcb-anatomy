#!/usr/bin/env python3
"""Add ground stitching vias between pours on different copper layers.

    python3 scripts/kicad/stitch.py board.kicad_pcb [--net GND] [--pitch 5]

Places a via on a grid wherever the net's pours on two layers overlap with
room to spare, then one more inside any pour island that has no via or
through-hole pad of its own (an island boxed in by tracks is otherwise
unconnected). Vias stay out of courtyards. route.py calls this for every
net that has pours on more than one layer.
"""

from __future__ import annotations

import argparse
import math

import kienv

pcbnew = kienv.pcbnew()


def _mm(v):
    return pcbnew.FromMM(v)


def _fits(polys, x, y, r) -> bool:
    pts = [(x, y)] + [(x + r * math.cos(a), y + r * math.sin(a)) for a in [i * math.pi / 4 for i in range(8)]]
    return all(p.Contains(pcbnew.VECTOR2I(int(px), int(py))) for p in polys for px, py in pts)


def _courtyards(board):
    boxes = []
    for fp in board.GetFootprints():
        for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            c = fp.GetCourtyard(layer)
            if c.OutlineCount():
                b = c.BBox()
                boxes.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))
    return boxes


def _in_box(boxes, x, y) -> bool:
    return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)


def stitch(board, net_name: str = "GND", pitch: float = 5.0) -> int:
    zones = [z for z in board.Zones() if z.GetNetname() == net_name and z.IsOnCopperLayer()]
    layers = sorted({l for z in zones for l in z.GetLayerSet().CuStack()})
    if len(layers) < 2:
        return 0
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    fills = {}
    for layer in layers:
        poly = pcbnew.SHAPE_POLY_SET()
        for z in zones:
            if z.IsOnLayer(layer):
                poly.Append(z.GetFilledPolysList(layer))
        fills[layer] = poly
    net = board.FindNet(net_name)
    ds = board.GetDesignSettings()
    width = ds.GetCurrentViaSize() or _mm(0.6)
    drill = ds.GetCurrentViaDrill() or _mm(0.3)
    r = width / 2 + _mm(0.1)
    keep = _courtyards(board)
    # never on a pad: a pour connects SMD pads solid, so the fill alone would let a via land inside one
    # (via-in-pad wicks solder away at assembly)
    grow = r + _mm(0.15)
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            b = pad.GetBoundingBox()
            pads.append((b.GetLeft() - grow, b.GetTop() - grow, b.GetRight() + grow, b.GetBottom() + grow))
    placed = []

    def add(x, y):
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        via.SetViaType(pcbnew.VIATYPE_THROUGH)
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        via.SetWidth(width)
        via.SetDrill(drill)
        via.SetNet(net)
        board.Add(via)
        placed.append((x, y))

    def clear_of_vias(x, y, gap):
        return all(math.hypot(x - px, y - py) >= gap for px, py in placed) and all(
            math.hypot(x - v.GetPosition().x, y - v.GetPosition().y) >= gap
            for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T)

    outer = board.GetBoardEdgesBoundingBox()
    step = _mm(pitch)
    both = [fills[l] for l in (layers[0], layers[-1])]
    y = outer.GetTop() + step / 2
    while y < outer.GetBottom():
        x = outer.GetLeft() + step / 2
        while x < outer.GetRight():
            if not _in_box(keep, x, y) and not _in_box(pads, x, y) and _fits(both, x, y, r) and clear_of_vias(x, y, step * 0.6):
                add(x, y)
            x += step
        y += step

    anchors = [(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks()
               if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() == net_name]
    anchors += [(p.GetPosition().x, p.GetPosition().y) for fp in board.GetFootprints() for p in fp.Pads()
                if p.GetNetname() == net_name and p.HasHole()]
    for layer in layers:
        poly = fills[layer]
        others = [fills[l] for l in layers if l != layer]
        for i in range(poly.OutlineCount()):
            outline = poly.Outline(i)
            single = pcbnew.SHAPE_POLY_SET()
            single.AddOutline(outline)
            if any(single.Contains(pcbnew.VECTOR2I(int(ax), int(ay))) for ax, ay in anchors):
                continue
            box = outline.BBox()
            fine = _mm(0.25)
            best = None
            cx, cy = (box.GetLeft() + box.GetRight()) / 2, (box.GetTop() + box.GetBottom()) / 2
            yy = box.GetTop()
            while yy <= box.GetBottom():
                xx = box.GetLeft()
                while xx <= box.GetRight():
                    if not _in_box(pads, xx, yy) and _fits([single] + others, xx, yy, r) and clear_of_vias(xx, yy, width * 1.5):
                        d = math.hypot(xx - cx, yy - cy)
                        if best is None or d < best[0]:
                            best = (d, xx, yy)
                    xx += fine
                yy += fine
            if best:
                add(best[1], best[2])
                anchors.append((best[1], best[2]))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return len(placed)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcb")
    ap.add_argument("--net", default="GND")
    ap.add_argument("--pitch", type=float, default=5.0)
    args = ap.parse_args()
    board = pcbnew.LoadBoard(args.pcb)
    n = stitch(board, args.net, args.pitch)
    pcbnew.SaveBoard(args.pcb, board)
    print(f"added {n} stitching vias on {args.net}")


if __name__ == "__main__":
    main()
