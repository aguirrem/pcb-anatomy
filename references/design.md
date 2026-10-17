# Design workflow

From requirements to fab files. `SKILL.md` gives the steps; this is the detail behind each.

## 1. Requirements

Write these down (in the conversation or a `requirements.md` beside the spec) before choosing parts:

- **Power:** input source and range (USB 5 V, a Li-ion cell 3.0–4.2 V, 24 V industrial), every rail with its peak current, and anything that must sequence.
- **Interfaces:** connectors, buses and their speeds (I²C 400 kHz, USB 2.0 FS/HS, CAN 500 k), anything going off the board.
- **Mechanics:** board outline, mounting holes, connector positions, height limits, enclosure.
- **Manufacturing:** fab and assembler (JLCPCB, PCBWay, in-house), layer count, whether parts must come from the assembler's stock, hand soldering or not (no 0201, no QFN without a stencil).
- **Already decided:** parts the user has or wants, existing firmware pin maps.

Ask about anything that changes the design and is not stated. Defaults you choose (layer count, passive size) go in your report.

## 2. Parts

```bash
python3 scripts/kicad/find_part.py sym "buck|step-down" --limit 60
python3 scripts/kicad/find_part.py pins "Regulator_Switching:TPS62160DGK"
python3 scripts/kicad/find_part.py fp "MSOP-8.*3x3"
python3 scripts/kicad/find_part.py pads "Package_SO:MSOP-8_3x3mm_P0.65mm"
```

- Prefer parts already in KiCad's libraries, with the symbol's own `Footprint` field or one matching its `ki_fp_filters` (`pins` prints both).
- Read each IC's datasheet reference design and copy its passives (values, voltage ratings, dielectric) into the spec. Note datasheet layout rules you must honour at placement.
- Passive sizes: 0603 by default (hand-solderable, cheap); 0402 when space demands it; 0805/1206 for bulk capacitance and power resistors.
- Check stock and lifecycle with the user's distributor when assembly is involved; put `MPN` (and `LCSC` for JLCPCB) in each part's `fields`.
- Missing parts: `partgen.py` (`references/parts.md`).

## 3. Schematic

`python3 scripts/kicad/sch_build.py spec.yaml` places symbols by `group` in framed blocks on the smallest page that fits, gives every pin a short stub to a net label or power symbol, joins neighbouring pins on the same net into one run, and marks no-connects. Because connectivity is by label, the drawing never has a wire crossing the wrong pin; the netlist parity check proves KiCad's connectivity equals the spec's.

Done means: `netlist parity: ok`, `ERC: 0 errors`, the PWR_FLAG list read and accepted, and the rendered `renders/sch.png` looked at (frames readable, no overlapping text). Warnings: read each. ERC warnings about libraries mean KiCad's global library tables are missing; run `setup.sh`.

The schematic is a generated view. To change the design, change the spec and rebuild; symbol identities (UUIDs) are stable across rebuilds, so the board stays linked.

## 4. Placement

`python3 scripts/kicad/pcb_build.py spec.yaml` builds the board from the schematic's netlist: outline, holes (board-only, out of the BOM), design rules and net classes from `rules`, every footprint with its nets and schematic link, pours, then placement and a silkscreen tidy (references resized to 0.8 mm and moved off pads, or moved to the fab layer when no room is left, listed). It ends with DRC including schematic parity.

How to place, in order:

1. **Mechanical first:** connectors on their edges (the plug side over or flush with the edge), mounting holes, switches and LEDs where the enclosure needs them.
2. **Power path:** input connector → protection → regulator → its output capacitors → loads. Short and wide; switching regulators per the datasheet's layout (input cap, switch node and diode or low-side switch in the tightest loop).
3. **The main IC** where its signals fan out most directly to their connectors.
4. **Critical small parts** with `near:` (decoupling at each supply pin, crystal and load caps at the oscillator pins, ESD diodes right at the connector).
5. **The rest:** let auto-placement fill in, render, and move what looks wrong.

Render with `render.py PROJECT --only top` after every change and look: are decoupling caps on the pins they serve, do signals flow without crossing, is there room to route between fine-pitch pins? Rebuilding is fast; iterate.

## 5. Routing

`python3 scripts/kicad/route.py PROJECT/name.kicad_pcb` exports Specctra DSN, runs Freerouting headless (net classes give track widths and clearances), imports the result, refills pours, adds ground stitching vias (a grid, plus one inside any pour island that would otherwise float), tidies the silkscreen and runs DRC. The board is backed up to `.kicad_pcb.bak` first.

- Ground is routed too by default, so nothing depends on a pour reaching a pad. `--pour-ground` leaves ground to the pours for a cleaner plane; check the unrouted count after.
- Route critical nets by hand in KiCad first, then `--keep-tracks`: differential pairs (USB D+/D−: 90 Ω, matched within ~1 mm for high speed), crystal traces (short, guarded by ground), switching-regulator loops, RF (controlled impedance, no autorouter).
- Done means 0 unrouted and 0 layout errors. Look at `renders/layers-*.png`: autorouters make detours a person would not; reroute the ugly ones by hand or move parts and route again.
- Four or more layers: give inner layers to ground and power planes (`pours` with `layers: [In1.Cu]`), route signals on the outer layers.

**Track width for current** (outer layer, 1 oz copper, 10 °C rise; halve the current for inner layers):

| Width | Current |
|---|---|
| 0.2 mm | ~0.75 A |
| 0.3 mm | ~1.0 A |
| 0.5 mm | ~1.5 A |
| 1.0 mm | ~2.5 A |
| 2.0 mm | ~4.0 A |

## 6. Outputs

`python3 scripts/kicad/outputs.py PROJECT --fab jlcpcb [--step]` writes `fab/`: Gerbers for copper, mask, paste, silkscreen and outline, drill files and maps, a zip of them, a grouped BOM, positions for both sides relative to the board's bottom-left corner, IPC-2581, JLCPCB-format BOM and CPL, and optionally STEP. It refuses a board with DRC errors or unrouted nets unless forced. Tell the user to check the fab's own Gerber viewer and, for JLCPCB assembly, the part rotations in their preview.

## Hand edits

The user will often finish a layout in KiCad. Rebuilding from the spec would discard their work unless they use the keep flags: `pcb_build.py --keep-placement` (keeps positions of parts not in `placement`) and `route.py --keep-tracks`. Before any rebuild of a board the user has touched, say what would be lost and ask.
