#!/usr/bin/env bash
# Install or check everything pcb-anatomy uses, then record where it all is in
# ~/.pcb-anatomy/env.json for the scripts to find.
#
#   bash scripts/kicad/setup.sh            install what is missing (asks sudo on Linux)
#   bash scripts/kicad/setup.sh --check    report only; changes nothing
#   bash scripts/kicad/setup.sh --3d       also install KiCad's 3D models (several GB; only for render.py's 3D views)
#
# What it sets up, and what needs it:
#   KiCad 10 (or 9) with kicad-cli and its Python module   design and review modes
#   KiCad's global symbol and footprint tables              ERC and DRC without library warnings
#   librsvg (rsvg-convert), PyYAML                          renders, YAML specs
#   Java 25 and Freerouting                                 route.py (autorouting) only
#   headless Chrome                                         figure checks (capture, lines) only
#   Node.js 20.10+                                          figures; never installed here, only checked
set -u
CHECK=0; WITH3D=0
for arg in "$@"; do case "$arg" in --check) CHECK=1 ;; --3d) WITH3D=1 ;; esac; done
STATE="${PCB_ANATOMY_HOME:-$HOME/.pcb-anatomy}"
mkdir -p "$STATE"
FR_VERSION="2.5.0"
say() { printf '%s\n' "$*"; }
ok() { printf '  ok      %s\n' "$*"; }
miss() { printf '  missing %s\n' "$*"; }
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
OS="$(uname -s)"

kicad_major() { "$1" version 2>/dev/null | tail -1 | cut -d. -f1; }
find_kicad_cli() {
  for c in "${KICAD_CLI:-}" "$(command -v kicad-cli 2>/dev/null)" /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli \
           "/c/Program Files/KiCad/10.0/bin/kicad-cli.exe" "/c/Program Files/KiCad/9.0/bin/kicad-cli.exe"; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return; }
  done
}

install_kicad_linux() {
  command -v apt-get >/dev/null || { say "  Not a Debian/Ubuntu system: install KiCad 10 from https://www.kicad.org/download/linux/ (Flatpak works), then rerun."; return 1; }
  . /etc/os-release
  local list=/etc/apt/sources.list.d/kicad-10.list
  if ! apt-cache policy kicad 2>/dev/null | grep -q "Candidate: 1[0-9]\.\|Candidate: 9\."; then
    say "  adding the KiCad 10 PPA for ${VERSION_CODENAME:-noble}"
    if ! $SUDO add-apt-repository -y ppa:kicad/kicad-10.0-releases >/dev/null 2>&1; then
      # add-apt-repository needs Launchpad's API and a keyserver round trip that proxies often block: do it by hand
      local fp
      fp=$(curl -fsS https://api.launchpad.net/1.0/~kicad/+archive/ubuntu/kicad-10.0-releases | python3 -c 'import sys,json;print(json.load(sys.stdin)["signing_key_fingerprint"])') || return 1
      curl -fsS "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x$fp" | $SUDO gpg --dearmor -o /etc/apt/trusted.gpg.d/kicad-ppa.gpg || return 1
      echo "deb https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu ${VERSION_CODENAME:-noble} main" | $SUDO tee "$list" >/dev/null
    fi
    $SUDO apt-get update -qq
  fi
  local pkgs="kicad kicad-symbols kicad-footprints"
  [ "$WITH3D" -eq 1 ] && pkgs="$pkgs kicad-packages3d"
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends $pkgs
}

say "pcb-anatomy setup ($OS)"

# --- KiCad -------------------------------------------------------------------------------------
CLI="$(find_kicad_cli)"
MAJOR=""; [ -n "$CLI" ] && MAJOR="$(kicad_major "$CLI")"
if [ -n "$CLI" ] && [ "${MAJOR:-0}" -ge 9 ]; then
  ok "KiCad $("$CLI" version | tail -1) ($CLI)"
else
  [ -n "$CLI" ] && miss "KiCad 9 or newer (found $("$CLI" version | tail -1), which has no CLI ERC/DRC)" || miss "KiCad"
  if [ "$CHECK" -eq 0 ]; then
    case "$OS" in
      Linux) install_kicad_linux || say "  KiCad install failed; see references/setup.md" ;;
      Darwin) command -v brew >/dev/null && brew install --cask kicad || say "  Install KiCad from https://www.kicad.org/download/macos/" ;;
      *) say "  Windows: winget install KiCad.KiCad   (then rerun this from Git Bash)" ;;
    esac
    CLI="$(find_kicad_cli)"; [ -n "$CLI" ] && MAJOR="$(kicad_major "$CLI")"
  fi
