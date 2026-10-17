#!/usr/bin/env python3
"""Make a symbol and a footprint for a part that is not in KiCad's libraries.

    python3 scripts/kicad/partgen.py part.yaml --project PROJECT_DIR

The part file gives the datasheet's numbers; see references/parts.md:

    name: TPS62840DLC
    lib: myparts                  # the project library both go into
    reference: U
    value: TPS62840
    description: 750 mA step-down converter
    datasheet: https://www.ti.com/lit/gpn/tps62840
    footprint:
      family: nolead              # chip | gullwing | nolead | header | pads
      pitch: 0.5
      pins: [3, 3]                # per side: 2 sides = [left, right], 4 sides = [left, bottom, right, top]
      body: [1.5, 2.0]            # x, y in mm (gullwing: the plastic body)
      lead: [0.35, 0.25]          # terminal length, width
      ep: [0.6, 1.2]              # exposed pad x, y (optional, becomes the last pad number + 1, or `ep_number`)
      height: 0.6
    symbol:
      pins:                       # number, name, electrical type, side of the symbol
        - [1, VIN, power_in, left]
        - [2, GND, power_in, bottom]

Writes PROJECT/lib/<lib>.kicad_sym and PROJECT/lib/<lib>.pretty/<name>.kicad_mod,
registers both in the project's sym-lib-table and fp-lib-table, renders
both to PROJECT/lib/previews/<name>-{symbol,footprint}.png, and prints the
land-pattern numbers so they can be checked against the datasheet's own
recommended layout, which always wins over these defaults.

Land patterns follow IPC-7351B nominal fillets (toe, heel, side):
gull-wing 0.35/0.35/0.03, no-lead (QFN/DFN) 0.40/0.00/-0.04 (pads never
narrower than the terminal), chips 0.35/0.00/0.00. Pads are trimmed to
keep at least 0.2 mm between neighbours for a solder-mask web.
"""

from __future__ import annotations

import argparse
import math
import sys
import uuid as uuidlib
from pathlib import Path

import kienv
import sexp
from sexp import Sym

WEB = 0.2          # minimum copper gap between pads (solder-mask web)
COURTYARD = 0.25
SILK = 0.12


def r2(v: float) -> float:
    return round(v + 0.0, 3)


def font(size=1.0, thick=0.15):
    return [Sym("effects"), [Sym("font"), [Sym("size"), size, size], [Sym("thickness"), thick]]]


def line(a, b, layer, width):
    return [Sym("fp_line"), [Sym("start"), r2(a[0]), r2(a[1])], [Sym("end"), r2(b[0]), r2(b[1])],
            [Sym("stroke"), [Sym("width"), width], [Sym("type"), Sym("solid")]], [Sym("layer"), layer]]


def rect(a, b, layer, width):
    return [Sym("fp_rect"), [Sym("start"), r2(a[0]), r2(a[1])], [Sym("end"), r2(b[0]), r2(b[1])],
            [Sym("stroke"), [Sym("width"), width], [Sym("type"), Sym("solid")]], [Sym("fill"), Sym("no")], [Sym("layer"), layer]]


def smd(number, x, y, w, h, shape="roundrect"):
    node = [Sym("pad"), str(number), Sym("smd"), Sym(shape), [Sym("at"), r2(x), r2(y)], [Sym("size"), r2(w), r2(h)],
            [Sym("layers"), "F.Cu", "F.Mask", "F.Paste"]]
    if shape == "roundrect":
        node.append([Sym("roundrect_rratio"), 0.25])
    return node


def tht(number, x, y, size, drill, square=False):
    return [Sym("pad"), str(number), Sym("thru_hole"), Sym("rect" if square else "circle"), [Sym("at"), r2(x), r2(y)],
            [Sym("size"), size, size], [Sym("drill"), drill], [Sym("layers"), "*.Cu", "*.Mask"], [Sym("remove_unused_layers"), Sym("no")]]


