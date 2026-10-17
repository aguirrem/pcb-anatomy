# Setup

`bash scripts/kicad/setup.sh` installs what is missing and writes `~/.pcb-anatomy/env.json`; `--check` only reports; `--3d` adds KiCad's 3D models (several GB, used only by `render.py`'s 3D views). Every script reads `env.json`, then environment variables (`KICAD_CLI`, `KICAD10_SYMBOL_DIR`, `KICAD10_FOOTPRINT_DIR`, `PCB_ANATOMY_JAVA`, `FREEROUTING_JAR`, `CHROME_PATH`) override it.

| Piece | Needed for | Version |
|---|---|---|
| KiCad with `kicad-cli` and its Python module `pcbnew` | design, review, real-board figures | 9 or 10 (8 lacks parts of the CLI used here) |
| KiCad symbol and footprint libraries, and the global library tables | design | match KiCad |
| PyYAML, librsvg (`rsvg-convert`) | specs in YAML; renders | any |
| Java and Freerouting | `route.py` only | Java 25 for Freerouting 2.5 |
| Node.js | figures | 20.10+ |
| Chrome or Chromium (headless shell is enough) | figure checks | any recent |

## Linux

Debian and Ubuntu: `setup.sh` adds the official KiCad 10 PPA and installs `kicad kicad-symbols kicad-footprints`. Behind a proxy `add-apt-repository` often fails (it needs Launchpad's API and a keyserver); the script then reads the PPA's key fingerprint from Launchpad, fetches the key from keyserver.ubuntu.com and writes the apt source itself. Distribution packages without the PPA are often KiCad 7: too old. Other distributions: install KiCad 10 from https://www.kicad.org/download/linux/ (the Flatpak works; point `KICAD_CLI` at `flatpak run --command=kicad-cli org.kicad.KiCad` via a small wrapper script, and run Python scripts inside the Flatpak for `pcbnew`).

The global library tables: KiCad's GUI writes `~/.config/kicad/<version>/sym-lib-table` and `fp-lib-table` on first run. A headless machine never runs the GUI, so ERC and DRC warn about every library; `setup.sh` copies KiCad's templates into place.

## macOS

`brew install --cask kicad`, `brew install librsvg openjdk`. KiCad's `pcbnew` module lives inside the app bundle and only works with KiCad's own Python. Run the Python scripts with it (the path below is the usual one; check it with `ls /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/`):

```bash
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 scripts/kicad/pcb_build.py spec.yaml
```

and install PyYAML into that interpreter once (`... -m pip install pyyaml`). The CLI-only scripts (`sch_build.py`, `find_part.py`, `render.py` for schematics) work with any Python 3.10+.

## Windows

`winget install KiCad.KiCad`, `winget install EclipseAdoptium.Temurin.25.JRE`, Node.js from nodejs.org. Run the scripts with KiCad's bundled Python (`C:\Program Files\KiCad\10.0\bin\python.exe`), which has `pcbnew`; run `setup.sh` from Git Bash or set the environment variables above by hand.

## The Claude app and other sandboxes

The skill runs in a sandboxed Linux container there; `setup.sh` handles it, with three things learned the hard way:

- **Network allowlists.** Package registries and GitHub usually work; other sites may not. The KiCad PPA (`ppa.launchpadcontent.net`) and Maven Central were reachable in testing; GitHub release downloads for Freerouting were not, which is why the script takes Freerouting from Maven Central and its mirrors (checksum-verified).
- **Maven Central rate limits** busy shared IPs (HTTP 429); the script falls back to `repo.maven.apache.org` and Google's mirror.
- **Chrome** is rarely present; the script installs Chrome's headless shell with `npx @puppeteer/browsers` into `~/.pcb-anatomy/chrome`.

Installing KiCad takes a few minutes and about 1 GB without 3D models. When it cannot be installed (no root, no network), only Illustrate mode for invented boards works: say so, and offer to run the design or review steps on the user's own machine, or to draw a real board from a `board.board.json` they create there with `board_dump.py`.
