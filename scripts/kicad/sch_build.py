#!/usr/bin/env python3
"""Build a KiCad schematic (.kicad_sch + .kicad_pro) from a circuit spec.

    python3 scripts/kicad/sch_build.py spec.yaml [--out DIR] [--no-erc]

Every pin gets a short wire stub ending in a net label, or in a power symbol
for nets that KiCad's `power` library knows (GND, +3V3, +5V, VBUS, ...).
Connectivity therefore never depends on wire geometry, and ERC checks the
result. Symbols are laid out by `group`, each group in its own frame.
Nets whose only sources are power-input pins get a PWR_FLAG, and the script
prints which; check that each really is fed from outside the board.
After writing, it runs ERC and compares KiCad's netlist with the spec.
"""

from __future__ import annotations

import argparse
import json
import uuid as uuidlib
from collections import defaultdict
from pathlib import Path

import kienv
import libsym
import sexp
import spec as specmod
from sexp import Sym

GRID = 1.27
STUB = 2.54
FONT = [Sym("font"), [Sym("size"), 1.27, 1.27]]
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]
NAMESPACE = uuidlib.UUID("6f1c5a2e-7d1b-4c1e-9a52-6b8a1f2c9d00")


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


class Ids:
    """Stable UUIDs, so rebuilding a spec keeps every symbol's identity (and the board's links to it)."""

    def __init__(self, project: str):
        self.project = project

    def __call__(self, *key) -> str:
        return str(uuidlib.uuid5(NAMESPACE, self.project + "/" + "/".join(map(str, key))))


def yesno(flag: bool) -> Sym:
    return Sym("yes" if flag else "no")


def effects(justify: str | None = None, hide: bool = False) -> list:
    node = [Sym("effects"), FONT]
    if justify:
        node.append([Sym("justify"), *[Sym(j) for j in justify.split()]])
    if hide:
        node.append([Sym("hide"), Sym("yes")])
    return node


def power_symbols() -> set[str]:
    path = kienv.share_dir("symbols") / "power.kicad_sym"
    return {n[1] for n in sexp.find_all(sexp.load(str(path)), "symbol")} if path.exists() else set()


def is_ground(net: str) -> bool:
    return "GND" in net.upper() or net.upper() in ("VSS", "EARTH", "CHASSIS")


ROT_GROUND = {(0, 1): 0, (0, -1): 180, (1, 0): 90, (-1, 0): 270}
ROT_SUPPLY = {(0, -1): 0, (0, 1): 180, (1, 0): 270, (-1, 0): 90}
LABEL = {(1, 0): (0, "left bottom"), (-1, 0): (180, "right bottom"), (0, -1): (90, "left bottom"), (0, 1): (270, "right bottom")}