fi

SHARE=""
for d in "${KICAD10_SYMBOL_DIR:-}/.." /usr/share/kicad /usr/local/share/kicad /Applications/KiCad/KiCad.app/Contents/SharedSupport "/c/Program Files/KiCad/10.0/share/kicad"; do
  [ -d "$d/symbols" ] && { SHARE="$(cd "$d" && pwd)"; break; }
done
[ -n "$SHARE" ] && ok "libraries in $SHARE" || miss "KiCad symbol and footprint libraries"
if [ -n "$SHARE" ]; then
  if [ -d "$SHARE/3dmodels" ] && [ -n "$(ls -A "$SHARE/3dmodels" 2>/dev/null)" ]; then ok "3D models"
  else printf '  note    no 3D models: render.py 3D views show bare pads (optional; setup.sh --3d installs them, several GB)\n'; fi
fi

# KiCad's global library tables: the GUI writes them on first run; headless machines never get them
if [ -n "$MAJOR" ] && [ -n "$SHARE" ]; then
  case "$OS" in Darwin) CFG="$HOME/Library/Preferences/kicad/$MAJOR.0" ;; *) CFG="${XDG_CONFIG_HOME:-$HOME/.config}/kicad/$MAJOR.0" ;; esac
  for t in sym-lib-table fp-lib-table; do
    if [ -f "$CFG/$t" ]; then ok "global $t"; elif [ "$CHECK" -eq 0 ] && [ -f "$SHARE/template/$t" ]; then
      mkdir -p "$CFG" && cp "$SHARE/template/$t" "$CFG/$t" && ok "global $t (installed from KiCad's template)"
    else miss "global $t (ERC/DRC will warn about every library)"; fi
  done
fi

PY_OK=0
python3 -c "import pcbnew" 2>/dev/null && { ok "KiCad Python module (pcbnew)"; PY_OK=1; } || miss "pcbnew Python module for python3 (on macOS/Windows run the scripts with KiCad's bundled Python; references/setup.md)"
python3 -c "import yaml" 2>/dev/null && ok "PyYAML" || { miss "PyYAML"; [ "$CHECK" -eq 0 ] && { $SUDO apt-get install -y python3-yaml >/dev/null 2>&1 || pip3 install --user pyyaml >/dev/null 2>&1 || pip3 install --break-system-packages pyyaml >/dev/null 2>&1; }; }
command -v rsvg-convert >/dev/null && ok "rsvg-convert" || { miss "rsvg-convert (librsvg)"; [ "$CHECK" -eq 0 ] && { case "$OS" in Linux) $SUDO apt-get install -y librsvg2-bin >/dev/null 2>&1 ;; Darwin) brew install librsvg >/dev/null 2>&1 ;; esac; }; }

# --- Java 25 and Freerouting --------------------------------------------------------------------
JAVA=""
for j in "${PCB_ANATOMY_JAVA:-}" /usr/lib/jvm/java-2[5-9]*/bin/java /opt/homebrew/opt/openjdk/bin/java /usr/local/opt/openjdk/bin/java "$(command -v java 2>/dev/null)"; do
  [ -n "$j" ] && [ -x "$j" ] || continue
  v=$("$j" -version 2>&1 | grep -o 'version "[0-9]*' | grep -o '[0-9]*$')
  [ "${v:-0}" -ge 25 ] && { JAVA="$j"; break; }
