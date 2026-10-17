// The physics the figure shows, in one pure module the build and the page both use.
//
// A decoupling capacitor and an IC's supply pins form a loop: out along the supply trace,
// through the capacitor, back along the ground trace. Two parallel flat traces of width w
// and copper thickness t, centre to centre d apart and l long, have an inductance of about
//   L = (mu0 / pi) * l * ln(pi * d / (w + t))          (valid for d >> w)
// mu0 / pi is 0.4 nH per mm. The capacitor's own mounted inductance (ESL) adds in series,
// about 0.5 nH for an 0603. What the IC sees at frequency f is |Z| = 2 * pi * f * L above
// the capacitor's self-resonance, which is why distance matters more than capacitance.

export const TRACE = { w: 0.3, t: 0.035, d: 3.81 };   // mm: track width, 1 oz copper, pin 8 to pin 5 on an SOIC-8
export const ESL = 0.5;                                // nH, a mounted 0603 capacitor
export const F_TEST = 100e6;                           // Hz

export function loopOf(l, { w, t, d } = TRACE) {
  const perMm = 0.4 * Math.log((Math.PI * d) / (w + t));
  const traces = perMm * l;
  const L = traces + ESL;
  const z = 2 * Math.PI * F_TEST * L * 1e-9;
  return { l, area: l * d, perMm, L, z };
}

export const describe = (s) =>
  `${s.l.toFixed(1)} mm · ${s.area.toFixed(0)} mm² · ${s.L.toFixed(1)} nH · ${s.z.toFixed(1)} Ω at 100 MHz`;