class Sheet:
    def __init__(self, sp: specmod.Spec):
        self.sp = sp
        self.ids = Ids(sp.project)
        self.root = self.ids("root")
        self.items: list = []
        self.lib_symbols: dict[str, list] = {}
        self.power_lib = power_symbols()
        self.pwr_count = 0
        self.flagged: list[str] = []

    # -- symbols -------------------------------------------------------------
    def use_lib(self, node: list) -> None:
        self.lib_symbols.setdefault(node[1], node)

    def place(self, node, ref, value, footprint, x, y, unit=1, rot=0, fields=None, dnp=False, hide_ref=False, key=None):
        self.use_lib(node)
        sid = self.ids("sym", key or ref, unit)
        props = []
        lib_props = {p[1]: p for p in sexp.find_all(node, "property")}
        values = {"Reference": ref, "Value": value, "Footprint": footprint or "", "Datasheet": sexp.prop(node, "Datasheet", "") or "",
                  "Description": sexp.prop(node, "Description", "") or ""}
        for name, text in values.items():
            lp = lib_props.get(name)
            at = sexp.find(lp, "at") if lp else None
            px, py = (float(at[1]), -float(at[2])) if at else (0.0, 0.0)
            angle = at[3] if at is not None and len(at) > 3 else 0
            eff = sexp.find(lp, "effects") if lp else None
            hidden = name in ("Footprint", "Datasheet", "Description") or (name == "Reference" and hide_ref)
            if eff is not None:
                eff = [c for c in eff if not (isinstance(c, list) and str(c[0]) == "hide")]
                if hidden or (lp is not None and sexp.find(sexp.find(lp, "effects") or [], "hide") is not None):
                    eff.append([Sym("hide"), Sym("yes")])
            else:
                eff = effects(hide=hidden)
            props.append([Sym("property"), name, text, [Sym("at"), snap(x + px) if not hidden else x, snap(y + py) if not hidden else y, angle], eff])
        for name, text in (fields or {}).items():
            props.append([Sym("property"), name, text, [Sym("at"), x, y, 0], effects(hide=True)])
        pin_nodes = [[Sym("pin"), p.number, [Sym("uuid"), self.ids("pin", key or ref, p.number)]]
                     for p in {p.number: p for p in libsym.pins(node) if p.unit in (0, unit)}.values()]
        self.items.append([
            Sym("symbol"), [Sym("lib_id"), node[1]], [Sym("at"), x, y, rot], [Sym("unit"), unit],
            [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), yesno(not ref.startswith("#"))],
            [Sym("on_board"), yesno(not ref.startswith("#"))], [Sym("dnp"), yesno(dnp)],
            [Sym("uuid"), sid], *props, *pin_nodes,
            [Sym("instances"), [Sym("project"), self.sp.project, [Sym("path"), "/" + self.root, [Sym("reference"), ref], [Sym("unit"), unit]]]],
        ])

    # -- connections ---------------------------------------------------------
    def wire(self, a, b) -> None:
        if (a[0], a[1]) == (b[0], b[1]):
            return
        self.items.append([Sym("wire"), [Sym("pts"), [Sym("xy"), a[0], a[1]], [Sym("xy"), b[0], b[1]]],
                           [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]], [Sym("uuid"), self.ids("wire", a, b)]])

    def label(self, net, at, out) -> None:
        angle, just = LABEL[out]
        self.items.append([Sym("label"), net, [Sym("at"), at[0], at[1], angle], [Sym("fields_autoplaced"), Sym("yes")],
                           effects(just), [Sym("uuid"), self.ids("label", net, at)]])

    def global_label(self, net, at, out) -> None:
        angle, just = LABEL[out]
        just = just.replace(" bottom", "")
        self.items.append([Sym("global_label"), net, [Sym("shape"), Sym("passive")], [Sym("at"), at[0], at[1], angle],
                           [Sym("fields_autoplaced"), Sym("yes")], effects(just), [Sym("uuid"), self.ids("glabel", net, at)],
                           [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}", [Sym("at"), at[0], at[1], 0], effects(hide=True)]])

    def power(self, net, at, out) -> None:
        node = libsym.load(f"power:{net}")
        self.pwr_count += 1
        rot = (ROT_GROUND if is_ground(net) else ROT_SUPPLY)[out]
        self.place(node, f"#PWR{self.pwr_count:03d}", net, "", at[0], at[1], rot=rot, hide_ref=True, key=("pwr", net, at))

    def no_connect(self, at) -> None:
        self.items.append([Sym("no_connect"), [Sym("at"), at[0], at[1]], [Sym("uuid"), self.ids("nc", at)]])

    def junction(self, at) -> None:
        self.items.append([Sym("junction"), [Sym("at"), at[0], at[1]], [Sym("diameter"), 0],
                           [Sym("color"), 0, 0, 0, 0], [Sym("uuid"), self.ids("junction", at)]])

    def attach(self, net: str, pin_at, out) -> None:
        self.attach_run(net, [pin_at], out)

    def attach_run(self, net: str, pins_at: list, out) -> None:
        """Stub every pin of a run of neighbouring same-net pins, join the stub ends, and mark the net once."""
        reach = STUB
        ends = [(snap(p[0] + out[0] * reach), snap(p[1] + out[1] * reach)) for p in pins_at]
        for p, e in zip(pins_at, ends):
            self.wire(p, e)
        for a, b in zip(ends, ends[1:]):
            self.wire(a, b)
        for e in ends[1:-1]:
            self.junction(e)
        if net in self.power_lib and out[0] == 0:
            self.power(net, ends[0], out)
        elif net in self.power_lib:
            self.global_label(net, ends[0], out)     # sideways: a label never collides with the part's text
        else:
            self.label(net, ends[0], out)


def footprint_of_unit(units: int, part: specmod.Part, unit: int) -> tuple:
    return libsym.bbox(part.node, unit)


POWER_NAMES: set[str] = set()


def label_room(sp: specmod.Spec, part: specmod.Part, unit: int, pin_net: dict) -> tuple[float, float, float, float]:
    """Extra room each side of a unit for stubs and label text."""
    room = [0.0, 0.0, 0.0, 0.0]  # left, top, right, bottom
    for pin in part.pins:
        if pin.unit not in (0, unit):
            continue
        net = pin_net.get((part.ref, pin.number))
        text = STUB * 2 + (len(net) * 1.0 + 2 if net else 1.5)
        out = pin.outward
        idx = {(-1, 0): 0, (0, -1): 1, (1, 0): 2, (0, 1): 3}[out]
        room[idx] = max(room[idx], text)
        if net and out[0] == 0 and net in POWER_NAMES:
            half = len(net) * 0.65 + 1        # an upright power symbol's text runs sideways
            edge = pin.x
            x0, _, x1, _ = libsym.bbox(part.node, unit)
            room[0] = max(room[0], half - (edge - x0))
            room[2] = max(room[2], half - (x1 - edge))
    return tuple(room)


def build(sp: specmod.Spec, out_dir: Path) -> tuple[Path, Sheet]:
    sheet = Sheet(sp)
    POWER_NAMES.update(sheet.power_lib)
    pin_net = sp.pin_net()

    # units to place, grouped
    groups: dict[str, list] = defaultdict(list)
    for part in sp.parts.values():
        units = libsym.unit_count(part.node)
        for unit in range(1, units + 1):
            if not any(p.unit == unit or (unit == 1 and p.unit == 0) for p in part.pins) and units > 1:
                continue
            groups[part.group].append((part, unit))

    # nets that need a PWR_FLAG: a power_in pin and no power_out pin
    drive: dict[str, set] = defaultdict(set)
    for (ref, number), net in pin_net.items():
        part = sp.parts[ref]
        for pin in part.pins:
            if pin.number == number:
                drive[net].add(pin.etype)
    flag_nets = [n for n, types in drive.items() if "power_in" in types and "power_out" not in types]
    flag_nets = [n for n in flag_nets if n not in (sp.raw.get("no_pwr_flag") or [])]
    flag_nets += [n for n in (sp.raw.get("pwr_flags") or []) if n not in flag_nets]
    sheet.flagged = flag_nets

    # lay out each group as rows inside a frame; frames flow left to right, then wrap
    boxes = []
    for name, members in groups.items():
        cells = []
        for part, unit in members:
            x0, y0, x1, y1 = libsym.bbox(part.node, unit)
            l, t, r, b = label_room(sp, part, unit, pin_net)
            cells.append((part, unit, (x0 - l, y0 - t - 3, x1 + r, y1 + b + 3)))
        boxes.append((name, cells))
    if flag_nets:
        flag_node = libsym.load("power:PWR_FLAG")
        x0, y0, x1, y1 = libsym.bbox(flag_node, 1)
        boxes.append(("power flags", [(None, n, (x0 - 2, y0 - 6, x1 + 10 + len(n) * 1.1, y1 + 6)) for n in flag_nets]))

    def pack_group(cells, target):
        """Shelf-pack one group's cells (tallest first) into rows no wider than `target`."""
        order = sorted(cells, key=lambda c: -(c[2][3] - c[2][1]))
        spots, cx, cy, line_h, used_w = [], 0.0, 0.0, 0.0, 0.0
        for part, unit, (x0, y0, x1, y1) in order:
            w, h = x1 - x0, y1 - y0
            if cx > 0 and cx + w > target:
                cx, cy, line_h = 0.0, cy + line_h + 2.54, 0.0
            spots.append((part, unit, cx - x0, cy - y0))
            cx += w + 2.54
            used_w, line_h = max(used_w, cx), max(line_h, h)
        return spots, used_w + 4, cy + line_h + 9

    def layout(page_w: float, page_h: float):
        placed, frames = [], []
        fx, fy, row_h = 15.0, 15.0, 0.0
        for name, cells in boxes:
            area = sum((c[2][2] - c[2][0] + 2.54) * (c[2][3] - c[2][1] + 2.54) for c in cells)
            widest = max(c[2][2] - c[2][0] for c in cells)
            target = min(page_w - 40, max(widest, (area * 1.6) ** 0.5))
            spots, fw, fh = pack_group(cells, target)
            if fx > 15 and fx + fw > page_w - 15:
                fx, fy, row_h = 15.0, fy + row_h + 5, 0.0
            frames.append((name, fx, fy, fw, fh))
            for part, unit, ox, oy in spots:
                placed.append((part, unit, snap(fx + 2 + ox), snap(fy + 6 + oy)))
            fx += fw + 5
            row_h = max(row_h, fh)
        return placed, frames, fy + row_h

    for paper, pw, ph in PAPERS:
        placed, frames, bottom = layout(pw, ph)
        if bottom <= ph - 40 and all(f[1] + f[3] <= pw - 12 for f in frames):
            break

    for name, fx, fy, fw, fh in frames:
        sheet.items.append([Sym("rectangle"), [Sym("start"), snap(fx), snap(fy)], [Sym("end"), snap(fx + fw), snap(fy + fh)],
                            [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("dash")]], [Sym("fill"), [Sym("type"), Sym("none")]],
                            [Sym("uuid"), sheet.ids("frame", name)]])
        sheet.items.append([Sym("text"), name.upper(), [Sym("exclude_from_sim"), Sym("no")], [Sym("at"), snap(fx + 1.27), snap(fy + 3.81), 0],
                            effects("left bottom"), [Sym("uuid"), sheet.ids("frametext", name)]])

    flag_index = 0
    for part, unit, x, y in placed:
        if part is None:
            net = unit
            flag_index += 1
            sheet.place(flag_node, f"#FLG{flag_index:02d}", "PWR_FLAG", "", x, y, hide_ref=True, key=("flag", net))
            at = (snap(x), snap(y))
            end = (snap(x + 3 * GRID * 2), at[1])
            sheet.wire(at, end)
            if net in sheet.power_lib:
                sheet.power(net, end, (0, 1) if is_ground(net) else (0, -1))
            else:
                sheet.label(net, end, (1, 0))
            continue
        sheet.place(part.node, part.ref, part.value, part.footprint, x, y, unit=unit, fields=part.fields, dnp=part.dnp)
        mine = [p for p in part.pins if p.unit == unit or (p.unit == 0 and unit == 1)]
        visible_at = {(p.x, p.y) for p in mine if not p.hidden}
        sides: dict[tuple, list] = defaultdict(list)
        seen_at = set()
        for pin in mine:
            if pin.hidden and (pin.x, pin.y) in visible_at:
                continue            # stacked under a visible pin: connected by position
            at = (snap(x + pin.x), snap(y - pin.y))
            if at in seen_at:
                continue
            seen_at.add(at)
            key = (part.ref, pin.number)
            net = pin_net.get(key)
            if net is None and key in sp.nc and not pin.hidden:
                sheet.no_connect(at)
            sides[pin.outward].append((at, net))
        for out, members in sides.items():
            along = 1 if out[0] != 0 else 0          # pins on a left/right side line up in y, top/bottom in x
            members.sort(key=lambda m: m[0][along])
            run: list = []
            for at, net in members + [(None, None)]:
                if run and (net != run[-1][1] or at is None or abs(at[along] - run[-1][0][along]) > 2.55
                            or at[1 - along] != run[-1][0][1 - along]):
                    if run[0][1] is not None:
                        sheet.attach_run(run[0][1], [a for a, _ in run], out)
                    run = []
                if at is not None:
                    run.append((at, net))

    title = [Sym("title_block"), [Sym("title"), sp.title], [Sym("rev"), sp.rev]]
    if sp.company:
        title.append([Sym("company"), sp.company])
    doc = [Sym("kicad_sch"), [Sym("version"), 20250114], [Sym("generator"), "pcb-anatomy"], [Sym("generator_version"), "1.0"],
           [Sym("uuid"), sheet.root], [Sym("paper"), paper], title,
           [Sym("lib_symbols"), *sheet.lib_symbols.values()], *sheet.items,
           [Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]], [Sym("embedded_fonts"), Sym("no")]]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sp.project}.kicad_sch"
    sexp.dump(doc, str(path))
    pro = out_dir / f"{sp.project}.kicad_pro"
    if not pro.exists():
        pro.write_text(json.dumps({"meta": {"filename": pro.name, "version": 3}, "sheets": [[sheet.root, "Root"]]}, indent=2))
    return path, sheet


