#!/usr/bin/env python3
"""Build a placed, unrouted KiCad board from a circuit spec and its schematic.

    python3 scripts/kicad/pcb_build.py spec.yaml [--out DIR] [--keep-placement]

Reads KiCad's netlist of the schematic (build it first with sch_build.py),
so footprints, values and nets on the board always match the schematic.
Then it draws the outline and mounting holes, sets the design rules and net
classes from the spec's `rules` (with a fab preset), places every footprint
listed under `placement`, auto-places the rest, adds the copper pours and
saves `<project>.kicad_pcb`. It finishes with a DRC including schematic
parity; unrouted connections are expected until route.py runs.

Auto-placement is a first proposal, not a layout: decoupling capacitors go
next to the IC pad that shares their supply net, other parts go as close as
they fit to the parts they connect to. Render it (render.py), look at it,
and move parts by adding them to `placement`. `--keep-placement` re-uses
the positions already on an existing board for any part not in `placement`,
so hand edits made in KiCad survive a rebuild.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import fplib
import kienv
import sch_build
import silk
import spec as specmod

pcbnew = kienv.pcbnew()

ORIGIN = (100.0, 100.0)  # mm: where the board's top-left corner sits on the page

FAB = {
    # min track, min clearance, via pad, via drill, min hole, annular ring, copper-to-edge, hole-to-hole
    "jlcpcb": dict(track=0.127, clearance=0.127, via=(0.45, 0.2), hole=0.2, annular=0.13, edge=0.3, hole_to_hole=0.5, hole_clearance=0.2),
    "pcbway": dict(track=0.1, clearance=0.1, via=(0.4, 0.2), hole=0.2, annular=0.1, edge=0.3, hole_to_hole=0.5, hole_clearance=0.2),
    "oshpark": dict(track=0.152, clearance=0.152, via=(0.508, 0.254), hole=0.254, annular=0.127, edge=0.381, hole_to_hole=0.5, hole_clearance=0.254),
    "generic": dict(track=0.15, clearance=0.15, via=(0.6, 0.3), hole=0.3, annular=0.15, edge=0.5, hole_to_hole=0.5, hole_clearance=0.25),
}


def mm(v: float) -> int:
    return pcbnew.FromMM(float(v))


def vec(x: float, y: float):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def board_xy(p) -> tuple[float, float]:
    return (pcbnew.ToMM(p.x) - ORIGIN[0], pcbnew.ToMM(p.y) - ORIGIN[1])


# -- outline ------------------------------------------------------------------
def outline(board, w: float, h: float, r: float, width: float = 0.1) -> None:
    ox, oy = ORIGIN
    def seg(a, b):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(vec(*a)); s.SetEnd(vec(*b)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(width)); board.Add(s)
    def arc(c, start, angle):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        s.SetCenter(vec(*c)); s.SetStart(vec(*start)); s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(angle, pcbnew.DEGREES_T), True)
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(width)); board.Add(s)
    if r <= 0:
        pts = [(ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            seg(a, b)
        return
    seg((ox + r, oy), (ox + w - r, oy)); seg((ox + w, oy + r), (ox + w, oy + h - r))
    seg((ox + w - r, oy + h), (ox + r, oy + h)); seg((ox, oy + h - r), (ox, oy + r))
    arc((ox + w - r, oy + r), (ox + w - r, oy), 90)
    arc((ox + w - r, oy + h - r), (ox + w, oy + h - r), 90)
    arc((ox + r, oy + h - r), (ox + r, oy + h), 90)
    arc((ox + r, oy + r), (ox, oy + r), 90)


# -- footprints ---------------------------------------------------------------
def load_footprint(board, fid: str, project_dir: Path):
    path = fplib.resolve(fid, project_dir)
    fp = pcbnew.FootprintLoad(str(path.parent), path.stem)
    if fp is None:
        raise SystemExit(f"KiCad could not load footprint {fid}")
    fp.SetFPID(pcbnew.LIB_ID(fid.split(":")[0], path.stem))
    board.Add(fp)
    return fp


def courtyard(fp, margin: float = 0.0) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) in board mm of the courtyard, or of the pads and body when there is none."""
    box = None
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        poly = fp.GetCourtyard(layer)
        if poly.OutlineCount() > 0:
            b = poly.BBox()
            box = b if box is None else box.Merge(b) or box
    if box is None:
        box = fp.GetBoundingBox(False)
    x0, y0 = board_xy(pcbnew.VECTOR2I(box.GetLeft(), box.GetTop()))
    x1, y1 = board_xy(pcbnew.VECTOR2I(box.GetRight(), box.GetBottom()))
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def set_field(fp, name: str, text: str) -> None:
    """Set a footprint field, creating it hidden on the fab layer if it is new."""
    field = fp.GetField(name) if fp.HasField(name) else None
    if field is None:
        field = pcbnew.PCB_FIELD(fp, getattr(pcbnew, "FIELD_T_USER", len(fp.GetFields())), name)
        field.SetLayer(pcbnew.F_Fab)
        field.SetVisible(False)
        fp.Add(field)
    field.SetText(str(text))
    field.SetVisible(False)


def overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def set_pose(fp, x: float, y: float, rot: float = 0, side: str = "top") -> None:
    if side == "bottom" and not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT if hasattr(pcbnew, "FLIP_DIRECTION_LEFT_RIGHT") else False)
    fp.SetOrientationDegrees(rot)
    fp.SetPosition(vec(ORIGIN[0] + x, ORIGIN[1] + y))


class Placer:
    def __init__(self, board, w: float, h: float, edge: float, keepouts: list):
        self.board, self.w, self.h, self.edge = board, w, h, edge
        self.taken = list(keepouts)

    def fits(self, box) -> bool:
        if box[0] < self.edge or box[1] < self.edge or box[2] > self.w - self.edge or box[3] > self.h - self.edge:
            return False
        return not any(overlaps(box, t) for t in self.taken)

    def claim(self, fp, margin: float) -> None:
        self.taken.append(courtyard(fp, margin))

    def near(self, fp, target, margin: float, rotations=(0, 90, 180, 270), step: float = 0.25, reach: float = 60):
        """Put fp at the free spot nearest `target`, trying each rotation; returns False if nothing fits."""
        best = None
        ring = 0
        while ring * step <= reach:
            for dx, dy in self._ring(ring):
                x, y = target[0] + dx * step, target[1] + dy * step
                for rot in rotations:
                    set_pose(fp, x, y, rot)
                    box = courtyard(fp, margin)
                    if self.fits(box):
                        d = math.hypot(dx, dy) * step
                        if best is None or d < best[0] - 1e-9:
                            best = (d, x, y, rot)
                if best is not None and ring > 0:
                    pass
            if best is not None and best[0] <= ring * step:
                break
            ring += 1
        if best is None:
            return False
        set_pose(fp, best[1], best[2], best[3])
        self.claim(fp, margin)
        return True

    @staticmethod
    def _ring(n: int):
        if n == 0:
            yield (0, 0)
            return
        for i in range(-n, n + 1):
            yield (i, -n); yield (i, n)
        for i in range(-n + 1, n):
            yield (-n, i); yield (n, i)


def pad_xy(fp, number: str):
    for pad in fp.Pads():
        if pad.GetNumber() == number:
            return board_xy(pad.GetPosition())
    return board_xy(fp.GetPosition())


