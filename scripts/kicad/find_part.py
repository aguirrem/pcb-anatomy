#!/usr/bin/env python3
"""Search KiCad's symbol and footprint libraries, or show a symbol's pins.

    python3 scripts/kicad/find_part.py sym  "ATtiny85"          # symbols by name, description or keywords
    python3 scripts/kicad/find_part.py fp   "SOIC-8_3.9x4.9"     # footprints by id
    python3 scripts/kicad/find_part.py pins "MCU_Microchip_ATtiny:ATtiny85-20S"
    python3 scripts/kicad/find_part.py pads "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"

Add --project DIR to include a project's own lib/ folder.
"""

import argparse
from pathlib import Path

import fplib
import libsym
import sexp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["sym", "fp", "pins", "pads"])
    ap.add_argument("query")
    ap.add_argument("--project")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    project = Path(args.project) if args.project else None
    if args.kind == "sym":
        print("\n".join(libsym.search(args.query, project, args.limit)) or "no match")
    elif args.kind == "fp":
        print("\n".join(fplib.search(args.query, project, args.limit)) or "no match")
    elif args.kind == "pins":
        node = libsym.load(args.query, project)
        fp = sexp.prop(node, "Footprint", "")
        filters = sexp.prop(node, "ki_fp_filters", "")
        print(f"{args.query}  units={libsym.unit_count(node)}  default footprint={fp or '-'}  filters={filters or '-'}")
        for p in sorted(libsym.pins(node), key=lambda p: (p.unit, len(p.number), p.number)):
            print(f"  unit {p.unit}  pin {p.number:>4}  {p.name:<16} {p.etype:<14}{' hidden' if p.hidden else ''}")
    else:
        print(" ".join(fplib.pad_numbers(args.query, project)))


if __name__ == "__main__":
    main()