done
if [ -n "$JAVA" ]; then ok "Java 25+ ($JAVA)"; else
  miss "Java 25 (Freerouting 2.5 needs it)"
  if [ "$CHECK" -eq 0 ]; then
    case "$OS" in
      Linux) $SUDO apt-get install -y --no-install-recommends openjdk-25-jre-headless >/dev/null 2>&1 ;;
      Darwin) brew install openjdk >/dev/null 2>&1 ;;
      *) say "  Windows: winget install EclipseAdoptium.Temurin.25.JRE" ;;
    esac
    for j in /usr/lib/jvm/java-2[5-9]*/bin/java /opt/homebrew/opt/openjdk/bin/java /usr/local/opt/openjdk/bin/java; do [ -x "$j" ] && { JAVA="$j"; break; }; done
  fi
fi

JAR="$STATE/freerouting-$FR_VERSION-executable.jar"
if [ -f "$JAR" ]; then ok "Freerouting $FR_VERSION"; else
  miss "Freerouting"
  if [ "$CHECK" -eq 0 ]; then
    # Maven Central rate-limits busy IPs; its mirrors serve the same signed files
    for m in https://repo.maven.apache.org/maven2 https://maven-central.storage-download.googleapis.com/maven2 https://repo1.maven.org/maven2; do
      base="$m/app/freerouting/freerouting/$FR_VERSION/freerouting-$FR_VERSION-executable.jar"
      if curl -fsSL -o "$JAR.part" "$base" && want=$(curl -fsSL "$base.sha1") && [ "$(sha1sum "$JAR.part" 2>/dev/null | cut -d' ' -f1 || shasum "$JAR.part" | cut -d' ' -f1)" = "$want" ]; then
        mv "$JAR.part" "$JAR"; ok "Freerouting $FR_VERSION (checksum verified, from $m)"; break
      fi
      rm -f "$JAR.part"
    done
    [ -f "$JAR" ] || miss "Freerouting download failed: fetch freerouting-$FR_VERSION-executable.jar by hand into $STATE"
  fi
fi

# --- headless Chrome and Node --------------------------------------------------------------------
CHROME="${CHROME_PATH:-}"
if [ -z "$CHROME" ]; then
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "$(command -v google-chrome 2>/dev/null)" "$(command -v chromium 2>/dev/null)" \
           $(ls -d "$STATE"/chrome/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell 2>/dev/null | tail -1); do
    [ -n "$c" ] && [ -x "$c" ] && { CHROME="$c"; break; }
  done
fi
if command -v node >/dev/null; then ok "Node $(node --version)"; else miss "Node.js 20.10+ (figures need it: https://nodejs.org)"; fi
if [ -n "$CHROME" ]; then ok "Chrome ($CHROME)"; else
  miss "Chrome or Chromium (only the figure checks need it)"
  if [ "$CHECK" -eq 0 ] && command -v npx >/dev/null; then
    (cd "$STATE" && npx -y @puppeteer/browsers install chrome-headless-shell@stable --path "$STATE/chrome" >/dev/null 2>&1)
    CHROME=$(ls -d "$STATE"/chrome/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell 2>/dev/null | tail -1)
    [ -n "$CHROME" ] && ok "headless Chrome ($CHROME)" || miss "headless Chrome download failed; set CHROME_PATH to any Chrome"
  fi
fi

# --- record ----------------------------------------------------------------------------------
python3 - "$STATE/env.json" "${CLI:-}" "${SHARE:-}" "${JAVA:-}" "$( [ -f "$JAR" ] && echo "$JAR")" "${CHROME:-}" <<'PY'
import json, sys
path, cli, share, java, jar, chrome = sys.argv[1:7]
data = {"kicad_cli": cli or None, "symbols_dir": f"{share}/symbols" if share else None,
        "footprints_dir": f"{share}/footprints" if share else None, "3dmodels_dir": f"{share}/3dmodels" if share else None,
        "java": java or None, "freerouting_jar": jar or None, "chrome": chrome or None}
json.dump({k: v for k, v in data.items() if v}, open(path, "w"), indent=2)
print(f"wrote {path}")
PY
[ -n "$CLI" ] && [ "${MAJOR:-0}" -ge 9 ] && [ "$PY_OK" -eq 1 ] && say "ready: design and review modes" || say "not ready for design/review: fix the missing items above"
