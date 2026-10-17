"""Find footprints in KiCad's libraries and in a project's own `lib/*.pretty`."""

from __future__ import annotations

import re
from pathlib import Path

import kienv
import sexp


def library_dirs(project_dir: Path | None = None) -> dict[str, Path]:
    libs: dict[str, Path] = {}
    shared = kienv.share_dir("footprints")
    for path in shared.glob("*.pretty"):
        libs[path.stem] = path
    if project_dir:
        table = project_dir / "fp-lib-table"
        if table.exists():
            for entry in sexp.find_all(sexp.load(str(table)), "lib"):
                uri = str(sexp.value(entry, "uri")).replace("${KIPRJMOD}", str(project_dir))
                libs[str(sexp.value(entry, "name"))] = Path(uri)
        for path in (project_dir / "lib").glob("*.pretty") if (project_dir / "lib").exists() else []:
            libs[path.stem] = path
    return libs


def resolve(footprint_id: str, project_dir: Path | None = None) -> Path:
    """Path of the `.kicad_mod` for "Library:Name", or exit with a hint."""
    lib, _, name = footprint_id.partition(":")
    libs = library_dirs(project_dir)
    if lib not in libs:
        raise SystemExit(f"footprint library {lib!r} not found (footprint {footprint_id})")
    path = libs[lib] / f"{name}.kicad_mod"
    if not path.exists():
        raise SystemExit(f"footprint {footprint_id} not found (search with: python3 scripts/kicad/find_part.py fp <regex>)")
    return path


def search(pattern: str, project_dir: Path | None = None, limit: int = 40) -> list[str]:
    rx = re.compile(pattern, re.I)
    hits = []
    for lib, path in sorted(library_dirs(project_dir).items()):
        for mod in sorted(path.glob("*.kicad_mod")):
            fid = f"{lib}:{mod.stem}"
            if rx.search(fid):
                hits.append(fid)
                if len(hits) >= limit:
                    return hits
    return hits


def pad_numbers(footprint_id: str, project_dir: Path | None = None) -> list[str]:
    node = sexp.load(str(resolve(footprint_id, project_dir)))
    return [str(p[1]) for p in sexp.find_all(node, "pad") if str(p[1]) != ""]
