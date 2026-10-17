#!/usr/bin/env python3
"""Autoroute a KiCad board with Freerouting, then refill pours and run DRC.

    python3 scripts/kicad/route.py board.kicad_pcb [--passes 30] [--keep-tracks] [--timeout 600]

Steps: export the board to Specctra DSN (KiCad's own exporter), run
Freerouting headless, import the session (.ses) back, refill every zone,
tidy the silkscreen, save, and run DRC with schematic parity. The board is
backed up to <name>.kicad_pcb.bak first.

Freerouting routes what it can, not what a designer would: always look at
renders/layers-*.png afterwards. Route anything critical first by hand in
KiCad (USB pairs, crystals, switching regulator loops, RF) and run this with
--keep-tracks so only the rest is autorouted. Ground is left to the pours.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import kienv
import pcb_build
import silk
import stitch

pcbnew = kienv.pcbnew()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcb")
    ap.add_argument("--passes", type=int, default=30, help="maximum autorouter passes")
    ap.add_argument("--keep-tracks", action="store_true", help="keep existing tracks and vias (route only the rest)")
    ap.add_argument("--pour-ground", action="store_true",
                    help="leave ground nets to the pours instead of routing them (cleaner planes; can strand pads)")
    ap.add_argument("--timeout", type=int, default=600, help="seconds before giving up on Freerouting")
    ap.add_argument("--stitch", type=float, default=5.0, help="ground stitching via pitch in mm")
    ap.add_argument("--no-parity", action="store_true", help="skip schematic parity (board without a schematic)")
    args = ap.parse_args()

    pcb = Path(args.pcb).resolve()
    java, jar = kienv.java(), kienv.freerouting_jar()
    if not jar or not java:
        sys.exit("Freerouting is not installed: run scripts/kicad/setup.sh (it fetches the jar and Java 25).")
    shutil.copy2(pcb, pcb.with_suffix(".kicad_pcb.bak"))

    board = pcbnew.LoadBoard(str(pcb))
    if not args.keep_tracks:
        for track in list(board.GetTracks()):
            board.Delete(track)             # Delete, not Remove: Remove leaves SWIG proxies dangling
    grounds = {p.GetNetname() for fp in board.GetFootprints() for p in fp.Pads() if "GND" in p.GetNetname().upper()}
    if args.pour_ground and any(z.GetNetname() in grounds for z in board.Zones()):
        # Freerouting would route ground as tracks; pours connect it instead. Pads stay on their net.
        print("ground nets left to the pours:", ", ".join(sorted(map(str, grounds))))
    for zone in board.Zones():
        zone.UnFill()
    work = pcb.parent / ".route"
    work.mkdir(exist_ok=True)
    dsn, ses = work / f"{pcb.stem}.dsn", work / f"{pcb.stem}.ses"
    ses.unlink(missing_ok=True)
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        sys.exit("KiCad could not export the board to Specctra DSN")
    if args.pour_ground and grounds and any(z.GetNetname() in grounds for z in board.Zones()):
        text = dsn.read_text()
        for g in grounds:
            name = re.escape(str(g))
            text = re.sub(rf'\n\s*\(net "?{name}"?\n\s*\(pins[^)]*\)\n\s*\)', "", text)
        dsn.write_text(text)

    cmd = [java, "-jar", jar, "-de", str(dsn), "-do", str(ses), "-mp", str(args.passes),
           "--gui.enabled=false", "--api_server.enabled=false"]
    print("running Freerouting ...", flush=True)
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    log = (proc.stdout + proc.stderr).splitlines()
    (work / "freerouting.log").write_text("\n".join(log))
    if not ses.exists():
        tail = "\n".join(l for l in log[-15:] if "JAVA_TOOL" not in l)
        sys.exit(f"Freerouting produced no session file (exit {proc.returncode}). Last lines:\n{tail}")
    summary = [l for l in log if "unrouted" in l.lower() or "completed" in l.lower() or "auto-routing" in l.lower()]
    print(f"Freerouting finished in {time.time() - start:.0f} s" + (f": {summary[-1].split('INFO')[-1].strip()}" if summary else ""))

    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        sys.exit("KiCad could not import the routed session")
    for g in sorted(map(str, grounds)):
        n = stitch.stitch(board, g, args.stitch)
        if n:
            print(f"added {n} stitching vias on {g}")
    off = silk.tidy(board)
    if off:
        print("references on the fab layer (no room on silkscreen):", ", ".join(off))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(pcb), board)
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    length = sum(pcbnew.ToMM(t.GetLength()) for t in tracks)
    print(f"wrote {pcb}: {len(tracks)} track segments ({length:.0f} mm), {len(vias)} vias")
    errors = pcb_build.print_drc(pcb_build.drc(pcb, parity=not args.no_parity))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
