"""Minimal S-expression reader and writer for KiCad files.

A node is a Python list whose first element is a Sym (the head); atoms are
Sym (bare words), str (quoted strings), int or float. Quoting is preserved:
a quoted string reads back as str and writes back quoted.
"""

from __future__ import annotations

import re


class Sym(str):
    """A bare (unquoted) atom such as `pin`, `yes` or `passive`."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_NUMBER = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def loads(text: str):
    """Parse the first S-expression in `text`."""
    stack: list[list] = []
    root = None
    pos = 0
    length = len(text)
    while pos < length:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad token at {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        opened, closed, quoted, bare = m.groups()
        if opened:
            node: list = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif closed:
            node = stack.pop()
            if not stack:
                root = node
                break
        elif quoted is not None:
            stack[-1].append(_unescape(quoted))
        elif bare is not None:
            if _NUMBER.match(bare):
                stack[-1].append(float(bare) if any(c in bare for c in ".eE") else int(bare))
            else:
                stack[-1].append(Sym(bare))
    if root is None:
        raise ValueError("no complete S-expression found")
    return root


def load(path: str):
    with open(path, encoding="utf-8") as handle:
        return loads(handle.read())


def _atom(value) -> str:
    if isinstance(value, Sym):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


_INLINE = {"at", "xy", "size", "length", "width", "type", "hide", "uuid", "unit", "lib_id", "start", "end",
           "mid", "center", "radius", "offset", "layer", "layers", "justify", "color", "thickness", "net",
           "drill", "roundrect_rratio", "angle", "reference", "page", "version", "generator",
           "generator_version", "paper", "in_bom", "on_board", "dnp", "exclude_from_sim", "fields_autoplaced",
           "embedded_fonts", "bold", "italic", "tstamp", "path", "property_name", "rotate", "scale", "xyz",
           "solder_mask_margin", "clearance", "min_thickness", "filled_areas_thickness", "mode", "priority"}


def dumps(node, indent: int = 0) -> str:
    """Write a node in KiCad's tab-indented style."""
    if not isinstance(node, list):
        return _atom(node)
    head = node[0] if node else ""
    children = node[1:]
    if not any(isinstance(c, list) for c in children):
        return "(" + " ".join(_atom(x) for x in node) + ")"
    if str(head) == "pts":
        return "(pts\n" + "\t" * (indent + 1) + " ".join(dumps(c) for c in children) + "\n" + "\t" * indent + ")"
    if str(head) in _INLINE and all(not isinstance(c, list) or str(c[0]) in ("xyz",) for c in children):
        return "(" + " ".join(dumps(x) for x in node) + ")"
    parts = ["(" + _atom(head)]
    atoms = []
    for child in children:
        if isinstance(child, list):
            break
        atoms.append(_atom(child))
    if atoms:
        parts[0] += " " + " ".join(atoms)
    lines = [parts[0]]
    for child in children[len(atoms):]:
        lines.append("\t" * (indent + 1) + dumps(child, indent + 1))
    lines.append("\t" * indent + ")")
    return "\n".join(lines)


def dump(node, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(dumps(node) + "\n")


def find(node, head: str):
    """First direct child list whose head is `head`."""
    for child in node[1:]:
        if isinstance(child, list) and child and str(child[0]) == head:
            return child
    return None


def find_all(node, head: str):
    return [c for c in node[1:] if isinstance(c, list) and c and str(c[0]) == head]


def walk(node):
    """Every list node, depth first."""
    if isinstance(node, list):
        yield node
        for child in node[1:]:
            yield from walk(child)


def value(node, head: str, default=None):
    child = find(node, head)
    return child[1] if child is not None and len(child) > 1 else default


def prop(node, name: str, default=None):
    """The value of `(property "name" "value" ...)`."""
    for child in find_all(node, "property"):
        if len(child) > 2 and child[1] == name:
            return child[2]
    return default
