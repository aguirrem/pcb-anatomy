#!/usr/bin/env python3
"""Generate fabrication and assembly outputs for a KiCad project.

    python3 scripts/kicad/outputs.py PROJECT_DIR [--fab jlcpcb|generic] [--out DIR] [--step] [--force]

Writes into PROJECT/fab/:
  gerbers/          every copper, mask, paste, silkscreen and edge layer, plus drill files and drill map
  <project>-gerbers.zip   the folder above, zipped for upload
  <project>-bom.csv       grouped BOM (Reference, Value, Footprint, Qty, DNP, MPN, LCSC)
  <project>-pos.csv       pick-and-place positions for both sides
  <project>-ipc2581.xml   one-file fab package many fabs accept (KiCad 8+)
  <project>.step          board and parts as a 3D model (with --step; slow on big boards)
With --fab jlcpcb the BOM and positions also come out in JLCPCB's column names
(<project>-bom-jlc.csv, <project>-cpl-jlc.csv).

It refuses to run while the board has DRC errors or unrouted connections,
unless you pass --force: fab files from a failing board are a liability.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

import kienv
import pcb_build


def find(project: Path, suffix: str) -> Path:
    if project.is_file():
        return project.with_suffix(suffix)
    hits = sorted(project.glob(f"*{suffix}"))
    if not hits:
        sys.exit(f"no {suffix} file in {project}")
    return hits[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--fab", default="generic", choices=["generic", "jlcpcb"])
    ap.add_argument("--out")
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    pcb, sch = find(project, ".kicad_pcb"), find(project, ".kicad_sch")
    out = Path(args.out).resolve() if args.out else pcb.parent / "fab"
    name = pcb.stem

    result = pcb_build.drc(pcb, parity=sch.exists())
    errors = pcb_build.print_drc(result)
    if errors and not args.force:
        sys.exit("not generating fab outputs from a board with DRC errors or unrouted connections (use --force to override)")

    if out.exists():
        shutil.rmtree(out)
    gerbers = out / "gerbers"
    gerbers.mkdir(parents=True)
    pcbnew = kienv.pcbnew()
    board = pcbnew.LoadBoard(str(pcb))
    copper = [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    layers = copper + ["F.Mask", "B.Mask", "F.Paste", "B.Paste", "F.Silkscreen", "B.Silkscreen", "Edge.Cuts"]
    origin = ["--use-drill-file-origin"] if board.GetDesignSettings().GetAuxOrigin() != pcbnew.VECTOR2I(0, 0) else []
    kienv.run_cli("pcb", "export", "gerbers", "--check-zones", "--subtract-soldermask", "--layers", ",".join(layers),
                  *origin, "-o", gerbers, pcb)
    kienv.run_cli("pcb", "export", "drill", "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2",
                  "--excellon-units", "mm", "--drill-origin", "plot" if origin else "absolute", "-o", str(gerbers) + "/", pcb)
    archive = shutil.make_archive(str(out / f"{name}-gerbers"), "zip", gerbers)
    print("wrote", archive)

    pos = out / f"{name}-pos.csv"
    kienv.run_cli("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "--exclude-dnp", *origin, "-o", pos, pcb)
    print("wrote", pos)

    if sch.exists():
        bom = out / f"{name}-bom.csv"
        kienv.run_cli("sch", "export", "bom", "--fields", "Reference,Value,Footprint,${QUANTITY},${DNP},MPN,LCSC",
                      "--labels", "Reference,Value,Footprint,Qty,DNP,MPN,LCSC", "--group-by", "Value,Footprint,MPN,LCSC",
                      "--ref-range-delimiter", "", "-o", bom, sch)
        print("wrote", bom)
        if args.fab == "jlcpcb":
            rows = list(csv.DictReader(bom.open()))
            jlc = out / f"{name}-bom-jlc.csv"
            with jlc.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
                for r in rows:
                    if r.get("DNP", "").strip():
                        continue
                    w.writerow([r["Value"], r["Reference"], r["Footprint"].split(":")[-1], r.get("LCSC", "")])
            print("wrote", jlc)
            missing = [r["Reference"] for r in rows if not r.get("LCSC") and not r.get("DNP", "").strip()]
            if missing:
                print("note: no LCSC part number (add an LCSC field in the spec) for:", ", ".join(missing))

    if args.fab == "jlcpcb":
        cpl = out / f"{name}-cpl-jlc.csv"
        with pos.open() as src, cpl.open("w", newline="") as dst:
            w = csv.writer(dst)
            w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
            for r in csv.DictReader(src):
                w.writerow([r["Ref"], f'{float(r["PosX"]):.4f}mm', f'{float(r["PosY"]):.4f}mm',
                            "Top" if r["Side"].lower().startswith("top") else "Bottom", r["Rot"]])
        print("wrote", cpl, "(check rotations against JLCPCB's preview: their part orientations differ by package)")

    ipc = out / f"{name}-ipc2581.xml"
    if kienv.run_cli("pcb", "export", "ipc2581", "-o", ipc, pcb, check=False).returncode == 0 and ipc.exists():
        print("wrote", ipc)
    if args.step:
        step = out / f"{name}.step"
        kienv.run_cli("pcb", "export", "step", "--subst-models", "-o", step, pcb, check=False)
        if step.exists():
            print("wrote", step)
    layers = sorted(p.name for p in gerbers.iterdir())
    print(f"{len(layers)} files in {gerbers}")


if __name__ == "__main__":
    main()
