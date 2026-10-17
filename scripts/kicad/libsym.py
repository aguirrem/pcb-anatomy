"""Read symbols from KiCad libraries: flatten derived symbols, list pins with
their schematic geometry, and give each unit's bounding box.

A symbol id is "Library:Name". Libraries are searched in the project's own
`lib/` folder (and its sym-lib-table) first, then in KiCad's shared symbols.
The shared libraries are large (over 200 MB), so lookups scan raw text and
parse only the one symbol block they need, and searches use an index cached
in ~/.pcb-anatomy/symbol-index.json.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

import kienv
import sexp
from sexp import Sym

_TOP = re.compile(r'\n\t\(symbol "((?:[^"\\]|\\.)*)"\n')
_DESC = re.compile(r'\(property "Description" "((?:[^"\\]|\\.)*)"')
_KEYS = re.compile(r'\(property "ki_keywords" "((?:[^"\\]|\\.)*)"')
_TEXT: dict[str, str] = {}


@dataclass
class Pin:
    number: str
    name: str
    etype: str          # input, output, bidirectional, power_in, power_out, passive, no_connect, ...
    unit: int           # 0 means common to every unit
    x: float            # connection point in symbol coordinates (y up)
    y: float
    angle: int          # direction from the connection point toward the body, degrees
    length: float
    hidden: bool

    @property
    def outward(self) -> tuple[int, int]:
        """Unit vector pointing away from the body, in schematic coordinates (y down)."""
        return {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[self.angle % 360]


def _library_files(lib: str, project_dir: Path | None) -> list[Path]:
    candidates = []
    if project_dir:
        table = project_dir / "sym-lib-table"
        if table.exists():
            for entry in sexp.find_all(sexp.load(str(table)), "lib"):
                if sexp.value(entry, "name") == lib:
                    uri = str(sexp.value(entry, "uri")).replace("${KIPRJMOD}", str(project_dir))
                    candidates.append(Path(uri))
        candidates += [project_dir / "lib" / f"{lib}.kicad_sym", project_dir / f"{lib}.kicad_sym"]
    shared = kienv.share_dir("symbols")
    candidates += [shared / f"{lib}.kicad_sym", shared / f"{lib}.kicad_symdir"]
    return [c for c in candidates if c.exists()]


def _text_of(path: Path) -> list[str]:
    files = sorted(path.glob("*.kicad_sym")) if path.is_dir() else [path]
    out = []
    for file in files:
        key = str(file)
        if key not in _TEXT:
            _TEXT[key] = file.read_text(encoding="utf-8")
        out.append(_TEXT[key])
    return out


def _blocks(text: str):
    """(name, block text) for every top-level symbol in a library's text."""
    for m in _TOP.finditer(text):
        end = text.find("\n\t)\n", m.end())
        yield m.group(1), text[m.start() + 1:end + 3]


def _lookup(lib: str, name: str, project_dir: Path | None):
    needle = f'\n\t(symbol "{name}"\n'
    for path in _library_files(lib, project_dir):
        for text in _text_of(path):
            at = text.find(needle)
            if at >= 0:
                end = text.find("\n\t)\n", at + len(needle))
                return sexp.loads(text[at + 1:end + 3]), path
    return None, None


def library_names(project_dir: Path | None = None) -> list[str]:
    shared = kienv.share_dir("symbols")
    names = [p.name.split(".")[0] for p in shared.iterdir() if p.suffix in (".kicad_sym", ".kicad_symdir")]
    if project_dir and (project_dir / "lib").exists():
        names += [p.stem for p in (project_dir / "lib").glob("*.kicad_sym")]
    return sorted(set(names))


def _index(project_dir: Path | None) -> list[tuple[str, str, str, str]]:
    """(lib, name, description, keywords) for every symbol; the shared part is cached on disk."""
    shared = kienv.share_dir("symbols")
    cache = kienv.HOME_STATE / "symbol-index.json"
    stamp = f"{shared}:{max((p.stat().st_mtime for p in shared.iterdir()), default=0)}"
    rows = None
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("stamp") == stamp:
                rows = [tuple(r) for r in data["rows"]]
        except (json.JSONDecodeError, KeyError):
            rows = None
    if rows is None:
        rows = []
        for path in sorted(shared.iterdir()):
            if path.suffix not in (".kicad_sym", ".kicad_symdir"):
                continue
            lib = path.name.split(".")[0]
            for text in _text_of(path):
                for name, block in _blocks(text):
                    d, k = _DESC.search(block), _KEYS.search(block)
                    rows.append((lib, name, d.group(1) if d else "", k.group(1) if k else ""))
            _TEXT.clear()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stamp": stamp, "rows": rows}))
    if project_dir and (project_dir / "lib").exists():
        for path in (project_dir / "lib").glob("*.kicad_sym"):
            for name, block in _blocks(path.read_text(encoding="utf-8")):
                d, k = _DESC.search(block), _KEYS.search(block)
                rows.append((path.stem, name, d.group(1) if d else "", k.group(1) if k else ""))
    return rows