# -- rules and project file ---------------------------------------------------
def project_settings(pro: Path, sp: specmod.Spec, rules: dict, fab: dict) -> None:
    data = json.loads(pro.read_text()) if pro.exists() else {}
    data.setdefault("meta", {"filename": pro.name, "version": 3})
    track = float(rules.get("track", max(0.2, fab["track"])))
    clearance = float(rules.get("clearance", max(0.2, fab["clearance"])))
    via = rules.get("via", list(fab["via"]))
    classes = [{
        "name": "Default", "clearance": clearance, "track_width": track, "via_diameter": via[0], "via_drill": via[1],
        "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
        "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12, "line_style": 0, "priority": 2147483647,
        "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
    }]
    patterns = []
    for i, (name, nc) in enumerate((rules.get("netclasses") or {}).items()):
        cls = dict(classes[0])
        cls.update({"name": name, "priority": i,
                    "clearance": float(nc.get("clearance", clearance)), "track_width": float(nc.get("track", track))})
        if "via" in nc:
            cls["via_diameter"], cls["via_drill"] = nc["via"]
        if "diff_pair" in nc:
            cls["diff_pair_width"], cls["diff_pair_gap"] = nc["diff_pair"]
        classes.append(cls)
        patterns += [{"netclass": name, "pattern": str(n)} for n in nc.get("nets", [])]
    data["net_settings"] = {"classes": classes, "meta": {"version": 5}, "net_colors": None,
                            "netclass_assignments": None, "netclass_patterns": patterns}
    ds = data.setdefault("board", {}).setdefault("design_settings", {})
    ds.setdefault("defaults", {})
    ds["rules"] = {
        "min_track_width": fab["track"], "min_clearance": fab["clearance"], "min_via_diameter": fab["via"][0],
        "min_through_hole_diameter": fab["hole"], "min_via_annular_width": fab["annular"],
        "min_copper_edge_clearance": fab["edge"], "min_hole_to_hole": fab["hole_to_hole"],
        "min_hole_clearance": fab["hole_clearance"], "min_silk_clearance": 0.0, "min_text_height": 0.6, "min_text_thickness": 0.1,
        "allow_blind_buried_vias": False, "allow_microvias": False, "max_error": 0.005, "min_resolved_spokes": 2,
        "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1, "min_via_annulus": fab["annular"],
        "solder_mask_to_copper_clearance": 0.0, "use_height_for_length_calcs": True, "min_connection": 0.0,
        "min_groove_width": 0.0,
    }
    widths = sorted({0.0, track, *[float(nc.get("track", track)) for nc in (rules.get("netclasses") or {}).values()]})
    ds["track_widths"] = widths
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": via[0], "drill": via[1]}]
    data.setdefault("pcbnew", {})
    data.setdefault("libraries", {"pinned_footprint_libs": [], "pinned_symbol_libs": []})
    pro.write_text(json.dumps(data, indent=2))


def apply_rules(board, rules: dict, fab: dict) -> None:
    ds = board.GetDesignSettings()
    ds.m_TrackMinWidth = mm(fab["track"])
    ds.m_MinClearance = mm(fab["clearance"])
    ds.m_ViasMinSize = mm(fab["via"][0])
    ds.m_MinThroughDrill = mm(fab["hole"])
    ds.m_ViasMinAnnularWidth = mm(fab["annular"])
    ds.m_CopperEdgeClearance = mm(fab["edge"])
    ds.m_HoleToHoleMin = mm(fab["hole_to_hole"])
    ds.m_HoleClearance = mm(fab["hole_clearance"])


def add_zone(board, net, layer, w: float, h: float, inset: float, clearance: float) -> None:
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNet(net)
    poly = zone.Outline()
    poly.NewOutline()
    for x, y in ((inset, inset), (w - inset, inset), (w - inset, h - inset), (inset, h - inset)):
        poly.Append(mm(ORIGIN[0] + x), mm(ORIGIN[1] + y))
    zone.SetLocalClearance(mm(clearance))
    zone.SetMinThickness(mm(0.2))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)   # SMD pads solid, through-hole pads with spokes
    zone.SetThermalReliefGap(mm(0.3))
    zone.SetThermalReliefSpokeWidth(mm(0.4))
    zone.SetAssignedPriority(0)
    zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)   # drop copper that connects to nothing
    board.Add(zone)


