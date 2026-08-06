#!/usr/bin/env bash
#
# minty installer
#
# Copies the copy of minty this script lives next to onto your system and wires
# it up so it can be your normal terminal.
#
# Usage:
#   bash install.sh                 # install
#   bash install.sh --uninstall     # remove again
#   curl -fsSL https://raw.githubusercontent.com/davethecoderr/minty/main/install.sh | bash
#
# Safe to re-run (idempotent). Only needs python3 + a terminal emulator.
# opencode is bundled if present, otherwise minty auto-installs it on first run.
# If run via curl|bash, files are fetched from the minty GitHub repo instead.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd 2>/dev/null || echo /dev/null)"
INSTALL_DIR="${MINTY_INSTALL_DIR:-$HOME/.local/share/minty}"
MINTY_REPO="${MINTY_REPO:-davethecoderr/minty}"
MINTY_REF="${MINTY_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/$MINTY_REPO/$MINTY_REF"
FILES=(minty.py minty-icon.svg)
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
CONFIG_KITTY="${KITTY_CONFIG_DIRECTORY:-$HOME/.config/kitty}/kitty.conf"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
info()  { echo "${GREEN}==>${RESET} $*"; }
warn()  { echo "${YELLOW}==>${RESET} $*"; }
error() { echo "${RED}==>${RESET} $*" >&2; }

uninstall() {
  info "Removing minty from $INSTALL_DIR"
  rm -rf "$INSTALL_DIR"
  rm -f "$BIN_DIR/minty"
  rm -f "$APPS_DIR/minty.desktop"
  if [ -f "$CONFIG_KITTY" ]; then
    sed -i '/minty/d' "$CONFIG_KITTY"
  fi
  info "minty uninstalled."
  exit 0
}

if [ "${1:-}" = "--uninstall" ]; then uninstall; fi

if ! command -v python3 >/dev/null 2>&1; then
  error "python3 is required but not found. Install it, then re-run this script."
  exit 1
fi

info "Installing minty to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APPS_DIR"

for f in "${FILES[@]}"; do
  if [ -f "$SOURCE_DIR/$f" ]; then
    install -m 755 "$SOURCE_DIR/$f" "$INSTALL_DIR/"
  elif command -v curl >/dev/null 2>&1; then
    info "Fetching $f from $MINTY_REPO..."
    curl -fsSL -o "$INSTALL_DIR/$f" "$RAW_BASE/$f"
    chmod 755 "$INSTALL_DIR/$f"
  else
    error "missing file next to installer: $SOURCE_DIR/$f (and no curl to fetch it from $RAW_BASE/$f)"
    exit 1
  fi
done

install -m 755 "$0" "$INSTALL_DIR/install.sh" 2>/dev/null || true

for old in mini_terminal.py minty_menu.py minty_theme.py minty_pkg.py minty_hist.py minty_tmux.py minty_vm.py minty_svc.py minty_proc.py minty_net.py minty.sh; do
  rm -f "$INSTALL_DIR/$old"
done

if [ -f "$SOURCE_DIR/opencode" ]; then
  info "Bundling opencode ($(du -h "$SOURCE_DIR/opencode" | cut -f1))..."
  install -m 755 "$SOURCE_DIR/opencode" "$INSTALL_DIR/opencode"
else
  warn "No bundled opencode in $SOURCE_DIR — minty will auto-install it on first run."
fi

info "Creating 'minty' command in $BIN_DIR"
cat > "$BIN_DIR/minty" <<EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/minty.py" "\$@"
EOF
chmod +x "$BIN_DIR/minty"

info "Installing desktop launcher and icon"
mkdir -p "$ICONS_DIR"
install -m 644 "$INSTALL_DIR/minty-icon.svg" "$ICONS_DIR/minty.svg"
cat > "$APPS_DIR/minty.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=minty terminal
GenericName=Terminal
Comment=Minty - a tiny shell terminal with OpenCode AI built in
Exec=$BIN_DIR/minty terminal
Terminal=false
Icon=minty
Categories=System;TerminalEmulator;Utility;
EOF

case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) warn "'$BIN_DIR' is not on your PATH yet. Add it to your shell rc, or launch minty from its desktop entry." ;;
esac

if command -v kitty >/dev/null 2>&1; then
  if [ -f "$CONFIG_KITTY" ] && grep -qE '^shell .*minty\.(sh|py)' "$CONFIG_KITTY"; then
    sed -i -E "s@^shell .*minty\.(sh|py).*@shell $INSTALL_DIR/minty.py@" "$CONFIG_KITTY"
    info "Updated kitty to open minty as its shell ($CONFIG_KITTY)"
  else
    mkdir -p "$(dirname "$CONFIG_KITTY")"
    touch "$CONFIG_KITTY"
    printf '\n# minty: open minty as the terminal shell (exit minty to reach your real shell)\nshell %s/minty.py\n' "$INSTALL_DIR" >> "$CONFIG_KITTY"
    info "Configured kitty to open minty as its shell ($CONFIG_KITTY)"
  fi
else
  warn "kitty not found. minty will run in whatever terminal you open it from — use the desktop entry or the 'minty' command."
fi

info "Verifying install..."
python3 -m py_compile "$INSTALL_DIR/minty.py"
if [ -x "$INSTALL_DIR/opencode" ]; then
  info "Bundled opencode is ready."
fi
info "Done!"
echo "  - Open a NEW terminal window -> it should be minty."
echo "  - Or run: $BIN_DIR/minty"
echo "  - 'minty terminal' opens minty in its own terminal window."
echo "  - Side menu with OpenCode: press Ctrl+T inside minty."
  echo "  - To remove: bash $INSTALL_DIR/install.sh --uninstall"