def periphery(fp: dict, toe: float, heel: float, side: float, span_key: str):
    """Pads around a body: (number, x, y, w, h) with pin 1 at top left, counting anticlockwise."""
    pitch = float(fp["pitch"])
    counts = list(fp["pins"])
    bx, by = (float(v) for v in fp["body"])
    lead_len, lead_w = (float(v) for v in fp["lead"])
    if len(counts) not in (2, 4):
        sys.exit("footprint.pins must list 2 sides [left, right] or 4 sides [left, bottom, right, top]")
    spans = fp.get("span")
    spans = [float(spans)] * 2 if isinstance(spans, (int, float)) else [float(v) for v in spans] if spans else [bx, by]
    out = []
    n = 1
    sides = ["left", "right"] if len(counts) == 2 else ["left", "bottom", "right", "top"]
    for count, where in zip(counts, sides):
        horizontal = where in ("left", "right")
        tip = spans[0] if horizontal else spans[1]        # lead tip to lead tip across this pair of sides
        outer = tip + 2 * toe
        inner = tip - 2 * lead_len - 2 * heel
        length = (outer - inner) / 2
        centre = (outer + inner) / 4
        width = max(lead_w + 2 * side, lead_w) if side < 0 and fp.get("family") == "nolead" else lead_w + 2 * side
        width = min(width, pitch - WEB)
        first = -(count - 1) * pitch / 2
        for i in range(count):
            t = first + i * pitch
            if where == "left":
                out.append((n, -centre, t, length, width))
            elif where == "bottom":
                out.append((n, t, centre, width, length))
            elif where == "right":
                out.append((n, centre, -t, length, width))
            else:
                out.append((n, -t, -centre, width, length))
            n += 1
    return out, n