def search(pattern: str, project_dir: Path | None = None, limit: int = 40) -> list[str]:
    """Symbol ids whose name, description or keywords match a regex."""
    rx = re.compile(pattern, re.I)
    hits = []
    for lib, name, desc, keys in _index(project_dir):
        if rx.search(name) or rx.search(desc) or rx.search(keys):
            hits.append(f"{lib}:{name}  {desc[:80]}")
            if len(hits) >= limit:
                break
    return hits


def load(symbol_id: str, project_dir: Path | None = None) -> list:
    """The symbol node, flattened (no `extends`), named "Lib:Name" for a schematic's lib_symbols."""
    lib, _, name = symbol_id.partition(":")
    if not name:
        raise SystemExit(f"symbol id {symbol_id!r} must be Library:Name")
    node, _ = _lookup(lib, name, project_dir)
    if node is None:
        raise SystemExit(f"symbol {symbol_id} not found (search with: python3 scripts/kicad/find_part.py sym <regex>)")
    node = copy.deepcopy(node)
    parent_name = sexp.value(node, "extends")
    if parent_name:
        parent = load(f"{lib}:{parent_name}", project_dir)
        own = {p[1]: p for p in sexp.find_all(node, "property")}
        flat = [Sym("symbol"), name]
        for child in parent[2:]:
            if isinstance(child, list) and str(child[0]) == "property" and child[1] in own:
                flat.append(own.pop(child[1]))
            elif isinstance(child, list) and str(child[0]) == "symbol":
                child[1] = re.sub(rf"^{re.escape(str(parent_name))}_", f"{name}_", str(child[1]))
                flat.append(child)
            else:
                flat.append(child)
        first_unit = next(i for i, c in enumerate(flat) if isinstance(c, list) and str(c[0]) == "symbol")
        flat[first_unit:first_unit] = list(own.values())
        node = flat
    node[1] = f"{lib}:{name}"
    return node


def _unit_of(sub_name: str) -> tuple[int, int]:
    m = re.search(r"_(\d+)_(\d+)$", str(sub_name))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def pins(node: list) -> list[Pin]:
    out = []
    for sub in sexp.find_all(node, "symbol"):
        unit, style = _unit_of(sub[1])
        if style not in (0, 1):
            continue
        for p in sexp.find_all(sub, "pin"):
            at = sexp.find(p, "at")
            name_node = sexp.find(p, "name")
            number_node = sexp.find(p, "number")
            hidden = any(isinstance(c, Sym) and c == "hide" for c in p) or str(sexp.value(p, "hide", "no")) == "yes"
            out.append(Pin(
                number=str(number_node[1]) if number_node else "",
                name=str(name_node[1]) if name_node else "",
                etype=str(p[1]), unit=unit,
                x=float(at[1]), y=float(at[2]), angle=int(at[3]) if len(at) > 3 else 0,
                length=float(sexp.value(p, "length", 2.54)), hidden=bool(hidden),
            ))
    return out


def unit_count(node: list) -> int:
    units = {_unit_of(sub[1])[0] for sub in sexp.find_all(node, "symbol")}
    return max(units | {1})


def is_power_symbol(node: list) -> bool:
    return sexp.find(node, "power") is not None


def bbox(node: list, unit: int) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) in schematic coordinates (y down) around the origin, pins included."""
    xs, ys = [], []
    for sub in sexp.find_all(node, "symbol"):
        u, style = _unit_of(sub[1])
        if u not in (0, unit) or style not in (0, 1):
            continue
        for n in sexp.walk(sub):
            head = str(n[0])
            if head == "pin":
                continue
            if head in ("xy", "start", "end", "center", "mid") and len(n) >= 3 and isinstance(n[1], (int, float)):
                xs.append(float(n[1])); ys.append(-float(n[2]))
            elif head == "circle":
                c = sexp.find(n, "center"); r = float(sexp.value(n, "radius", 0))
                if c:
                    xs += [float(c[1]) - r, float(c[1]) + r]; ys += [-float(c[2]) - r, -float(c[2]) + r]
        for p in pins([Sym("symbol"), "x", sub]):
            xs.append(p.x); ys.append(-p.y)
            dx, dy = {0: (1, 0), 180: (-1, 0), 90: (0, 1), 270: (0, -1)}[p.angle % 360]
            xs.append(p.x + dx * p.length); ys.append(-(p.y + dy * p.length))
    if not xs:
        return (-2.54, -2.54, 2.54, 2.54)
    return (min(xs), min(ys), max(xs), max(ys))
