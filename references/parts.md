# Custom symbols and footprints

`python3 scripts/kicad/partgen.py part.yaml --project PROJECT` makes both from the datasheet's numbers, puts them in the project's own library (`lib/<lib>.kicad_sym`, `lib/<lib>.pretty/`), registers them in the project's `sym-lib-table` and `fp-lib-table`, and renders previews to `lib/previews/`. Reference them in the spec as `<lib>:<name>`.

**Always compare the footprint preview against the datasheet's recommended land pattern.** When the datasheet gives one, it wins: use `family: pads` and type its pads in exactly.

## Part file

```yaml
name: TPS62840DLC               # symbol and footprint name
lib: myparts                    # project library for both
reference: U
value: TPS62840
description: 750 mA step-down converter, 1.5x2 mm DFN-6
datasheet: https://www.ti.com/lit/gpn/tps62840
footprint: {...}                # below
symbol:
  pins:                         # [number, name, electrical type, side]
    - [1, VIN, power_in, left]
    - [2, EN, input, left]
    - [3, GND, power_in, bottom]
    - [4, SW, output, right]
    - [5, VOUT, input, right]
    - [6, MODE, input, left]
```

Electrical types are KiCad's: `input`, `output`, `bidirectional`, `tri_state`, `passive`, `power_in`, `power_out`, `open_collector`, `open_emitter`, `no_connect`, `unspecified`. Get them right: ERC relies on them (`power_out` on regulator outputs, `power_in` on supply pins). Sides: inputs and power on the left, outputs on the right, supplies on top, ground on the bottom. The body is sized so names never collide.

## Footprint families

| family | for | keys |
|---|---|---|
| `gullwing` | SOIC, SSOP, TSSOP, MSOP, QFP, SOT-23 | `pitch`, `pins` per side (`[left, right]` or `[left, bottom, right, top]`), `body` [x, y], `span` (lead tip to lead tip, use the maximum), `lead` [foot length, width] (maximums) |
| `nolead` | QFN, DFN, SON | `pitch`, `pins` per side, `body` [x, y], `lead` [terminal length, width], `ep` [x, y] exposed pad, `ep_number` |
| `chip` | two-terminal chips | `body` [length, width], `lead` [terminal length] |
| `header` | pin headers and through-hole grids | `rows`, `cols`, `pitch`, `drill`, `pad` |
| `pads` | anything else, or a datasheet land pattern | `pads: [{number, at: [x, y], size: [w, h], shape, drill}]` |

Pin 1 is top left and numbering runs anticlockwise (down the left, along the bottom, up the right, back along the top), as JEDEC packages do. Check this against the datasheet's pinout: some parts number differently.

Land patterns use IPC-7351B nominal fillets (toe, heel, side): gull-wing 0.35/0.35/0.03, no-lead 0.40/0.00/−0.04 (pads never narrower than the terminal), chips 0.35/0.00/0.00. Pads are trimmed to leave 0.2 mm between neighbours for a solder-mask web, and the script warns when an exposed pad comes closer than that to the terminal pads. The courtyard is 0.25 mm around everything, the silkscreen outline stays 0.2 mm off copper, and a pin-1 triangle sits outside pad 1.

## Checking a generated part

1. Read the printed pad table against the datasheet's dimensions drawing.
2. Open `lib/previews/<name>-footprint.png` and `-symbol.png` and look: pad count and pitch, pin-1 position, exposed pad, silkscreen clear of pads, symbol names legible.
3. Build the schematic and board as usual; ERC and DRC then check the part in context.

A part used on a real board deserves a 1:1 printout of the footprint with the physical part laid on it, before ordering. Say so to the user for anything fine-pitch.