def build_footprint(part: dict) -> tuple[list, dict]:
    fp = part["footprint"]
    family = fp["family"]
    name = part["name"]
    pads, notes = [], {}
    attr = "smd"
    if family == "gullwing":
        rows, nxt = periphery(fp, 0.35, 0.35, 0.03 if float(fp["pitch"]) > 0.625 else 0.01, "span")
        pads = [smd(*r) for r in rows]
        notes["pads"] = rows
    elif family == "nolead":
        rows, nxt = periphery(fp, 0.40, 0.0, -0.04, "body")
        pads = [smd(*r) for r in rows]
        notes["pads"] = rows
        if fp.get("ep"):
            ex, ey = (float(v) for v in fp["ep"])
            pads.append(smd(fp.get("ep_number", nxt), 0, 0, ex, ey, "rect"))
            notes["ep"] = (ex, ey)
            inner_left = min(abs(r[1]) - r[3] / 2 for r in rows if abs(r[2]) < 1e-9 or abs(r[1]) > abs(r[2]))
            if inner_left - ex / 2 < WEB:
                print(f"warning: exposed pad leaves {inner_left - ex / 2:.2f} mm to the terminal pads (want {WEB} mm or more)")
    elif family == "chip":
        length, width = (float(v) for v in fp["body"])
        term = float(fp["lead"][0])
        outer, inner = length + 2 * 0.35, max(length - 2 * term, 0.2)
        plen, pw = (outer - inner) / 2, width
        c = (outer + inner) / 4
        rows = [(1, -c, 0, plen, pw), (2, c, 0, plen, pw)]
        pads = [smd(*r) for r in rows]
        notes["pads"] = rows
    elif family == "header":
        cols, rws = int(fp.get("cols", 1)), int(fp["rows"])
        pitch, drill = float(fp.get("pitch", 2.54)), float(fp.get("drill", 1.0))
        size = float(fp.get("pad", drill + 0.7))
        n = 1
        for r in range(rws):
            for c in range(cols):
                pads.append(tht(n, c * pitch, r * pitch, size, drill, n == 1))
                n += 1
        attr = "through_hole"
        notes["pads"] = [(i + 1, 0, 0, size, size) for i in range(n - 1)]
    elif family == "pads":
        for p in fp["pads"]:
            if p.get("drill"):
                pads.append(tht(p["number"], p["at"][0], p["at"][1], p["size"][0], p["drill"], p.get("square", False)))
                attr = "through_hole"
            else:
                pads.append(smd(p["number"], p["at"][0], p["at"][1], p["size"][0], p["size"][1], p.get("shape", "roundrect")))
        notes["pads"] = [(p["number"], *p["at"], *p["size"]) for p in fp["pads"]]
    else:
        sys.exit(f"unknown footprint family {family!r}")

    # extents of copper and body
    xs, ys = [], []
    for p in pads:
        at, size = sexp.find(p, "at"), sexp.find(p, "size")
        xs += [at[1] - size[1] / 2, at[1] + size[1] / 2]
        ys += [at[2] - size[2] / 2, at[2] + size[2] / 2]
    body = fp.get("body", [max(xs) - min(xs), max(ys) - min(ys)])
    if family == "header":
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
    else:
        cx = cy = 0.0
        bx0, by0, bx1, by1 = -float(body[0]) / 2, -float(body[1]) / 2, float(body[0]) / 2, float(body[1]) / 2
    cx0, cy0 = min(xs + [bx0]) - COURTYARD, min(ys + [by0]) - COURTYARD
    cx1, cy1 = max(xs + [bx1]) + COURTYARD, max(ys + [by1]) + COURTYARD
    cx0, cy0, cx1, cy1 = (math.floor(cx0 * 100) / 100, math.floor(cy0 * 100) / 100, math.ceil(cx1 * 100) / 100, math.ceil(cy1 * 100) / 100)

    items = [rect((bx0, by0), (bx1, by1), "F.Fab", 0.1), rect((cx0, cy0), (cx1, cy1), "F.CrtYd", 0.05)]
    # silkscreen: body outline segments that stay clear of every pad
    g = SILK / 2 + 0.2
    pad_boxes = [(x0 - g, y0 - g, x1 + g, y1 + g) for x0, y0, x1, y1 in
                 [(sexp.find(p, "at")[1] - sexp.find(p, "size")[1] / 2, sexp.find(p, "at")[2] - sexp.find(p, "size")[2] / 2,
                   sexp.find(p, "at")[1] + sexp.find(p, "size")[1] / 2, sexp.find(p, "at")[2] + sexp.find(p, "size")[2] / 2) for p in pads]]
    sx0, sy0, sx1, sy1 = bx0 - 0.11, by0 - 0.11, bx1 + 0.11, by1 + 0.11
    for a, b in [((sx0, sy0), (sx1, sy0)), ((sx1, sy0), (sx1, sy1)), ((sx1, sy1), (sx0, sy1)), ((sx0, sy1), (sx0, sy0))]:
        steps = 200
        run = None
        for i in range(steps + 1):
            t = i / steps
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            free = not any(p[0] <= x <= p[2] and p[1] <= y <= p[3] for p in pad_boxes)
            if free and run is None:
                run = (x, y)
            if (not free or i == steps) and run is not None:
                end = (x, y) if free else (a[0] + (b[0] - a[0]) * (i - 1) / steps, a[1] + (b[1] - a[1]) * (i - 1) / steps)
                if math.dist(run, end) > 0.3:
                    items.append(line(run, end, "F.SilkS", SILK))
                run = None
    # pin-1 marker: a small triangle outside the courtyard-facing corner of pad 1
    p1 = sexp.find(pads[0], "at")
    s1 = sexp.find(pads[0], "size")
    mx, my = p1[1] - s1[1] / 2 - 0.35, p1[2] - s1[2] / 2 - 0.1
    if family != "header":
        items.append([Sym("fp_poly"), [Sym("pts"), [Sym("xy"), r2(mx), r2(my)], [Sym("xy"), r2(mx - 0.3), r2(my - 0.3)], [Sym("xy"), r2(mx - 0.3), r2(my + 0.3)]],
                      [Sym("stroke"), [Sym("width"), SILK], [Sym("type"), Sym("solid")]], [Sym("fill"), Sym("yes")], [Sym("layer"), "F.SilkS"]])
    node = [Sym("footprint"), name, [Sym("version"), 20260206], [Sym("generator"), "pcb-anatomy"], [Sym("generator_version"), "1.0"],
            [Sym("layer"), "F.Cu"], [Sym("descr"), part.get("description", name) + (f", {part['datasheet']}" if part.get("datasheet") else "")],
            [Sym("tags"), str(part.get("tags", family))],
            [Sym("property"), "Reference", "REF**", [Sym("at"), r2(cx), r2(cy0 - 0.7), 0], [Sym("layer"), "F.SilkS"], font()],
            [Sym("property"), "Value", name, [Sym("at"), r2(cx), r2(cy1 + 0.7), 0], [Sym("layer"), "F.Fab"], font()],
            [Sym("attr"), Sym(attr)], *items,
            [Sym("fp_text"), Sym("user"), "${REFERENCE}", [Sym("at"), r2(cx), r2(cy), 0], [Sym("layer"), "F.Fab"], font(0.5, 0.08)],
            *pads, [Sym("embedded_fonts"), Sym("no")]]
    notes["courtyard"] = (cx1 - cx0, cy1 - cy0)
    return node, notes