def netlist(sch: Path) -> dict:
    """KiCad's own netlist of the schematic: {"parts": {ref: {...}}, "nets": {name: {(ref, pin)}}}."""
    out = sch.with_suffix(".net")
    kienv.run_cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch)
    node = sexp.load(str(out))
    parts = {}
    for comp in sexp.find_all(sexp.find(node, "components"), "comp"):
        ref = sexp.value(comp, "ref")
        fields = {str(sexp.value(f, "name")): str(f[-1]) for f in sexp.find_all(sexp.find(comp, "fields") or ["fields"], "field")
                  if len(f) > 2 and not isinstance(f[-1], list)}
        parts[ref] = {"value": sexp.value(comp, "value"), "footprint": sexp.value(comp, "footprint", ""),
                      "datasheet": sexp.value(comp, "datasheet", ""), "description": sexp.value(comp, "description", ""),
                      "fields": {k: v for k, v in fields.items() if k not in ("Footprint", "Datasheet", "Description")},
                      "tstamps": [str(t) for t in (sexp.find(comp, "tstamps") or [])[1:]],
                      "sheetpath": sexp.value(sexp.find(comp, "sheetpath"), "tstamps", "/")}
    nets, types = {}, {}
    for net in sexp.find_all(sexp.find(node, "nets"), "net"):
        name = str(sexp.value(net, "name"))
        nets[name] = {(str(sexp.value(n, "ref")), str(sexp.value(n, "pin"))) for n in sexp.find_all(net, "node")}
        for n in sexp.find_all(net, "node"):
            types[(str(sexp.value(n, "ref")), str(sexp.value(n, "pin")))] = str(sexp.value(n, "pintype", ""))
    out.unlink(missing_ok=True)
    return {"parts": parts, "nets": nets, "pintypes": types}