# -- main ---------------------------------------------------------------------
def build(sp: specmod.Spec, out_dir: Path, keep_placement: bool) -> Path:
    sch = out_dir / f"{sp.project}.kicad_sch"
    if not sch.exists():
        raise SystemExit(f"{sch} not found: run sch_build.py first")
    net = sch_build.netlist(sch)
    cfg = sp.raw.get("board") or {}
    rules = sp.raw.get("rules") or {}
    fab = dict(FAB.get(str(rules.get("fab", "jlcpcb")), FAB["jlcpcb"]))
    w, h = (float(v) for v in cfg.get("size", [50, 40]))
    corner = float(cfg.get("corner", 1))
    edge = float(cfg.get("edge_clearance", fab["edge"]))
    margin = float(cfg.get("spacing", 0.6)) / 2

    path = out_dir / f"{sp.project}.kicad_pcb"
    previous = {}
    if keep_placement and path.exists():
        old = pcbnew.LoadBoard(str(path))
        for fp in old.GetFootprints():
            x, y = board_xy(fp.GetPosition())
            previous[fp.GetReference()] = (x, y, fp.GetOrientationDegrees(), "bottom" if fp.IsFlipped() else "top")

    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(int(cfg.get("layers", 2)))
    board.GetDesignSettings().SetBoardThickness(mm(float(cfg.get("thickness", 1.6))))
    apply_rules(board, rules, fab)
    outline(board, w, h, corner)
    origin = vec(ORIGIN[0], ORIGIN[1] + h)          # bottom-left corner: what fabs expect for drill and place files
    board.GetDesignSettings().SetAuxOrigin(origin)
    board.GetDesignSettings().SetGridOrigin(origin)

    nets = {}
    for name in sorted(net["nets"]):
        info = pcbnew.NETINFO_ITEM(board, name)
        board.Add(info)
        nets[name] = info
    node_net = {node: name for name, nodes in net["nets"].items() for node in nodes}

    keepouts = []
    holes = cfg.get("holes")
    if holes:
        inset = float(holes.get("inset", 3.5))
        fid = holes.get("footprint", "MountingHole:MountingHole_3.2mm_M3")
        spots = holes.get("at") or [(inset, inset), (w - inset, inset), (w - inset, h - inset), (inset, h - inset)]
        for i, (x, y) in enumerate(spots, 1):
            fp = load_footprint(board, fid, sp.project_dir)
            fp.SetReference(f"H{i}"); fp.SetValue("MountingHole")
            fp.SetBoardOnly(True); fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
            set_pose(fp, x, y)
            keepouts.append(courtyard(fp, margin))

    fps = {}
    for ref, info in sorted(net["parts"].items()):
        if not info["footprint"]:
            continue
        fp = load_footprint(board, info["footprint"], sp.project_dir)
        fp.SetReference(ref)
        fp.SetValue(str(info["value"]))
        uuid = info["tstamps"][-1] if info["tstamps"] else ""
        if uuid:
            sheet = str(info["sheetpath"]).rstrip("/")
            fp.SetPath(pcbnew.KIID_PATH(f"{sheet}/{uuid}"))
        for k, v in info.get("fields", {}).items():
            set_field(fp, k, v)
        if info.get("datasheet") not in (None, "", "~"):
            set_field(fp, "Datasheet", info["datasheet"])
        if info.get("description"):
            set_field(fp, "Description", info["description"])
        part = sp.parts.get(ref)
        if part and part.dnp:
            fp.SetDNP(True)
        for pad in fp.Pads():
            name = node_net.get((ref, pad.GetNumber()))
            if name:
                pad.SetNet(nets[name])
        fps[ref] = fp

    placer = Placer(board, w, h, edge, keepouts)
    plan = sp.raw.get("placement") or {}
    pending = []
    for ref, fp in fps.items():
        if ref in plan:
            p = plan[ref]
            x, y = p["at"]
            set_pose(fp, x, y, float(p.get("rot", 0)), p.get("side", "top"))
            box = courtyard(fp, margin)
            if not placer.fits(box):
                clash = [t for t in placer.taken if overlaps(box, t)]
                where = f"overlaps {len(clash)} placed part(s) or hole(s)" if clash else "crosses the edge margin (fine for an edge connector)"
                print(f"note: {ref} at {p['at']} {where}")
            placer.claim(fp, margin)
        elif ref in previous:
            x, y, rot, side = previous[ref]
            set_pose(fp, x, y, rot, side)
            placer.claim(fp, margin)
        else:
            pending.append(ref)

    power_nets = set()
    for nc in (rules.get("netclasses") or {}).values():
        power_nets |= set(map(str, nc.get("nets", [])))
    power_nets |= {n for n in net["nets"] if n.upper().startswith(("GND", "+", "V")) and len(net["nets"][n]) > 4}
    grounds = {n for n in net["nets"] if "GND" in n.upper()}
    pins_of = defaultdict(list)
    for (ref, pin), name in node_net.items():
        pins_of[ref].append((pin, name))

    def size(ref):
        x0, y0, x1, y1 = courtyard(fps[ref])
        return (x1 - x0) * (y1 - y0)

    assigned = defaultdict(int)       # caps already sent to each IC pad, so several caps on one rail spread out
    pintypes = net.get("pintypes", {})

    def near_target(ref):
        """`near: U1.VOUT` on a part in the spec: put it by that pin."""
        want = (sp.raw.get("parts", {}).get(ref) or {}).get("near")
        if not want:
            return None
        other, _, pin = str(want).partition(".")
        if other not in fps or other in pending:
            return None
        numbers = specmod._resolve_pin(sp.parts[other], pin) if other in sp.parts else [pin]
        return pad_xy(fps[other], numbers[0])

    def decoupling_target(ref):
        """For a two-pin cap between a supply and ground: the IC supply pad it should sit by.
        ICs before connectors, a regulator's output first, then each power input in turn."""
        names = {n for _, n in pins_of[ref]}
        if len(pins_of[ref]) != 2 or not (names & grounds) or not ref.startswith("C"):
            return None
        supply = next(iter(names - grounds), None)
        if supply is None:
            return None
        options = []
        for other, fp in fps.items():
            if other == ref or other in pending or len(pins_of[other]) < 3:
                continue
            for pin, n in pins_of[other]:
                if n != supply:
                    continue
                kind = pintypes.get((other, pin), "")
                is_ic = other.startswith(("U", "IC", "Q"))
                rank = (assigned[(other, pin)], 0 if is_ic else 1, 0 if kind == "power_out" else 1 if kind == "power_in" else 2, -len(pins_of[other]))
                options.append((rank, other, pin))
        if not options:
            return None
        options.sort()
        _, other, pin = options[0]
        assigned[(other, pin)] += 1
        return pad_xy(fps[other], pin)

    def affinity_target(ref):
        pts, wts = [], []
        for pin, n in pins_of[ref]:
            members = net["nets"][n]
            weight = 0.0 if n in grounds else 1.0 / max(1, len(members) - 1)   # ground pulls nowhere: it is everywhere
            for other, opin in members:
                if other != ref and other in fps and other not in pending:
                    pts.append(pad_xy(fps[other], opin)); wts.append(weight)
        if not pts or sum(wts) == 0:
            return (w / 2, h / 2)
        total = sum(wts)
        return (sum(p[0] * k for p, k in zip(pts, wts)) / total, sum(p[1] * k for p, k in zip(pts, wts)) / total)

    unplaced = []
    while pending:
        # most-connected-to-placed first, big parts before small
        def score(ref):
            linked = sum(1 for pin, n in pins_of[ref] for other, _ in net["nets"][n] if other not in pending and other != ref)
            is_cap = ref.startswith("C") and len(pins_of[ref]) == 2
            return (len(pins_of[ref]) >= 4, is_cap, linked, size(ref))
        pending.sort(key=score, reverse=True)
        ref = pending.pop(0)
        target = near_target(ref) or decoupling_target(ref) or affinity_target(ref)
        if not placer.near(fps[ref], target, margin, reach=max(w, h)):
            unplaced.append(ref)
            set_pose(fps[ref], w + 10 + 8 * len(unplaced), h / 2)

    for pour in sp.raw.get("pours") or []:
        name = str(pour["net"])
        if name not in nets:
            continue
        for layer_name in pour.get("layers", ["F.Cu", "B.Cu"]):
            add_zone(board, nets[name], board.GetLayerID(layer_name), w, h, float(pour.get("inset", edge)),
                     float(pour.get("clearance", max(0.25, fab["clearance"]))))
    off_silk = silk.tidy(board)
    if off_silk:
        print("references moved to the fab layer (no room on silkscreen):", ", ".join(off_silk))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(str(path), board)
    project_settings(path.with_suffix(".kicad_pro"), sp, rules, fab)
    if unplaced:
        print("could not fit (parked right of the board):", ", ".join(unplaced))
    return path