SIDE_ANGLE = {"left": 0, "right": 180, "top": 270, "bottom": 90}


def build_symbol(part: dict, footprint_id: str) -> list:
    name = part["name"]
    pins = [dict(zip(("number", "name", "type", "side"), map(str, p))) if isinstance(p, list) else {k: str(v) for k, v in p.items()}
            for p in part["symbol"]["pins"]]
    sides = {s: [p for p in pins if p.get("side", "left") == s] for s in SIDE_ANGLE}
    longest = max((len(p["name"]) for p in pins), default=3)
    rows = max(len(sides["left"]), len(sides["right"]), 1)
    cols = max(len(sides["top"]), len(sides["bottom"]), 0)
    half_w = max(5.08, math.ceil((longest * 1.27 * 0.9 + 2.54) / 2.54) * 2.54 / 1, (cols + 1) * 2.54 / 2)
    half_w = math.ceil(half_w / 2.54) * 2.54
    vertical = lambda side: (max((len(p["name"]) for p in sides[side]), default=0) * 1.2 + 1.016) if sides[side] else 0
    need_h = (vertical("top") + vertical("bottom") + 2.54) / 2         # top and bottom names must not meet
    half_h = math.ceil(max((rows + 1) * 2.54 / 2, need_h) / 2.54) * 2.54
    length = 2.54
    pin_nodes = []
    for side, group in sides.items():
        n = len(group)
        for i, p in enumerate(group):
            off = (i - (n - 1) / 2) * 2.54
            off = round(off / 1.27) * 1.27 if n % 2 == 0 else round(off / 2.54) * 2.54
            if side == "left":
                at = (-half_w - length, -off)
            elif side == "right":
                at = (half_w + length, -off)
            elif side == "top":
                at = (off, half_h + length)
            else:
                at = (off, -half_h - length)
            at = (round(at[0] / 1.27) * 1.27, round(at[1] / 1.27) * 1.27)
            pin_nodes.append([Sym("pin"), Sym(p.get("type", "passive")), Sym("line"), [Sym("at"), r2(at[0]), r2(at[1]), SIDE_ANGLE[side]],
                              [Sym("length"), length], [Sym("name"), p["name"], [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]],
                              [Sym("number"), p["number"], [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]]])
    prop = lambda k, v, y, hide=False: [Sym("property"), k, v, [Sym("at"), 0, r2(y), 0],
                                        [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]] + ([[Sym("hide"), Sym("yes")]] if hide else [])]
    return [Sym("symbol"), name, [Sym("pin_names"), [Sym("offset"), 1.016]], [Sym("exclude_from_sim"), Sym("no")],
            [Sym("in_bom"), Sym("yes")], [Sym("on_board"), Sym("yes")],
            prop("Reference", part.get("reference", "U"), half_h + 1.27 + (length if sides["top"] else 0)),
            prop("Value", part.get("value", name), -half_h - 1.27 - (length if sides["bottom"] else 0)),
            prop("Footprint", footprint_id, -half_h - 3.81, True), prop("Datasheet", part.get("datasheet", ""), 0, True),
            prop("Description", part.get("description", ""), 0, True),
            [Sym("symbol"), f"{name}_0_1", [Sym("rectangle"), [Sym("start"), -half_w, half_h], [Sym("end"), half_w, -half_h],
                                           [Sym("stroke"), [Sym("width"), 0.254], [Sym("type"), Sym("default")]], [Sym("fill"), [Sym("type"), Sym("background")]]]],
            [Sym("symbol"), f"{name}_1_1", *pin_nodes], [Sym("embedded_fonts"), Sym("no")]]


