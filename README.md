# minty

minty is its own terminal — a single file that brings the shell **and** a
real GTK3/VTE terminal emulator, so it doesn't need kitty or any other
terminal. It also packs visual themes, a package manager, a tmux session
manager, a VM manager, a systemd service manager, a process manager, a
network/wifi manager and an fzf-style history picker.

## Requirements

- Python 3
- For the built-in minty terminal: GTK3 + VTE 2.91 + python-gobject.
  Install per distro:
  - Arch/CachyOS/Manjaro: `sudo pacman -S vte3 python-gobject`
  - Debian/Ubuntu/Mint: `sudo apt install python3-gi gir1.2-vte-2.91`
  - Fedora: `sudo dnf install vte291 python3-gobject`
  - openSUSE: `sudo zypper install vte python3-gobject`
- The minty shell itself works in any terminal, no GTK needed.
- Package manager auto-detected: paru/yay, pacman, apt, dnf or zypper.
- OpenCode AI is bundled automatically on first run if not present

## Install

### One-line install (from this repo)

```bash
curl -fsSL https://raw.githubusercontent.com/davethecoderr/minty/main/install.sh | bash
```

### Local install

Clone (or copy) the repo, then:

```bash
bash install.sh
```

Both methods copy `minty.py` into `~/.local/share/minty`, create a `minty`
command in `~/.local/bin` and add a desktop launcher. `minty` opens its own
terminal window — no kitty required.

## Use

- Launch the `minty terminal` desktop app, or run `minty terminal` in any
  terminal — it opens its own GTK3/VTE window with background, foreground,
  palette and font all matched to your active theme (add `background` and
  `foreground` to a theme's colors, or `font`/`font_size` to its settings).
  Real terminal features: tabs (Ctrl+Shift+T, Ctrl+PageUp/Down, Ctrl+1..9),
  split panes (Ctrl+Shift+E down / Ctrl+Shift+O right, Ctrl+Alt+arrows to
  navigate, Ctrl+Shift+F to focus the next pane), font zoom (Ctrl +/-/0),
  copy/paste (Ctrl+Shift+C/V), a right-click context menu, and clickable
  links (Ctrl+click or context menu)
- Run `minty` in an existing terminal to start the minty shell there
- First run shows a quick guided tour; `tour` replays it any time
- `settings` — visual editor for the terminal (font size, colors, window
  size, scrollback, notifications); `settings get/set <key> <value>` from
  the shell
- `learn` — built-in code guide: how to create/edit files, git, python,
  pipes, permissions, network and more (`minty learn git` filters it)
- `open <file>` — open files/folders with your default app
- `clip <text>` — copy text to the clipboard
- `help` — list every built-in command
- `theme` — browse, apply, edit and share themes
- `oc` (alias for `opencode`) — start the OpenCode AI assistant; add
  `--new` to open it in a fresh terminal window in the current folder
  (also available as a menu item under `Ctrl+T`)
- `pkg` — search / install / remove packages and update your system
  (pacman/yay/paru, apt, dnf and zypper are all supported)
- `tmux`, `vms`, `svc`, `proc`, `net` — visual managers for each
- `hist` or `Ctrl+R` — browse command history
- `Ctrl+T` — side menu
- `exit` — leave minty and fall through to your real shell

## Update

```bash
minty update --source davethecoderr/minty
```

## Uninstall

```bash
bash ~/.local/share/minty/install.sh --uninstall
```

## Credits

AI helped a lot building this. 🧠💚