_REF = re.compile(r"(?:\bof|Footprint)\s+([A-Za-z#]+[0-9]+)")


def owner(violation: dict) -> str | None:
    """The one footprint every item of a violation belongs to, if there is exactly one."""
    refs = set()
    for item in violation.get("items", []):
        m = _REF.search(item.get("description", ""))
        if not m:
            return None
        refs.add(m.group(1))
    return refs.pop() if len(refs) == 1 else None


def drc(pcb: Path, parity: bool = True) -> dict:
    report = pcb.with_name(pcb.stem + "-drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "-o", report]
    if parity:
        args.append("--schematic-parity")
    kienv.run_cli(*args, pcb)
    data = json.loads(report.read_text())
    counts, inherent = defaultdict(int), defaultdict(int)
    for v in data.get("violations", []):
        fp = owner(v) if v["type"] in ("hole_clearance", "clearance", "copper_edge_clearance", "hole_to_hole",
                                      "silk_over_copper", "silk_overlap", "solder_mask_bridge", "annular_width") else None
        if fp:
            inherent[(v["severity"], v["type"], fp)] += 1
        else:
            counts[(v["severity"], v["type"])] += 1
    for v in data.get("schematic_parity", []):
        counts[("parity", v["type"])] += 1
    return {"report": str(report), "data": data, "counts": dict(counts), "inherent": dict(inherent),
            "unrouted": len(data.get("unconnected_items", []))}


def print_drc(result: dict) -> int:
    """Print a summary; returns the number of real errors (inside-footprint findings excluded)."""
    errors = sum(n for (sev, _), n in result["counts"].items() if sev in ("error", "parity"))
    total = sum(result["counts"].values()) + sum(result["inherent"].values())
    print(f"DRC: {total} findings, {errors} layout errors or parity, {result['unrouted']} unrouted -> {result['report']}")
    for (sev, kind), n in sorted(result["counts"].items()):
        print(f"  {sev:8} {kind:32} x{n}")
    for (sev, kind, fp), n in sorted(result["inherent"].items()):
        print(f"  inside {fp:<4} {kind:30} x{n}  (from the footprint itself: confirm with the fab or fix the footprint)")
    return errors + (1 if result["unrouted"] else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    ap.add_argument("--keep-placement", action="store_true")
    args = ap.parse_args()
    sp = specmod.load(args.spec)
    out_dir = Path(args.out).resolve() if args.out else sp.project_dir
    path = build(sp, out_dir, args.keep_placement)
    print(f"wrote {path}")
    print_drc(drc(path))


if __name__ == "__main__":
    main()
