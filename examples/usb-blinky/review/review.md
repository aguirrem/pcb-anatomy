# Design review: usb-blinky

Fab rules: **jlcpcb**. Findings: 0 blocker, 0 major, 3 minor, 7 info

Located findings are ringed on `review-top.png` with their number.

## Minor

1. **DRC (inside footprint)**: J1: Hole clearance violation (board setup constraints hole clearance 0.2000 mm; actual 0.1944 mm) (x4); comes from the footprint itself, so confirm the fab can build it or edit the footprint (at 19.2, 7.5 mm)
2. **bom**: no MPN or LCSC number for D1, J2, SW1
3. **silkscreen**: 2 references not on the silkscreen: D1, R1 (assembly drawing needed)

## Info

4. **board**: 32.1 x 24.1 mm, 2 copper layers, 1.60 mm thick, 17 footprints
5. **bom**: 7 passives without part numbers (value and footprint may be enough for the assembler)
6. **current**: +3V3: narrowest track 0.50 mm carries about 1.45 A (1 oz outer, 10 °C rise), 2 vias; needs 0.3 A
7. **current**: VBUS: narrowest track 0.38 mm carries about 1.17 A (1 oz outer, 10 °C rise), 2 vias; needs 1.0 A
8. **decoupling**: U2 pin 8 (VCC_8): C3 at 2.1 mm
9. **decoupling**: U1 pin 1 (VIN_1): C1 at 2.3 mm
10. **fab**: narrowest track 0.187 mm (fab minimum 0.127 mm)

## Not checked

- signal integrity beyond pair length
- thermal dissipation
- EMC
- datasheet-specific layout rules
- component availability and lifecycle
- mechanical fit with the enclosure
