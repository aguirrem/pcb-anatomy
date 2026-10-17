# The circuit spec

One YAML (or JSON) file per project. `sch_build.py` turns it into the schematic, `pcb_build.py` turns the schematic's netlist into the board, and both check KiCad's view against it. Put it in the project folder: every output lands next to it, named after `project`.

`examples/usb-blinky/spec.yaml` is a complete, working spec. Start from it.

## Top level

| Key | Required | Meaning |
|---|---|---|
| `project` | yes | file stem for `.kicad_sch`, `.kicad_pcb`, `.kicad_pro` |
| `title`, `rev`, `company` | no | the schematic title block |
| `parts` | yes | the components, keyed by reference designator |
| `nets` | yes | the connections, keyed by net name |
| `nc` | no | pins deliberately left unconnected (get a no-connect flag) |
| `auto_nc` | no | `true` marks every pin not in a net as no-connect, with a warning per pin. Off by default: an unlisted pin is an error, because a forgotten pin is the commonest schematic bug |
| `allow_single` | no | net names allowed to have one pin (a test point, an antenna feed) |
| `pwr_flags`, `no_pwr_flag` | no | add or suppress PWR_FLAGs by net (see below) |
| `board` | for the board | size, shape, layers, holes |
| `rules` | for the board | fab preset, track widths, clearances, vias, net classes |
| `pours` | no | copper pours |
| `placement` | for the board | positions for the parts you place by intent |

## parts

```yaml
parts:
  U1: {symbol: "Regulator_Linear:AP2112K-3.3", footprint: "Package_TO_SOT_SMD:SOT-23-5", value: AP2112K-3.3,
       group: power, fields: {MPN: AP2112K-3.3TRG1, LCSC: C51118}}
  C2: {symbol: "Device:C", footprint: "Capacitor_SMD:C_0603_1608Metric", value: 1u, group: power, near: U1.VOUT}
```

| Key | Meaning |
|---|---|
| `symbol` | `Library:Name` from KiCad's libraries or the project's `lib/` (`find_part.py sym <regex>`) |
| `footprint` | `Library:Name` (`find_part.py fp <regex>`); checked to exist when the spec loads |
| `value` | the value field; defaults to the symbol name |
| `group` | schematic frame the part is drawn in (power, mcu, usb, ...). Group by function: it is what makes the schematic readable |
| `fields` | extra fields copied to the schematic and the board. `MPN` and `LCSC` feed the BOM and the review's BOM check |
| `dnp` | `true` for do-not-populate |
| `near` | `REF.PIN`: auto-placement puts this part next to that pin. Use it for every capacitor whose distance matters (regulator input and output caps, crystal load caps) |

Multi-unit symbols (dual op-amps, quad gates) place every unit; pins are referenced by number across units.

## nets

```yaml
nets:
  +3V3: [U1.VOUT, C2.1, U2.VCC, J2.VCC]
  GND: [U1.GND, C2.2, U2.GND, J1.GND, J1.SHIELD]
  RESET: [U2.1, R3.2, J2.5]
```

A member is `REF.PIN`, where PIN is:

- a pin **number** (`U2.1`, `J1.A4`), always unambiguous;
- a pin **name** (`U2.VCC`); a name shared by several pins selects all of them, so `J1.GND` connects every ground pin of a USB-C receptacle;
- one **alternate function** of a pin named with slashes (`U2.PB0` matches `AREF/PB0`), if exactly one pin has it.

Use pin numbers where a name could be ambiguous. Net names become labels: power nets that KiCad's `power` library knows (`GND`, `+3V3`, `+5V`, `VBUS`, `VCC`, `+12V` ...) get power symbols; others get net labels, and local nets appear in KiCad as `/NAME`.

Checks when the spec loads: unknown parts or pins, a pin on two nets, a net with one pin (unless in `allow_single`), and every pin neither in a net nor in `nc` (unless the symbol already marks it no-connect, or `auto_nc` is on).

## PWR_FLAG

KiCad's ERC requires every power-input pin to be driven. A net fed from a connector (USB VBUS) or by a part whose output pin is typed passive has no driver, so `sch_build.py` adds a PWR_FLAG to every net that has a `power_in` pin and no `power_out` pin, and prints the list. Read it: a flag on a net that is in fact undriven (an LDO left out) hides a real error. `pwr_flags: [NET]` adds one, `no_pwr_flag: [NET]` suppresses one.

## board

```yaml
board:
  size: [32, 24]          # mm, width and height
  corner: 2               # corner radius, mm
  layers: 2               # 2, 4, 6 ...
  thickness: 1.6
  holes: {inset: 2.6, footprint: "MountingHole:MountingHole_2.2mm_M2"}   # four corner holes; or `at: [[x, y], ...]`
  edge_clearance: 0.5     # keep auto-placed parts this far from the edge
  spacing: 0.6            # courtyard-to-courtyard gap the auto-placer keeps
```

Coordinates everywhere are millimetres from the board's top-left corner, x right, y down, as KiCad shows them.

## rules

```yaml
rules:
  fab: jlcpcb             # jlcpcb (default), pcbway, oshpark or generic: the minimums DRC enforces
  track: 0.25             # default track width
  clearance: 0.2          # default clearance
  via: [0.6, 0.3]         # default via pad diameter and drill
  netclasses:
    Power: {nets: [VBUS, +3V3, GND], track: 0.5, clearance: 0.2}
    USB: {nets: [USB_DP, USB_DN], track: 0.2, diff_pair: [0.2, 0.15]}
```

The fab preset sets the board's minimum rules (track, clearance, via, drill, annular ring, copper-to-edge, hole clearance); the defaults and net classes set what routing actually uses. Widths for current: `review.py --currents` checks them, and `references/design.md` has a table.

## pours

```yaml
pours:
  - {net: GND, layers: [F.Cu, B.Cu]}     # optional: inset (mm from the edge), clearance
```

Pours connect SMD pads solid and through-hole pads with thermal spokes, and drop islands that connect to nothing. `route.py` stitches the layers together with vias.

## placement

```yaml
placement:
  J1: {at: [16, 3.8], rot: 180}          # rot in degrees, KiCad's sense (counter-clockwise)
  U2: {at: [17, 14]}
  R9: {at: [6, 9], side: bottom}
```

Place every **anchor** here: connectors (on the edge, facing out), ICs (by signal flow), regulators (next to their input), crystals (at their pins), anything hot, noisy or mechanical. Leave passives out; they are placed next to what they connect to, capacitors at IC supply pins. A placement that collides with another part, a hole or the edge margin prints a note: an edge connector crossing the margin is expected, anything else is a mistake to fix. `pcb_build.py --keep-placement` keeps positions you moved by hand in KiCad for parts not listed here.
