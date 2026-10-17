# Design review

`python3 scripts/kicad/review.py PROJECT [--fab jlcpcb] [--currents NET=amps,...]` works on any KiCad 8–10 project with a `.kicad_sch` and/or `.kicad_pcb`. It writes `PROJECT/review/`: `review.md`, `review.json`, and `review-top.png`, the top side with every located finding ringed and numbered (red blocker, orange major, blue minor).

The script is the first pass. A review is finished only when you have done the second pass below and written the report.

## Severity

| Severity | Means | Examples |
|---|---|---|
| **blocker** | will not work or cannot be built as is | unrouted nets, shorts, a symbol pin with no pad, a track or drill under the fab minimum |
| **major** | likely failure, respin or failed assembly | an IC supply pin with no capacitor or one >10 mm away, a floating CMOS input, a power track too thin for its current, a high-speed pair mismatched by >2.5 mm, schematic/board mismatch |
| **minor** | worth fixing, cheap now | caps 4–10 mm from their pin, parts within 1 mm of the edge, references off the silkscreen, missing MPNs, a footprint's own clearance below the fab rule |
| **info** | facts and measurements | board size, narrowest track, current capacity per rail, pair lengths |

Never describe a board as ready to order while a blocker or major is open.

## What the script checks

- **ERC** (all severities) and **DRC with schematic parity**. DRC findings whose items all belong to one footprint are reported once as "inside footprint": the fix is to confirm with the fab or edit the footprint, not to move tracks.
- **Fab limits** against the preset (`jlcpcb`, `pcbway`, `oshpark`, `generic`): narrowest track, smallest via drill and annular ring.
- **Decoupling:** every IC pin typed `power_in` must share its net with a capacitor whose other pin is on ground; distance from the pin to the nearest such capacitor pad (≤4 mm fine, 4–10 minor, >10 major, none major).
- **Via in pad:** any via inside an SMD pad (it wicks solder at reflow unless the fab fills and caps it), major.
- **Floating inputs:** pins typed `input` on unconnected or single-pin nets.
- **Current:** narrowest track on each power net against the current given with `--currents` (IPC-2221 outer layer, 1 oz, 10 °C rise), plus via counts. Without `--currents` it reports capacity only; ask the user for the real currents. Ground nets are not checked this way: their current returns through pours and many paths, so give currents for the supply nets.
- **Differential pairs:** nets named `D+/D-`, `DP/DM`, `*_P/*_N` and length mismatch.
- **Crystals:** routed length of each crystal net (>10 mm minor).
- **Mechanical and assembly:** parts closer than 1 mm to the edge (connectors and holes excepted), mounting holes present, fiducials when fine-pitch parts are on the board.
- **Silkscreen:** references hidden or moved to the fab layer.
- **BOM:** parts with no `MPN` or `LCSC` field (passives reported apart), symbol pins with no matching footprint pad.

## The second pass (yours)

1. **Look at `review-top.png`** and confirm every ringed finding. Look at the renders (`render.py PROJECT`) for what no check catches: odd routing, parts facing the wrong way, a connector on the wrong edge.
2. **Datasheets of the main ICs:**
   - reference circuit: capacitor values and voltage ratings (ceramic capacitance drops with DC bias), inductor saturation current, feedback divider giving the right output, enable/UVLO pins not floating;
   - required pull-ups and pull-downs (I²C, reset, boot-mode straps, open-drain outputs), and their values for the bus speed;
   - layout notes: exposed pad connected and stitched to ground, thermal vias, keep-outs under antennas, Kelvin sense lines, switching loop areas.
3. **Power:** input protection (reverse polarity, ESD and TVS on external connectors, fuses where a short is possible), regulator dissipation (`(Vin − Vout) × I` for an LDO; is the package able to shed it?), bulk capacitance at the input.
4. **Interfaces:** USB-C CC resistors (5.1 kΩ to ground for a sink), USB ESD at the connector, series resistors on fast edges, connector pinouts against the mating cable.
5. **Manufacturability:** the parts exist and are in stock at the assembler, footprints match the chosen MPN's package exactly, polarity marks on diodes, LEDs and electrolytics, test points for programming and power rails.

## The report

Lead with a one-line verdict (ready, ready after the listed fixes, or not ready). Then findings by severity, each with where it is (reference, pin, coordinates or the ring number on `review-top.png`), why it matters in one sentence, and the concrete fix. End with what was not checked: the script's "Not checked" list plus anything you could not verify (a datasheet you could not open, currents the user did not give). Offer to apply the fixes when the project was built from a spec.