def parity(sp: specmod.Spec, net: dict) -> list[str]:
    """Differences between the spec's connections and KiCad's netlist."""
    problems = []
    by_node = {node: name for name, nodes in net["nets"].items() for node in nodes}
    for name, nodes in sp.nets.items():
        got = {by_node.get(n) for n in nodes}
        if len(got) != 1 or None in got:
            problems.append(f"net {name}: KiCad splits it into {sorted(map(str, got))}")
    for ref in sp.parts:
        if ref not in net["parts"]:
            problems.append(f"{ref} missing from the netlist")
    return problems


def erc(sch: Path) -> dict:
    report = sch.with_name(sch.stem + "-erc.json")
    kienv.run_cli("sch", "erc", "--format", "json", "--severity-all", "-o", report, sch)
    data = json.loads(report.read_text())
    violations = [v for s in data.get("sheets", []) for v in s.get("violations", [])]
    counts = defaultdict(int)
    for v in violations:
        counts[(v["severity"], v["type"])] += 1
    return {"report": str(report), "violations": violations, "counts": dict(counts)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out", help="output folder (default: the spec's folder)")
    ap.add_argument("--no-erc", action="store_true")
    args = ap.parse_args()
    sp = specmod.load(args.spec)
    out_dir = Path(args.out).resolve() if args.out else sp.project_dir
    path, sheet = build(sp, out_dir)
    print(f"wrote {path}")
    for w in sp.warnings:
        print("warning:", w)
    if sheet.flagged:
        print("PWR_FLAG added on (confirm each is fed from off-board or by a part that drives it):", ", ".join(sheet.flagged))
    problems = parity(sp, netlist(path))
    if problems:
        print("NETLIST PARITY FAILED:\n  " + "\n  ".join(problems))
        raise SystemExit(1)
    print("netlist parity: ok (KiCad's netlist matches the spec)")
    if not args.no_erc:
        result = erc(path)
        errors = sum(n for (sev, _), n in result["counts"].items() if sev == "error")
        print(f"ERC: {len(result['violations'])} violations ({errors} errors) -> {result['report']}")
        for (sev, kind), n in sorted(result["counts"].items()):
            print(f"  {sev:8} {kind:32} x{n}")
        if errors:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
