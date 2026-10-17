"""Where KiCad, its libraries, Java and Freerouting live, and how to run them.

Every script imports this. Paths come from environment variables first, then
from `~/.pcb-anatomy/env.json` (written by setup.sh), then from the usual
install locations on Linux, macOS and Windows.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("KICAD_NO_ASSERT", "1")

HOME_STATE = Path(os.environ.get("PCB_ANATOMY_HOME", Path.home() / ".pcb-anatomy"))
SKILL_ROOT = Path(__file__).resolve().parents[2]


def _saved() -> dict:
    path = HOME_STATE / "env.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _first(paths) -> str | None:
    for path in paths:
        if path and Path(path).exists():
            return str(path)
    return None


def kicad_cli() -> str:
    found = os.environ.get("KICAD_CLI") or _saved().get("kicad_cli") or shutil.which("kicad-cli")
    if not found:
        found = _first([
            "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
            r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
            r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
        ])
    if not found:
        sys.exit("kicad-cli not found. Run scripts/kicad/setup.sh (or install KiCad 9 or 10) first.")
    return found


def kicad_version() -> tuple[int, int, int]:
    out = subprocess.run([kicad_cli(), "version"], capture_output=True, text=True).stdout.strip().splitlines()
    text = out[-1] if out else "0.0.0"
    parts = [int(p) for p in text.split("-")[0].split(".")[:3] if p.isdigit()]
    return tuple((parts + [0, 0, 0])[:3])


def share_dir(kind: str) -> Path:
    """`kind` is symbols, footprints or 3dmodels."""
    for var in (f"KICAD10_{kind.upper().rstrip('S')}_DIR", f"KICAD10_{kind.upper()}_DIR", f"KICAD9_{kind.upper().rstrip('S')}_DIR"):
        if os.environ.get(var):
            return Path(os.environ[var])
    saved = _saved().get(f"{kind}_dir")
    if saved:
        return Path(saved)
    found = _first([
        f"/usr/share/kicad/{kind}",
        f"/usr/local/share/kicad/{kind}",
        f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/{kind}",
        rf"C:\Program Files\KiCad\10.0\share\kicad\{kind}",
        rf"C:\Program Files\KiCad\9.0\share\kicad\{kind}",
    ])
    if not found:
        sys.exit(f"KiCad {kind} library not found; set KICAD10_{kind.upper()}_DIR or run setup.sh.")
    return Path(found)


def java() -> str | None:
    found = os.environ.get("PCB_ANATOMY_JAVA") or _saved().get("java")
    if found:
        return found
    for candidate in sorted(Path("/usr/lib/jvm").glob("java-2[5-9]*/bin/java"), reverse=True) if Path("/usr/lib/jvm").exists() else []:
        return str(candidate)
    return shutil.which("java")


def freerouting_jar() -> str | None:
    found = os.environ.get("FREEROUTING_JAR") or _saved().get("freerouting_jar")
    if found and Path(found).exists():
        return found
    jars = sorted(HOME_STATE.glob("freerouting-*-executable.jar"))
    return str(jars[-1]) if jars else None


def chrome() -> str | None:
    return os.environ.get("CHROME_PATH") or _saved().get("chrome")


def run_cli(*args, check: bool = True, quiet: bool = True) -> subprocess.CompletedProcess:
    """Run kicad-cli with the given arguments; asserts from wx are filtered out."""
    proc = subprocess.run([kicad_cli(), *map(str, args)], capture_output=True, text=True)
    noise = lambda line: "assert" in line or "PROPERTY_ENUM" in line
    proc.stdout = "\n".join(l for l in proc.stdout.splitlines() if not noise(l))
    proc.stderr = "\n".join(l for l in proc.stderr.splitlines() if not noise(l))
    if not quiet:
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
    if check and proc.returncode not in (0, 5):
        sys.exit(f"kicad-cli {' '.join(map(str, args))} failed ({proc.returncode}):\n{proc.stderr or proc.stdout}")
    return proc


def pcbnew():
    """Import KiCad's Python module, finding it on macOS and Windows too."""
    try:
        import pcbnew as module  # noqa: F401
        return module
    except ImportError:
        pass
    extra = [
        "/usr/lib/python3/dist-packages",
        "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/lib/python3.11/site-packages",
        r"C:\Program Files\KiCad\10.0\bin\Lib\site-packages",
    ]
    for path in extra:
        if Path(path).exists() and path not in sys.path:
            sys.path.append(path)
    try:
        import pcbnew as module
        return module
    except ImportError:
        sys.exit(
            "KiCad's pcbnew Python module is not importable from this interpreter. On macOS and Windows run the "
            "script with KiCad's bundled Python (see references/setup.md)."
        )


def is_windows() -> bool:
    return platform.system() == "Windows"


@contextlib.contextmanager
def quiet_stderr():
    """Silence C-level stderr (SWIG's leak notices) for the duration of the block."""
    sys.stderr.flush()
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)
