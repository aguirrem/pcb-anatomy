"""Tidy reference designators on the silkscreen after placement.

Each footprint's reference is set to a small, legible size and moved to the
first free spot around its courtyard (above, below, left, right), clear of
every pad, hole, other reference and the board edge. A reference with no
free spot is moved to the fab layer and reported, so the silkscreen never
prints over copper. Values stay on the fab layer.
"""

from __future__ import annotations

import kienv

pcbnew = kienv.pcbnew()


def _mm(v):
    return pcbnew.FromMM(v)


def _box(item, grow=0.0):
    b = item.GetBoundingBox()
    g = _mm(grow)
    return (b.GetLeft() - g, b.GetTop() - g, b.GetRight() + g, b.GetBottom() + g)


def _hit(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _edge_box(board):
    b = board.GetBoardEdgesBoundingBox()
    return (b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom())


def tidy(board, size: float = 0.8, thickness: float = 0.12, gap: float = 0.25, edge: float = 0.3) -> list[str]:
    """Returns the references that had to leave the silkscreen."""
    obstacles = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            obstacles.append(_box(pad, 0.15))
        for field in (fp.Value(),):
            if field.IsVisible() and field.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                field.SetLayer(pcbnew.F_Fab if not fp.IsFlipped() else pcbnew.B_Fab)
    for fp in board.GetFootprints():
        for item in fp.GraphicalItems():
            if item.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                obstacles.append(_box(item, 0.05))
    bx = _edge_box(board)
    inner = (bx[0] + _mm(edge), bx[1] + _mm(edge), bx[2] - _mm(edge), bx[3] - _mm(edge))
    moved = []
    texts = []
    order = sorted(board.GetFootprints(), key=lambda f: -f.GetBoundingBox(False).GetArea())
    for fp in order:
        ref = fp.Reference()
        if not ref.IsVisible() or fp.GetReference().startswith("H"):
            if fp.GetReference().startswith("H"):
                ref.SetVisible(False)
            continue
        silk = pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
        ref.SetLayer(silk)
        ref.SetTextSize(pcbnew.VECTOR2I(_mm(size), _mm(size)))
        ref.SetTextThickness(_mm(thickness))
        crt = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
        cb = crt.BBox() if crt.OutlineCount() else fp.GetBoundingBox(False)
        cx, cy = (cb.GetLeft() + cb.GetRight()) // 2, (cb.GetTop() + cb.GetBottom()) // 2
        half = _mm(size / 2 + gap)
        spots = [
            ((cx, cb.GetTop() - half), 0), ((cx, cb.GetBottom() + half), 0),
            ((cb.GetLeft() - half, cy), 90), ((cb.GetRight() + half, cy), 90),
            ((cb.GetLeft() + _mm(size), cb.GetTop() - half), 0), ((cb.GetRight() - _mm(size), cb.GetBottom() + half), 0),
            ((cx, cy), 0), ((cx, cy), 90),
        ]
        placed = False
        for (x, y), angle in spots:
            ref.SetTextAngleDegrees(angle)
            ref.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
            box = _box(ref, 0.05)
            if box[0] < inner[0] or box[1] < inner[1] or box[2] > inner[2] or box[3] > inner[3]:
                continue
            if any(_hit(box, o) for o in obstacles) or any(_hit(box, t) for t in texts):
                continue
            texts.append(box)
            placed = True
            break
        if not placed:
            ref.SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)
            ref.SetTextAngleDegrees(0)
            ref.SetPosition(pcbnew.VECTOR2I(cx, cy))
            moved.append(fp.GetReference())
    return moved
