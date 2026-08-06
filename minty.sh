#!/usr/bin/env bash
# minty terminal wrapper - launches minty, then falls through to your real shell.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$here/mini_terminal.py"
exec "$SHELL" -l
