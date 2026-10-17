"""The circuit spec: one YAML (or JSON) file that the schematic, the board and
the review are all built from. See references/spec.md for the format.

    project: blinky
    parts:
      U1: {symbol: "Timer:NE555P", footprint: "Package_DIP:DIP-8_W7.62mm", value: NE555}
      R1: {symbol: "Device:R", footprint: "Resistor_SMD:R_0603_1608Metric", value: 1k}
    nets:
      VCC: [U1.VCC, U1.R, R1.1]
      GND: [U1.GND]
    nc: [U1.CV]

A pin reference is REF.PIN, where PIN is a pin number ("U1.8") or a pin name
("U1.VCC"). A name shared by several pins (an IC's GND pins) selects all of them.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import fplib
import libsym


@dataclass
class Part:
    ref: str
    symbol: str
    footprint: str
    value: str
    group: str = "main"
    fields: dict = field(default_factory=dict)
    dnp: bool = False
    node: list | None = None
    pins: list = field(default_factory=list)


@dataclass
class Spec:
    path: Path
    project: str
    title: str
    rev: str
    company: str
    parts: dict[str, Part]
    nets: dict[str, list[tuple[str, str]]]      # net -> [(ref, pin number)]
    nc: list[tuple[str, str]]
    raw: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def project_dir(self) -> Path:
        return self.path.parent

    def pin_net(self) -> dict[tuple[str, str], str]:
        return {node: net for net, nodes in self.nets.items() for node in nodes}


def _read(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            sys.exit("PyYAML is needed for .yaml specs: pip install pyyaml (or write the spec as .json)")
        return yaml.safe_load(text)
    return json.loads(text)


def _clean(name: str) -> str:
    return re.sub(r"[~{}]", "", name).strip().upper()


def _resolve_pin(part: Part, pin: str) -> list[str]:
    numbers = [p.number for p in part.pins]
    if pin in numbers:
        return [pin]
    matches = sorted({p.number for p in part.pins if _clean(p.name) == _clean(pin)}, key=lambda n: (len(n), n))
    if matches:
        return matches
    alternates = sorted({p.number for p in part.pins if _clean(pin) in _clean(p.name).split("/")})
    if len(alternates) == 1:
        return alternates
    names = sorted({p.name for p in part.pins if p.name not in ("~", "")})
    raise SystemExit(f"{part.ref} ({part.symbol}) has no pin {pin!r}. Numbers: {', '.join(numbers)}; names: {', '.join(names)}")


def load(path: str | Path, check_footprints: bool = True) -> Spec:
    path = Path(path).resolve()
    raw = _read(path)
    project = str(raw.get("project") or path.stem)
    parts: dict[str, Part] = {}
    for ref, info in (raw.get("parts") or {}).items():
        info = info or {}
        if "symbol" not in info:
            sys.exit(f"part {ref} has no symbol")
        part = Part(
            ref=str(ref), symbol=str(info["symbol"]), footprint=str(info.get("footprint", "")),
            value=str(info.get("value", info["symbol"].split(":")[-1])), group=str(info.get("group", "main")),
            fields={str(k): str(v) for k, v in (info.get("fields") or {}).items()}, dnp=bool(info.get("dnp", False)),
        )
        part.node = libsym.load(part.symbol, path.parent)
        part.pins = libsym.pins(part.node)
        if check_footprints and part.footprint:
            fplib.resolve(part.footprint, path.parent)
        parts[part.ref] = part

    nets: dict[str, list[tuple[str, str]]] = {}
    seen: dict[tuple[str, str], str] = {}
    for net, members in (raw.get("nets") or {}).items():
        nodes = []
        for member in members:
            ref, _, pin = str(member).partition(".")
            if ref not in parts:
                sys.exit(f"net {net}: unknown part {ref!r} in {member!r}")
            for number in _resolve_pin(parts[ref], pin):
                key = (ref, number)
                if key in seen and seen[key] != net:
                    sys.exit(f"{ref}.{number} is on two nets: {seen[key]} and {net}")
                seen[key] = str(net)
                if key not in nodes:
                    nodes.append(key)
        nets[str(net)] = nodes

    nc = []
    for member in raw.get("nc") or []:
        ref, _, pin = str(member).partition(".")
        if ref not in parts:
            sys.exit(f"nc: unknown part {ref!r}")
        for number in _resolve_pin(parts[ref], pin):
            nc.append((ref, number))

    spec = Spec(path=path, project=project, title=str(raw.get("title", project)), rev=str(raw.get("rev", "A")),
                company=str(raw.get("company", "")), parts=parts, nets=nets, nc=nc, raw=raw)
    _validate(spec)
    return spec


def _validate(spec: Spec) -> None:
    connected = set(spec.pin_net())
    problems = []
    for net, nodes in spec.nets.items():
        if len(nodes) < 2 and not (spec.raw.get("allow_single") or []).count(net):
            problems.append(f"net {net} has only one pin {nodes}: add its other end, list it under `nc`, or under `allow_single`")
    auto_nc = bool(spec.raw.get("auto_nc", False))
    for part in spec.parts.values():
        drawn = {}
        for pin in part.pins:
            if pin.number in drawn:
                continue
            drawn[pin.number] = pin
        for number, pin in drawn.items():
            key = (part.ref, number)
            if key in connected or key in spec.nc:
                continue
            if pin.etype == "no_connect":
                spec.nc.append(key)
            elif auto_nc:
                spec.nc.append(key)
                spec.warnings.append(f"{part.ref}.{number} ({pin.name}) left unconnected (auto_nc)")
            else:
                problems.append(f"{part.ref}.{number} ({pin.name}, {pin.etype}) is on no net and not in `nc`")
    if part_without_fp := [p.ref for p in spec.parts.values() if not p.footprint and not p.symbol.startswith("power:")]:
        spec.warnings.append(f"no footprint for {', '.join(part_without_fp)} (schematic only)")
    if problems:
        sys.exit("spec problems:\n  " + "\n  ".join(problems))