def register(table: Path, kind: str, lib: str, uri: str) -> None:
    head = "sym_lib_table" if kind == "sym" else "fp_lib_table"
    node = sexp.load(str(table)) if table.exists() else [Sym(head), [Sym("version"), 7]]
    if not any(sexp.value(e, "name") == lib for e in sexp.find_all(node, "lib")):
        node.append([Sym("lib"), [Sym("name"), lib], [Sym("type"), "KiCad"], [Sym("uri"), uri], [Sym("options"), ""], [Sym("descr"), "project parts (pcb-anatomy)"]])
        sexp.dump(node, str(table))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    import yaml
    part = yaml.safe_load(Path(args.part).read_text())
    project = Path(args.project).resolve()
    lib = part.get("lib", "project")
    libdir = project / "lib"
    pretty = libdir / f"{lib}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    fid = f"{lib}:{part['name']}"

    fp_node, notes = build_footprint(part)
    fp_path = pretty / f"{part['name']}.kicad_mod"
    sexp.dump(fp_node, str(fp_path))

    sym_path = libdir / f"{lib}.kicad_sym"
    lib_node = sexp.load(str(sym_path)) if sym_path.exists() else [Sym("kicad_symbol_lib"), [Sym("version"), 20251024],
                                                                    [Sym("generator"), "pcb-anatomy"], [Sym("generator_version"), "1.0"]]
    lib_node = [c for c in lib_node if not (isinstance(c, list) and str(c[0]) == "symbol" and c[1] == part["name"])]
    if "symbol" in part:
        lib_node.append(build_symbol(part, fid))
    sexp.dump(lib_node, str(sym_path))
    register(project / "sym-lib-table", "sym", lib, f"${{KIPRJMOD}}/lib/{lib}.kicad_sym")
    register(project / "fp-lib-table", "fp", lib, f"${{KIPRJMOD}}/lib/{lib}.pretty")

    previews = libdir / "previews"
    previews.mkdir(exist_ok=True)
    from render import rasterize
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kienv.run_cli("fp", "export", "svg", "--footprint", part["name"], "--layers", "F.Cu,F.SilkS,F.CrtYd,F.Fab", "-o", tmp, pretty)
        for svg in Path(tmp).glob("*.svg"):
            rasterize(svg, previews / f"{part['name']}-footprint.png", 900)
    if "symbol" in part:
        with tempfile.TemporaryDirectory() as tmp:
            kienv.run_cli("sym", "export", "svg", "--symbol", part["name"], "-o", tmp, sym_path)
            for svg in Path(tmp).glob("*.svg"):
                rasterize(svg, previews / f"{part['name']}-symbol.png", 900)
                break

    print(f"footprint {fid} -> {fp_path}")
    for row in notes.get("pads", [])[:4]:
        print(f"  pad {row[0]}: at ({row[1]:.3f}, {row[2]:.3f}) size {row[3]:.3f} x {row[4]:.3f} mm")
    if len(notes.get("pads", [])) > 4:
        print(f"  ... {len(notes['pads'])} pads in all")
    if "ep" in notes:
        print(f"  exposed pad {notes['ep'][0]} x {notes['ep'][1]} mm")
    print(f"  courtyard {notes['courtyard'][0]:.2f} x {notes['courtyard'][1]:.2f} mm")
    if "symbol" in part:
        print(f"symbol {lib}:{part['name']} -> {sym_path}")
    print(f"previews in {previews}: compare the footprint with the datasheet's recommended land pattern before using it")


if __name__ == "__main__":
    main()
