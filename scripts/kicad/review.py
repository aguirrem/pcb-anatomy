#!/usr/bin/env python3
"""Review a KiCad project: rule checks plus the design checks DRC does not do.

    python3 scripts/kicad/review.py PROJECT_DIR [--fab jlcpcb] [--currents VBUS=1.5,+3V3=0.6] [--out DIR]

Works on any KiCad 8–10 project, not only ones this skill built. Writes
PROJECT/review/review.md, review.json and review-top.png (the top side with
every located finding ringed and numbered). Each finding has a severity:

  blocker   will not work or cannot be built as is
  major     likely to cause a failure, a respin or a failed assembly
  minor     worth fixing; costs little now
  info      facts worth knowing (measurements, assumptions)

Checks: ERC; DRC with schematic parity (findings inside one footprint
reported apart); the fab's minimums against what the board actually uses;
decoupling (every IC power pin has a capacitor to ground, and how far its
pad is from the pin); floating inputs; power-net trace width against
current (IPC-2152, outer layer, 1 oz, 10 °C rise) and via counts;
differential pair length match (D+/D-, *_P/*_N); crystal trace lengths;
parts too close to the board edge; mounting holes; fiducials for fine
pitch; BOM completeness (MPN or LCSC); references missing from the
silkscreen; symbol-pin vs footprint-pad count mismatches.

The heuristics are a second pair of eyes, not a sign-off: read the
datasheets for anything they flag, and say which checks were not possible.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import kienv
import pcb_build
import sexp

pcbnew = kienv.pcbnew()

SEVERITY = ["blocker", "major", "minor", "info"]


class Findings:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, severity: str, check: str, message: str, at=None, refs=()):
        self.items.append({"severity": severity, "check": check, "message": message,
                           "at": [round(at[0], 2), round(at[1], 2)] if at else None, "refs": list(refs)})


def find(project: Path, suffix: str):
    if project.is_file():
        p = project.with_suffix(suffix)
        return p if p.exists() else None
    hits = sorted(project.glob(f"*{suffix}"))
    return hits[0] if hits else None


def rich_netlist(sch: Path) -> dict:
    out = sch.parent / ".review.net"
    kienv.run_cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch)
    node = sexp.load(str(out))
    out.unlink(missing_ok=True)
    parts = {}
    for comp in sexp.find_all(sexp.find(node, "components"), "comp"):
        fields = {str(sexp.value(f, "name")): str(f[-1]) for f in sexp.find_all(sexp.find(comp, "fields") or ["fields"], "field")
                  if len(f) > 2 and not isinstance(f[-1], list)}
        parts[str(sexp.value(comp, "ref"))] = {"value": str(sexp.value(comp, "value", "")), "footprint": str(sexp.value(comp, "footprint", "")),
                                                "fields": fields, "dnp": any(str(p[1]) == "dnp" for p in sexp.find_all(comp, "property") if len(p) > 1)}
    nets = {}
    for net in sexp.find_all(sexp.find(node, "nets"), "net"):
        nodes = []
        for n in sexp.find_all(net, "node"):
            nodes.append({"ref": str(sexp.value(n, "ref")), "pin": str(sexp.value(n, "pin")),
                          "type": str(sexp.value(n, "pintype", "")), "function": str(sexp.value(n, "pinfunction", ""))})
        nets[str(sexp.value(net, "name"))] = nodes
    return {"parts": parts, "nets": nets}


def mm_xy(p):
    return (pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))


def is_ground(name: str) -> bool:
    n = name.upper().lstrip("/")
    return "GND" in n or n in ("VSS", "0V", "EARTH")


def ipc2152_amps(width_mm: float, oz: float = 1.0, rise: float = 10.0) -> float:
    """Outer-layer current for a trace, IPC-2221 curve fit (close to IPC-2152 for small boards)."""
    area_mil2 = (width_mm / 0.0254) * (1.378 * oz)
    return 0.048 * rise ** 0.44 * area_mil2 ** 0.725


def board_checks(board, f: Findings, fab: dict, currents: dict, net: dict | None):
    edges = board.GetBoardEdgesBoundingBox()
    ox, oy = pcbnew.ToMM(edges.GetLeft()), pcbnew.ToMM(edges.GetTop())
    rel = lambda p: (pcbnew.ToMM(p.x) - ox, pcbnew.ToMM(p.y) - oy)
    w, h = pcbnew.ToMM(edges.GetWidth()), pcbnew.ToMM(edges.GetHeight())
    layers = board.GetCopperLayerCount()
    f.add("info", "board", f"{w:.1f} x {h:.1f} mm, {layers} copper layers, {pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()):.2f} mm thick, "
          f"{len(board.GetFootprints())} footprints")

    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    if tracks:
        tmin = min(tracks, key=lambda t: t.GetWidth())
        if pcbnew.ToMM(tmin.GetWidth()) < fab["track"] - 1e-6:
            f.add("blocker", "fab", f"track {pcbnew.ToMM(tmin.GetWidth()):.3f} mm is under the fab minimum {fab['track']} mm", rel(tmin.GetStart()))
        else:
            f.add("info", "fab", f"narrowest track {pcbnew.ToMM(tmin.GetWidth()):.3f} mm (fab minimum {fab['track']} mm)")
    if vias:
        vmin = min(vias, key=lambda v: v.GetDrillValue())
        drill = pcbnew.ToMM(vmin.GetDrillValue())
        ring = (pcbnew.ToMM(vmin.GetWidth(pcbnew.F_Cu)) - drill) / 2
        if drill < fab["hole"] - 1e-6:
            f.add("blocker", "fab", f"via drill {drill:.2f} mm is under the fab minimum {fab['hole']} mm", rel(vmin.GetPosition()))
        if ring < fab["annular"] - 1e-6:
            f.add("major", "fab", f"via annular ring {ring:.3f} mm is under the fab minimum {fab['annular']} mm", rel(vmin.GetPosition()))

    pads_by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
    # parts close to the edge (connectors, holes and switches belong there)
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if re.match(r"^(J|P|H|MH|SW|BT|TP|FID|LOGO)", ref):
            continue
        for pad in fp.Pads():
            b = pad.GetBoundingBox()
            gap = min(pcbnew.ToMM(b.GetLeft() - edges.GetLeft()), pcbnew.ToMM(edges.GetRight() - b.GetRight()),
                      pcbnew.ToMM(b.GetTop() - edges.GetTop()), pcbnew.ToMM(edges.GetBottom() - b.GetBottom()))
            if gap < 1.0:
                f.add("minor", "edge", f"{ref} pad {pad.GetNumber()} is {gap:.2f} mm from the board edge (depanelling stress, V-score clearance)",
                      rel(pad.GetPosition()), [ref])
                break

    # vias inside SMD pads: unplugged, they wick solder away from the joint at reflow
    smd = [(fp.GetReference(), p) for fp in board.GetFootprints() for p in fp.Pads() if not p.HasHole()]
    for v in vias:
        for ref, p in smd:
            if p.HitTest(v.GetPosition()) and p.IsOnLayer(pcbnew.F_Cu if v.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu):
                f.add("major", "via in pad", f"via on {v.GetNetname()} inside {ref} pad {p.GetNumber()}: move it off the pad, "
                      "or order filled and capped vias (via-in-pad process)", rel(v.GetPosition()), [ref])
                break

    holes = [fp for fp in board.GetFootprints() if "MountingHole" in str(fp.GetFPID().GetLibItemName()) or fp.GetReference().startswith(("H", "MH"))]
    if not holes:
        f.add("minor", "mechanical", "no mounting holes")
    fine = [fp.GetReference() for fp in board.GetFootprints()
            if any(re.search(r"P0\.[0-4]\d*mm", str(fp.GetFPID().GetLibItemName())) for _ in [0])]
    fids = [fp for fp in board.GetFootprints() if fp.GetReference().startswith("FID") or "Fiducial" in str(fp.GetFPID().GetLibItemName())]
    if fine and len(fids) < 3:
        f.add("minor", "assembly", f"fine-pitch parts ({', '.join(fine[:6])}) and {len(fids)} fiducials: machine assembly wants three")

    hidden = [fp.GetReference() for fp in board.GetFootprints()
              if not fp.GetReference().startswith(("H", "MH", "#", "FID", "LOGO")) and
              (not fp.Reference().IsVisible() or fp.Reference().GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS))]
    if hidden:
        f.add("minor", "silkscreen", f"{len(hidden)} references not on the silkscreen: {', '.join(sorted(hidden))} (assembly drawing needed)", refs=hidden)

    # nets by name with their tracks
    by_net = defaultdict(list)
    for t in tracks:
        by_net[t.GetNetname()].append(t)
    vias_by_net = defaultdict(int)
    for v in vias:
        vias_by_net[v.GetNetname()] += 1
    zone_nets = {z.GetNetname() for z in board.Zones() if z.IsOnCopperLayer()}

    # power nets: width vs current
    power = sorted({n for n in by_net if re.match(r"^/?(\+|V|VCC|VDD|VBUS|VIN|VBAT|PWR)", n.upper().lstrip("/")) or n in currents})
    for name in power:
        ts = by_net[name]
        wmin = min(pcbnew.ToMM(t.GetWidth()) for t in ts)
        cap = ipc2152_amps(wmin)
        need = currents.get(name.lstrip("/"))
        line = f"{name}: narrowest track {wmin:.2f} mm carries about {cap:.2f} A (1 oz outer, 10 °C rise), {vias_by_net[name]} vias"
        if need is not None and cap < need:
            narrow = min(ts, key=lambda t: t.GetWidth())
            f.add("major", "current", line + f"; needs {need} A", rel(narrow.GetStart()))
        else:
            f.add("info", "current", line + (f"; needs {need} A" if need is not None else "; give its current with --currents to check it"))

    # differential pairs
    pairs = {}
    names = set(by_net)
    for n in names:
        m = re.match(r"^(.*?)(D\+|DP|_P|P)$", n.lstrip("/"))
        if m:
            base, tag = m.groups()
            mate = {"D+": "D-", "DP": "DM", "_P": "_N", "P": "N"}[tag]
            other = next((x for x in names if x.lstrip("/") == base + mate), None)
            if other and tag != "P" or (other and base.upper().endswith(("USB", "CAN", "LVDS", "ETH"))):
                pairs[n] = other
    for a, b in pairs.items():
        la = sum(pcbnew.ToMM(t.GetLength()) for t in by_net[a])
        lb = sum(pcbnew.ToMM(t.GetLength()) for t in by_net[b])
        diff = abs(la - lb)
        sev = "major" if diff > 2.5 else "minor" if diff > 0.5 else "info"
        mid = by_net[a][0].GetStart() if by_net[a] else None
        f.add(sev, "diff pair", f"{a}/{b}: {la:.1f} vs {lb:.1f} mm, mismatch {diff:.2f} mm "
              "(USB 2.0 full speed tolerates a lot; high speed wants under ~1.25 mm and 90 Ω differential)", rel(mid) if mid else None)

    if net is None:
        return
    # decoupling: every IC power-input pin should see a cap to ground nearby
    caps_by_net = defaultdict(list)
    for name, nodes in net["nets"].items():
        for nd in nodes:
            if nd["ref"].startswith("C"):
                other = [x for x in net["nets"].items() if any(y["ref"] == nd["ref"] and y["pin"] != nd["pin"] for y in x[1])]
                if any(is_ground(o[0]) for o in other):
                    caps_by_net[name].append(nd)
    for name, nodes in net["nets"].items():
        if is_ground(name):
            continue
        for nd in nodes:
            if nd["type"] != "power_in" or not nd["ref"].startswith("U"):
                continue
            fp = pads_by_ref.get(nd["ref"])
            pad = next((p for p in fp.Pads() if p.GetNumber() == nd["pin"]), None) if fp else None
            if pad is None:
                continue
            caps = caps_by_net.get(name, [])
            label = f"{nd['ref']} pin {nd['pin']} ({nd['function'] or name})"
            if not caps:
                f.add("major", "decoupling", f"{label} on {name} has no capacitor to ground on its net", rel(pad.GetPosition()), [nd["ref"]])
                continue
            best = None
            for c in caps:
                cfp = pads_by_ref.get(c["ref"])
                cpad = next((p for p in cfp.Pads() if p.GetNumber() == c["pin"]), None) if cfp else None
                if cpad:
                    d = math.dist(mm_xy(pad.GetPosition()), mm_xy(cpad.GetPosition()))
                    if best is None or d < best[0]:
                        best = (d, c["ref"])
            if best is None:
                continue
            if best[0] > 10:
                f.add("major", "decoupling", f"{label}: nearest capacitor {best[1]} is {best[0]:.1f} mm away", rel(pad.GetPosition()), [nd["ref"], best[1]])
            elif best[0] > 4:
                f.add("minor", "decoupling", f"{label}: nearest capacitor {best[1]} is {best[0]:.1f} mm away (aim for under ~3 mm)",
                      rel(pad.GetPosition()), [nd["ref"], best[1]])
            else:
                f.add("info", "decoupling", f"{label}: {best[1]} at {best[0]:.1f} mm")

    # floating inputs
    for name, nodes in net["nets"].items():
        if name.startswith("unconnected-") or len(nodes) == 1:
            for nd in nodes:
                if nd["type"] in ("input",) and not nd["ref"].startswith(("J", "P", "TP")):
                    fp = pads_by_ref.get(nd["ref"])
                    pad = next((p for p in fp.Pads() if p.GetNumber() == nd["pin"]), None) if fp else None
                    f.add("major", "floating input", f"{nd['ref']} pin {nd['pin']} ({nd['function']}) is an input left floating",
                          rel(pad.GetPosition()) if pad else None, [nd["ref"]])

    # crystals: trace length from crystal to the IC
    for ref, info in net["parts"].items():
        if not re.match(r"^(Y|X)\d", ref):
            continue
        for name, nodes in net["nets"].items():
            if any(n["ref"] == ref for n in nodes) and not is_ground(name):
                length = sum(pcbnew.ToMM(t.GetLength()) for t in by_net.get(name, []))
                if length > 10:
                    f.add("minor", "crystal", f"{ref} net {name} is routed {length:.1f} mm; keep crystal traces short (under ~10 mm) and away from other signals",
                          refs=[ref])

    # BOM completeness and pin/pad counts
    missing = [r for r, p in net["parts"].items() if not r.startswith(("#", "H", "MH", "TP", "FID"))
               and not (p["fields"].get("MPN") or p["fields"].get("LCSC") or p["fields"].get("Manufacturer_Part_Number"))]
    passives = [r for r in missing if re.match(r"^(R|C|L|FB)\d", r)]
    actives = [r for r in missing if r not in passives]
    if actives:
        f.add("minor", "bom", f"no MPN or LCSC number for {', '.join(sorted(actives))}", refs=actives)
    if passives:
        f.add("info", "bom", f"{len(passives)} passives without part numbers (value and footprint may be enough for the assembler)")
    for ref, p in net["parts"].items():
        fp = pads_by_ref.get(ref)
        if not fp:
            continue
        pins = {n["pin"] for nodes in net["nets"].values() for n in nodes if n["ref"] == ref}
        pads = {pad.GetNumber() for pad in fp.Pads() if pad.GetNumber()}
        lost = sorted(pins - pads)
        if lost:
            f.add("blocker", "footprint", f"{ref}: symbol pins {', '.join(lost)} have no pad on footprint {p['footprint']}", refs=[ref])


def rule_checks(project: Path, f: Findings) -> dict:
    sch, pcb = find(project, ".kicad_sch"), find(project, ".kicad_pcb")
    summary = {}
    if sch:
        report = sch.with_name(sch.stem + "-erc.json")
        kienv.run_cli("sch", "erc", "--format", "json", "--severity-all", "-o", report, sch)
        data = json.loads(report.read_text())
        for v in (v for s in data.get("sheets", []) for v in s.get("violations", [])):
            sev = {"error": "major", "warning": "minor"}.get(v["severity"], "info")
            f.add(sev, "ERC", f"{v['type']}: {v['description']} ({'; '.join(i['description'] for i in v['items'][:2])})")
        summary["erc"] = str(report)
    if pcb:
        result = pcb_build.drc(pcb, parity=bool(sch))
        data = result["data"]
        edges = pcbnew.LoadBoard(str(pcb)).GetBoardEdgesBoundingBox()
        inside = defaultdict(list)
        for v in data.get("violations", []):
            owner = pcb_build.owner(v)
            item = v["items"][0] if v["items"] else {}
            pos = item.get("pos")
            at = (pos["x"] - pcbnew.ToMM(edges.GetLeft()), pos["y"] - pcbnew.ToMM(edges.GetTop())) if pos else None
            if owner and v["type"] in ("hole_clearance", "clearance", "hole_to_hole", "silk_over_copper", "solder_mask_bridge", "annular_width"):
                inside[(owner, v["type"])].append((v, at))
                continue
            sev = {"error": "major", "warning": "minor"}.get(v["severity"], "info")
            if v["type"] in ("shorting_items", "tracks_crossing", "unconnected_items", "clearance", "hole_clearance", "copper_edge_clearance"):
                sev = "blocker" if v["severity"] == "error" else sev
            f.add(sev, "DRC", f"{v['type']}: {v['description']} ({'; '.join(i['description'] for i in v['items'][:2])})", at)
        for (owner, kind), hits in inside.items():
            v, at = hits[0]
            many = f" (x{len(hits)})" if len(hits) > 1 else ""
            f.add("minor", "DRC (inside footprint)", f"{owner}: {v['description']}{many}; comes from the footprint itself, "
                  "so confirm the fab can build it or edit the footprint", at, [owner])
        for v in data.get("schematic_parity", []):
            f.add("major", "parity", f"{v['type']}: {v['description']} ({'; '.join(i['description'] for i in v['items'][:2])})")
        if result["unrouted"]:
            f.add("blocker", "DRC", f"{result['unrouted']} connections are not routed")
        summary["drc"] = result["report"]
    return summary


def annotate(pcb: Path, findings: list[dict], out: Path) -> Path | None:
    located = [(i, x) for i, x in enumerate(findings, 1) if x["at"] and x["severity"] != "info"]
    svg = out / "review-top.svg"
    kienv.run_cli("pcb", "export", "svg", "--layers", "F.Cu,F.Silkscreen,F.Mask,Edge.Cuts", "--mode-single",
                  "--fit-page-to-board", "--exclude-drawing-sheet", "-o", svg, pcb)
    text = svg.read_text()
    colours = {"blocker": "#d40000", "major": "#ff7a00", "minor": "#1f6feb"}
    marks = []
    for n, item in located:
        x, y = item["at"]
        c = colours[item["severity"]]
        marks.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="1.2" fill="none" stroke="{c}" stroke-width="0.25"/>'
                     f'<text x="{x + 1.3:.3f}" y="{y - 1.0:.3f}" font-family="sans-serif" font-size="1.4" font-weight="bold" '
                     f'fill="{c}" stroke="white" stroke-width="0.12" paint-order="stroke">{n}</text>')
    text = text.replace("</svg>", "".join(marks) + "</svg>")
    svg.write_text(text)
    png = out / "review-top.png"
    from render import rasterize
    rasterize(svg, png, 1800)
    svg.unlink()
    return png


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--fab", default="jlcpcb", choices=sorted(pcb_build.FAB))
    ap.add_argument("--currents", default="", help="NET=amps,... expected current per power net")
    ap.add_argument("--out")
    args = ap.parse_args()
    project = Path(args.project).resolve()
    base = project if project.is_dir() else project.parent
    out = Path(args.out).resolve() if args.out else base / "review"
    out.mkdir(parents=True, exist_ok=True)
    currents = {k.strip(): float(v) for k, v in (p.split("=") for p in args.currents.split(",") if "=" in p)}

    f = Findings()
    summary = rule_checks(project, f)
    sch, pcb = find(project, ".kicad_sch"), find(project, ".kicad_pcb")
    net = rich_netlist(sch) if sch else None
    if pcb:
        board = pcbnew.LoadBoard(str(pcb))
        board_checks(board, f, pcb_build.FAB[args.fab], currents, net)
    not_checked = ["signal integrity beyond pair length", "thermal dissipation", "EMC", "datasheet-specific layout rules",
                   "component availability and lifecycle", "mechanical fit with the enclosure"]
    if not pcb:
        not_checked.insert(0, "everything on the board (no .kicad_pcb found)")
    if not sch:
        not_checked.insert(0, "ERC, decoupling, floating inputs, BOM (no .kicad_sch found)")

    order = {s: i for i, s in enumerate(SEVERITY)}
    f.items.sort(key=lambda x: (order[x["severity"]], x["check"]))
    image = annotate(pcb, f.items, out) if pcb else None
    counts = {s: sum(1 for x in f.items if x["severity"] == s) for s in SEVERITY}
    (out / "review.json").write_text(json.dumps({"project": base.name, "fab": args.fab, "counts": counts, "findings": f.items,
                                                 "reports": summary, "not_checked": not_checked}, indent=2))
    lines = [f"# Design review: {base.name}", "",
             f"Fab rules: **{args.fab}**. Findings: " + ", ".join(f"{counts[s]} {s}" for s in SEVERITY), ""]
    if image:
        lines += ["Located findings are ringed on `review-top.png` with their number.", ""]
    for s in SEVERITY:
        rows = [(i, x) for i, x in enumerate(f.items, 1) if x["severity"] == s]
        if not rows:
            continue
        lines += [f"## {s.capitalize()}", ""]
        for i, x in rows:
            where = f" (at {x['at'][0]:.1f}, {x['at'][1]:.1f} mm)" if x["at"] else ""
            lines.append(f"{i}. **{x['check']}**: {x['message']}{where}")
        lines.append("")
    lines += ["## Not checked", "", *[f"- {n}" for n in not_checked], ""]
    (out / "review.md").write_text("\n".join(lines))
    print(f"review: " + ", ".join(f"{counts[s]} {s}" for s in SEVERITY))
    print("wrote", out / "review.md")
    if image:
        print("wrote", image)


if __name__ == "__main__":
    main()
